from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.roll.resolver import action_never_rolls
from app.domain.roll.schemas import RollResultBatch
from app.domain.views import (
    project_current_state,
    project_npc_profiles,
)

from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.director.schemas import DirectorStageResult
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_event.views import project_director_events

from .schemas import (
    ActionJudgment,
    ActionResolutionFrame,
    OutcomeBand,
    PlayerActionResolution,
    PlayerRollModifier,
    PlayerTurnResolution,
    RiskLevel,
    TurnResolutionResult,
)


def _project_recent_scene(scene: Any) -> str:
    if scene is None:
        return "null"
    return json.dumps(
        {
            "scene_id": getattr(scene, "scene_id", ""),
            "turn_index": getattr(scene, "turn_index", 0),
            "scene_text": getattr(scene, "scene_text", ""),
        },
        ensure_ascii=False,
        indent=2,
    )


class _ActionResolutionOutput(BaseModel):
    action_id: str
    effect_reasoning: str = Field(min_length=1, max_length=120)
    resolved_fact: str = Field(min_length=1, max_length=120)
    narrative_hook: str = Field(min_length=1, max_length=120)


class _ActionResolutionOutputSoft(BaseModel):
    action_id: str
    effect_reasoning: str = Field(min_length=1)
    resolved_fact: str = Field(min_length=1)
    narrative_hook: str = Field(min_length=1)


class _TurnResolutionOutput(BaseModel):
    resolution_reasoning: str = Field(min_length=1)
    action_resolutions: list[_ActionResolutionOutput] = Field(default_factory=list)


class _TurnResolutionOutputLoose(BaseModel):
    model_config = ConfigDict(extra="allow")

    resolution_reasoning: str | None = None
    action_resolutions: list[Any] = Field(default_factory=list)


@dataclass(frozen=True)
class TurnResolutionWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    reasoning: str = ""
    resolutions: list[_ActionResolutionOutputSoft] = field(default_factory=list)
    warnings: list[TurnResolutionWarning] = field(default_factory=list)


_RESOLUTION_ADAPTER = TypeAdapter(_ActionResolutionOutputSoft)

_HARD_LIMIT = 120
_ACCEPT_LIMIT = 150
_SOFT_LIMIT = 180
_FALLBACK_REASONING = "行动判定未生成详细说明，按确定性结果继续。"
_FALLBACK_EFFECT = "行动按判定推进，未生成详细效果说明。"
_FALLBACK_FACT = "未生成额外事实。"


def map_roll_to_band(value: int, sides: int) -> OutcomeBand:
    if sides <= 0:
        raise ValueError("dice sides must be positive")
    if value < 1 or value > sides:
        raise ValueError(f"roll value must be between 1 and {sides}")

    ratio = value / sides
    if ratio <= 0.10:
        return "critical_failure"
    if ratio <= 0.40:
        return "failure"
    if ratio <= 0.60:
        return "partial_success"
    if ratio <= 0.90:
        return "success"
    return "critical_success"


def infer_risk_level(action: AtomicAction, band: OutcomeBand) -> RiskLevel:
    if action.risk_level is not None:
        base = action.risk_level
    elif action.action_type in {"attack", "defend", "cast_or_use_power", "hide_or_stealth"}:
        base = "high"
    elif action.action_type in {"speak", "observe", "inspect_object", "wait"}:
        base = "low"
    else:
        base = "medium"

    if band in {"critical_failure", "failure"}:
        if base == "low":
            return "medium"
        if base == "medium":
            return "high"
    return base


def build_action_judgments(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
) -> list[ActionJudgment]:
    roll_by_id = {roll.action_id: roll for roll in roll_results.results}
    judgments: list[ActionJudgment] = []

    for action in parse_result.atomic_actions:
        roll = roll_by_id.get(action.action_id)
        if (
            roll is None
            and action.likely_needs_roll
            and not action_never_rolls(action)
        ):
            raise ValueError(
                f"Missing roll result for action requiring a roll: {action.action_id}"
            )
        band: OutcomeBand = (
            map_roll_to_band(roll.value, roll.dice_sides)
            if roll is not None
            else "success"
        )
        judgments.append(
            ActionJudgment(
                action_id=action.action_id,
                sequence=action.sequence,
                actor=action.actor,
                action_type=action.action_type,
                action_text=action.raw_text,
                resolution_source="roll" if roll is not None else "automatic",
                dice=roll.dice if roll is not None else None,
                roll_value=roll.value if roll is not None else None,
                raw_value=roll.raw_value if roll is not None else None,
                dice_sides=roll.dice_sides if roll is not None else None,
                outcome_band=band,
                risk_level=infer_risk_level(action, band),
            )
        )
    return judgments


def build_player_turn_resolution(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
    turn_resolution: TurnResolutionResult,
) -> PlayerTurnResolution:
    roll_by_id = {roll.action_id: roll for roll in roll_results.results}
    resolution_by_id = {
        resolution.action_id: resolution
        for resolution in turn_resolution.action_resolutions
    }
    actions: list[PlayerActionResolution] = []
    for action in sorted(parse_result.atomic_actions, key=lambda item: item.sequence):
        resolution = resolution_by_id.get(action.action_id)
        if resolution is None:
            continue
        roll = roll_by_id.get(action.action_id)
        actions.append(PlayerActionResolution(
            action_text=action.raw_text,
            resolution_source="roll" if roll is not None else "automatic",
            outcome_band=resolution.outcome_band,
            dice=roll.dice if roll is not None else None,
            raw_values=list(roll.all_raw_values) if roll is not None else [],
            modifiers=(
                [
                    PlayerRollModifier(
                        label=modifier.label,
                        reason=modifier.reason,
                        value=modifier.value,
                    )
                    for modifier in roll.modifiers
                ]
                if roll is not None
                else []
            ),
            modifier_total=(
                sum(modifier.value for modifier in roll.modifiers)
                if roll is not None
                else 0
            ),
            final_value=roll.value if roll is not None else None,
            dice_sides=roll.dice_sides if roll is not None else None,
        ))
    return PlayerTurnResolution(actions=actions)


def _parse_resolution(raw: Any) -> _ActionResolutionOutputSoft:
    if isinstance(raw, dict):
        cleaned = dict(raw)
        for key in ("action_id", "effect_reasoning", "resolved_fact", "narrative_hook"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = value.strip()
        raw = cleaned
    resolution = _RESOLUTION_ADAPTER.validate_python(raw)
    if not resolution.action_id.strip():
        raise ValueError("action_id must be non-blank")
    if not resolution.effect_reasoning.strip() or not resolution.resolved_fact.strip():
        raise ValueError("effect_reasoning and resolved_fact must be non-blank")
    return resolution


def _collect_record(
    output: _TurnResolutionOutputLoose,
    judgments: list[ActionJudgment],
    attempt: int,
) -> tuple[_AttemptRecord, list[str]]:
    judgment_by_id = {judgment.action_id: judgment for judgment in judgments}
    warnings: list[TurnResolutionWarning] = []
    retry_errors: list[str] = []

    reasoning = (output.resolution_reasoning or "").strip()

    if not isinstance(output.action_resolutions, list):
        return _AttemptRecord(
            reasoning=reasoning,
            warnings=warnings,
        ), ["action_resolutions must be a list"]

    seen: set[str] = set()
    resolutions: list[_ActionResolutionOutputSoft] = []
    for index, raw in enumerate(output.action_resolutions):
        try:
            resolution = _parse_resolution(raw)
        except (ValidationError, ValueError) as exc:
            retry_errors.append(f"action_resolutions[{index}]: {exc}")
            warnings.append(TurnResolutionWarning(
                code="turn_resolution_dropped",
                message=f"action_resolutions[{index}] dropped: {exc}",
            ))
            continue

        if resolution.action_id not in judgment_by_id:
            retry_errors.append(
                f"action_resolutions[{index}] unknown action_id: "
                f"{resolution.action_id}"
            )
            warnings.append(TurnResolutionWarning(
                code="turn_resolution_dropped",
                message=(
                    f"action_resolutions[{index}] dropped: unknown action_id "
                    f"{resolution.action_id}"
                ),
            ))
            continue

        if resolution.action_id in seen:
            retry_errors.append(
                f"action_resolutions[{index}] duplicate action_id: "
                f"{resolution.action_id}"
            )
            warnings.append(TurnResolutionWarning(
                code="turn_resolution_dropped",
                message=(
                    f"action_resolutions[{index}] dropped: duplicate action_id "
                    f"{resolution.action_id}"
                ),
            ))
            continue

        invalid = False
        for field_name in ("effect_reasoning", "resolved_fact", "narrative_hook"):
            value = getattr(resolution, field_name)
            if attempt == 1 and len(value) > _ACCEPT_LIMIT:
                # Accept-Limit buffer: values within (H, A] are accepted on
                # the first attempt; only > A triggers the single feedback
                # retry asking the model to return to the H target.
                retry_errors.append(
                    f"action_resolutions[{index}].{field_name} length "
                    f"{len(value)} exceeds accept limit {_ACCEPT_LIMIT}; "
                    f"return to the prompt target {_HARD_LIMIT}"
                )
                invalid = True
            elif attempt > 1 and len(value) > _SOFT_LIMIT:
                warnings.append(TurnResolutionWarning(
                    code="turn_resolution_truncated",
                    message=(
                        f"action_resolutions[{index}].{field_name} exceeded "
                        f"soft limit {_SOFT_LIMIT} and was truncated"
                    ),
                ))
                setattr(resolution, field_name, value[:_SOFT_LIMIT])
        if invalid:
            warnings.append(TurnResolutionWarning(
                code="turn_resolution_dropped",
                message=(
                    f"action_resolutions[{index}] dropped: exceeds accept "
                    f"limit {_ACCEPT_LIMIT}"
                ),
            ))
            continue

        seen.add(resolution.action_id)
        resolutions.append(resolution)

    missing = [
        judgment.action_id
        for judgment in judgments
        if judgment.action_id not in seen
    ]
    if missing:
        retry_errors.append(
            "TurnResolution missing action resolutions: "
            + ", ".join(missing)
        )

    return _AttemptRecord(
        reasoning=reasoning,
        resolutions=resolutions,
        warnings=warnings,
    ), retry_errors


def _fallback_frame(judgment: ActionJudgment) -> ActionResolutionFrame:
    return ActionResolutionFrame(
        action_id=judgment.action_id,
        sequence=judgment.sequence,
        actor=judgment.actor,
        action_type=judgment.action_type,
        outcome_band=judgment.outcome_band,
        resolution_source=judgment.resolution_source,
        effect_reasoning=_FALLBACK_EFFECT,
        resolved_fact=_FALLBACK_FACT,
        narrative_hook="",
    )


def _build_result(
    record: _AttemptRecord,
    judgments: list[ActionJudgment],
) -> TurnResolutionResult:
    by_id = {
        resolution.action_id: resolution
        for resolution in record.resolutions
    }
    frames: list[ActionResolutionFrame] = []
    for judgment in judgments:
        resolution = by_id.get(judgment.action_id)
        if resolution is None:
            frames.append(_fallback_frame(judgment))
            continue
        frames.append(
            ActionResolutionFrame(
                action_id=judgment.action_id,
                sequence=judgment.sequence,
                actor=judgment.actor,
                action_type=judgment.action_type,
                outcome_band=judgment.outcome_band,
                resolution_source=judgment.resolution_source,
                effect_reasoning=resolution.effect_reasoning,
                resolved_fact=resolution.resolved_fact,
                narrative_hook=resolution.narrative_hook,
            )
        )
    return TurnResolutionResult(
        resolution_reasoning=record.reasoning or _FALLBACK_REASONING,
        action_resolutions=frames,
    )


class TurnResolutionAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[TurnResolutionWarning] = []

    def resolve(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        action_parse_result: ActionParseResult,
        roll_results: RollResultBatch,
        core_memory: CoreMemory,
        context_facts: ContextFactSet | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        director_stage: DirectorStageResult | None = None,
        recent_scene: ShortTermScene | None = None,
        engaged_events: list[DirectorEvent] | None = None,
        turn_index: int | None = None,
        trace_id: str | None = None,
    ) -> TurnResolutionResult:
        self.last_warnings = []
        judgments = build_action_judgments(action_parse_result, roll_results)

        if not judgments:
            return TurnResolutionResult(
                resolution_reasoning="本回合没有可判定的原子行动。",
                action_resolutions=[],
            )

        schema = schema_json(_TurnResolutionOutput)
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: _TurnResolutionOutputLoose) -> None:
            record, retry_errors = _collect_record(
                output, judgments, current_attempt
            )
            attempt_records[current_attempt] = record
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        try:
            result = cast(
                _TurnResolutionOutputLoose,
                self.llm.run(
                    task="turn_resolution",
                    input_data={
                        "turn_index": turn_index if turn_index is not None else 1,
                        "action_judgments_json": json.dumps(
                            [judgment.model_dump(mode="json") for judgment in judgments],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "current_state_json": project_current_state(current_state),
                        "user_input": user_input,
                        "context_facts_json": project_context_facts(
                            context_facts or ContextFactSet(turn_focus="No Context Facts", facts=[])
                        ),
                        "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                        "director_stage_json": json.dumps(
                            (
                                director_stage.model_dump(mode="json")
                                if director_stage is not None
                                else None
                            ),
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "engaged_events_json": project_director_events(
                            engaged_events or [],
                            effective_stage="engaged",
                        ),
                        "recent_scene_json": _project_recent_scene(recent_scene),
                        "output_schema_json": schema,
                    },
                    output_schema=_TurnResolutionOutputLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    temperature=0.6,
                    trace_id=trace_id,
                ),
            )
            if current_attempt in attempt_records:
                record = attempt_records[current_attempt]
            else:
                # StubGateway compatibility.
                record, _ = _collect_record(
                    result, judgments, current_attempt or 1
                )
            self.last_warnings = list(record.warnings)
            return _build_result(record, judgments)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            return self._salvage(attempt_records, judgments, exc)

    def _salvage(
        self,
        attempt_records: dict[int, _AttemptRecord],
        judgments: list[ActionJudgment],
        exception: Exception,
    ) -> TurnResolutionResult:
        latest = attempt_records.get(2)
        if latest is None:
            latest = attempt_records.get(1)
        usable = attempt_records.get(2)
        if usable is None or not usable.resolutions:
            first = attempt_records.get(1)
            if first is not None and first.resolutions:
                usable = first
        if usable is None:
            usable = attempt_records.get(2) or attempt_records.get(1)

        if usable is not None and usable.resolutions:
            warnings = list(usable.warnings)
            if latest is not None and latest is not usable:
                for warning in latest.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            missing = [
                judgment.action_id
                for judgment in judgments
                if judgment.action_id not in {
                    resolution.action_id for resolution in usable.resolutions
                }
            ]
            if missing:
                warnings.append(TurnResolutionWarning(
                    code="turn_resolution_missing_filled",
                    message=(
                        "Turn resolution was incomplete; missing frames were "
                        f"filled deterministically: {', '.join(missing)}"
                    ),
                ))
            self.last_warnings = warnings
            return _build_result(usable, judgments)

        warnings: list[TurnResolutionWarning] = []
        for record in attempt_records.values():
            for warning in record.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        warnings.append(TurnResolutionWarning(
            code="turn_resolution_fallback",
            message=(
                "Turn resolution failed; deterministic fallback frames were "
                f"generated: {exception}"
            ),
        ))
        self.last_warnings = warnings
        return _build_result(_AttemptRecord(), judgments)
