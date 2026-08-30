"""Record and validate the active Narrative Composer against fixed cases."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.ids import new_hex_id
from app.core.llm import LLMGateway
from app.core.llm.config import create_gateway
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director.schemas import DirectorStageResult
from app.domain.director_event.schemas import DirectorEvent
from app.domain.narrative_composer.runner import run_narrative_composition
from app.domain.narrative_composer.schemas import (
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.action_judgment.schemas import ActionJudgment


DEFAULT_FIXTURES = ROOT / "benchmarks" / "narrative_quality" / "fixtures.json"
DEFAULT_RECORDINGS = (
    ROOT / "benchmarks" / "narrative_quality" / "recordings" / "current.json"
)


class NarrativeTestCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    turn_index: int = Field(ge=1)
    user_input: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    action_judgments: list[ActionJudgment] = Field(min_length=1)
    context_facts: ContextFactSet = Field(
        default_factory=lambda: ContextFactSet(turn_focus="No Context Facts", facts=[])
    )
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    director_stage: DirectorStageResult | None = None
    engaged_events: list[DirectorEvent] = Field(default_factory=list)
    recent_scene: ShortTermScene | None = None


class InvariantCheckResult(BaseModel):
    passed: bool
    details: list[str] = Field(default_factory=list)


class CaseRecording(BaseModel):
    case_id: str
    output: dict[str, Any]
    latency_ms: float
    invariant_check: InvariantCheckResult


def load_fixtures(path: Path = DEFAULT_FIXTURES) -> list[NarrativeTestCase]:
    return TypeAdapter(list[NarrativeTestCase]).validate_json(path.read_text())


def check_invariants(
    case: NarrativeTestCase,
    raw_output: dict[str, Any],
) -> InvariantCheckResult:
    try:
        result = NarrativeComposerResult.model_validate(raw_output)
    except Exception as exc:
        return InvariantCheckResult(passed=False, details=[f"invalid schema: {exc}"])

    expected = [(item.action_id, item.outcome_band) for item in case.action_judgments]
    actual = [(item.action_id, item.outcome_band) for item in result.action_resolutions]
    details = [] if actual == expected else [f"outcomes differ: {actual} != {expected}"]
    linked = [
        action_id
        for event in result.planned_events
        for action_id in event.linked_outcome_ids
    ]
    missing = [action_id for action_id, _ in expected if action_id not in linked]
    if missing:
        details.append(f"planned_events miss actions: {missing}")
    if not result.scene_text.strip() or not result.scene_stopping_point.strip():
        details.append("scene text or stopping point is blank")
    return InvariantCheckResult(passed=not details, details=details)


def run_recording(
    fixtures: list[NarrativeTestCase],
    gateway: LLMGateway,
    only_case_id: str | None = None,
) -> list[CaseRecording]:
    run_id = new_hex_id(prefix="bench_narrative", length=8)
    recordings = []
    for case in fixtures:
        if only_case_id and case.case_id != only_case_id:
            continue
        context = NarrativeComposerContext(
            user_input=case.user_input,
            current_state=case.current_state,
            action_judgments=case.action_judgments,
            core_memory=case.core_memory,
            context_facts=case.context_facts,
            npc_profiles=case.npc_profiles,
            director_stage=case.director_stage,
            engaged_events=case.engaged_events,
        )
        started = time.perf_counter()
        result = run_narrative_composition(
            gateway,
            context=context,
            recent_scene=case.recent_scene,
            turn_index=case.turn_index,
            trace_id=f"{run_id}-{case.case_id}",
        )
        output = result.model_dump(mode="json")
        recordings.append(
            CaseRecording(
                case_id=case.case_id,
                output=output,
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
                invariant_check=check_invariants(case, output),
            )
        )
    return recordings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--fixtures", type=Path, default=DEFAULT_FIXTURES)
    parser.add_argument("--recordings", type=Path, default=DEFAULT_RECORDINGS)
    parser.add_argument("--model")
    parser.add_argument("--only")
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures)
    if args.record:
        recordings = run_recording(fixtures, create_gateway(model=args.model), args.only)
        args.recordings.parent.mkdir(parents=True, exist_ok=True)
        args.recordings.write_text(
            json.dumps([item.model_dump(mode="json") for item in recordings], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return
    if args.check:
        recordings = TypeAdapter(list[CaseRecording]).validate_json(
            args.recordings.read_text()
        )
        case_by_id = {case.case_id: case for case in fixtures}
        failures = [
            item.case_id
            for item in recordings
            if item.case_id not in case_by_id
            or not check_invariants(case_by_id[item.case_id], item.output).passed
        ]
        if failures:
            raise SystemExit(f"benchmark invariant failures: {failures}")
        return
    parser.error("choose --record or --check")


if __name__ == "__main__":
    main()
