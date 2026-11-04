"""Record and validate the active Narrative Composer against fixed cases."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
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
from app.core.llm.config import GatewayConfig, SharedGatewayFactory, create_gateway
from app.core.llm.prompts import PromptLoader
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


DEFAULT_FIXTURES = ROOT / "benchmarks" / "narrative_quality" / "cases" / "standard.json"
DEFAULT_RECORDINGS = (
    ROOT / "benchmarks" / "narrative_quality" / "local" / "contract-check" / "current.json"
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
    data = json.loads(path.read_text())
    adapter = TypeAdapter(list[NarrativeTestCase])
    if isinstance(data, dict) and "cases" in data:
        cases = []
        for reference in data["cases"]:
            source = json.loads((path.parent / reference["path"]).read_text())
            candidates = adapter.validate_python(source if isinstance(source, list) else [source])
            selected = [case for case in candidates if case.case_id == reference["case_id"]]
            if len(selected) != 1:
                raise ValueError(f"suite reference must select one case: {reference!r}")
            cases.extend(selected)
    else:
        cases = adapter.validate_python(data if isinstance(data, list) else [data])
    ids = [case.case_id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("fixture case_ids must be unique")
    return cases


def record_run(
    fixtures: list[NarrativeTestCase], config: GatewayConfig, run_dir: Path,
    *, prompts_dir: Path | None = None,
) -> list[dict[str, Any]]:
    # Each directory is an immutable run; a repeated path must never reuse stale output.
    run_dir.mkdir(parents=True, exist_ok=False)
    snapshot = run_dir / "prompts"
    shutil.copytree(prompts_dir or PromptLoader().prompts_dir, snapshot)
    fixture_text = json.dumps([case.model_dump(mode="json") for case in fixtures], ensure_ascii=False, indent=2)
    (run_dir / "fixtures.json").write_text(fixture_text)
    manifest = {
        "case_ids": [case.case_id for case in fixtures],
        "model": config.model, "protocol": config.protocol,
        "thinking": config.thinking, "reasoning_effort": config.reasoning_effort,
        "temperature": 0.8, "timeout_seconds": config.timeout,
        "request_profile": config.request_profile, "max_output_tokens": None,
        "fixtures_sha256": hashlib.sha256(fixture_text.encode()).hexdigest(),
        "prompt_sha256": {str(p.relative_to(snapshot)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in sorted(snapshot.rglob("*")) if p.is_file()},
        "code_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in [Path(__file__).resolve(),
                                  ROOT / "app/domain/narrative_composer/runner.py",
                                  ROOT / "app/domain/narrative_composer/schemas.py",
                                  ROOT / "app/domain/scene_writer/schemas.py"]},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    records = []
    for case in fixtures:
        folder = run_dir / case.case_id
        folder.mkdir()
        gateway = SharedGatewayFactory(
            config=config, prompts_dir=str(snapshot), log_path=str(folder / "traces.jsonl"),
        )()
        gateway.set_trace_context(turn_index=case.turn_index, session_type="narrative_benchmark")
        context = NarrativeComposerContext.model_validate(case.model_dump(mode="json"))
        started = time.perf_counter()
        print(f"{case.case_id}: started", flush=True)
        record: dict[str, Any] = {"case_id": case.case_id}
        try:
            result = run_narrative_composition(
                gateway, context=context, recent_scene=case.recent_scene,
                turn_index=case.turn_index, trace_id=new_hex_id(prefix="bench_narrative", length=12),
            )
            output = result.model_dump(mode="json")
            (folder / "composition.json").write_text(json.dumps(output, ensure_ascii=False, indent=2))
            (folder / "scene.md").write_text(result.scene_text)
            (folder / "scene-result.json").write_text(result.to_scene_result().model_dump_json(indent=2))
            record.update(success=True, scene_chars=len(result.scene_text),
                          npc_usages=output["npc_usages"],
                          invariant_check=check_invariants(case, output).model_dump())
        except Exception as exc:
            record.update(success=False, error=str(exc))
        trace = folder / "traces.jsonl"
        attempts = [json.loads(line) for line in trace.read_text().splitlines()] if trace.exists() else []
        record.update(latency_ms=round((time.perf_counter() - started) * 1000, 2),
                      attempts=len(attempts), errors=[c["error"] for c in attempts if c.get("error")])
        records.append(record)
        (folder / "result.json").write_text(json.dumps(record, ensure_ascii=False, indent=2))
        (run_dir / "results.json").write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(json.dumps(record, ensure_ascii=False), flush=True)
    return records


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
    parser.add_argument("--run-dir", type=Path, help="Create a new run directory; existing paths are rejected")
    parser.add_argument("--service", help="Saved model service config ID or preset ID, used with --run-dir")
    args = parser.parse_args()

    fixtures = load_fixtures(args.fixtures)
    if args.record:
        if args.run_dir:
            if args.service:
                from app.runtime.model_config import ModelConfigStore

                services = [c for c in ModelConfigStore().load_set().services
                            if args.service in {c.config_id, c.preset_id}]
                if len(services) != 1:
                    parser.error("--service must identify exactly one saved model service")
                config = services[0].gateway_config()
                if args.model and args.model != config.model:
                    parser.error("--model must match the saved service model")
            else:
                config = GatewayConfig.from_env(model=args.model)
            selected = [c for c in fixtures if not args.only or c.case_id == args.only]
            if not selected:
                parser.error("--only did not match any fixture")
            records = record_run(selected, config, args.run_dir)
            if any(not record["success"] for record in records):
                raise SystemExit("one or more benchmark calls failed; see results.json")
            return
        if args.service:
            parser.error("--service requires --run-dir")
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
