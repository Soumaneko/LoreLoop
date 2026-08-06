import json
from typing import cast

from pydantic import BaseModel, Field

from app.core.llm import LLMGateway
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


class _ActionResolutionOutput(BaseModel):
    action_id: str
    effect_reasoning: str = Field(min_length=1, max_length=120)
    resolved_fact: str = Field(min_length=1, max_length=120)


class _TurnResolutionOutput(BaseModel):
    resolution_reasoning: str = Field(min_length=1)
    action_resolutions: list[_ActionResolutionOutput] = Field(default_factory=list)


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


class TurnResolutionAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

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
        trace_id: str | None = None,
    ) -> TurnResolutionResult:
        judgments = build_action_judgments(action_parse_result, roll_results)

        if not judgments:
            return TurnResolutionResult(
                resolution_reasoning="本回合没有可判定的原子行动。",
                action_resolutions=[],
            )

        schema = schema_json(_TurnResolutionOutput)

        decision = cast(
            _TurnResolutionOutput,
            self.llm.run(
                task="turn_resolution",
                input_data={
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
                    "output_schema_json": schema,
                },
                output_schema=_TurnResolutionOutput,
                temperature=0.6,
                trace_id=trace_id,
            ),
        )

        action_resolutions = _validate_action_resolutions(
            decision.action_resolutions,
            judgments,
        )
        return TurnResolutionResult(
            resolution_reasoning=decision.resolution_reasoning,
            action_resolutions=action_resolutions,
        )


def _validate_action_resolutions(
    resolutions: list[_ActionResolutionOutput],
    judgments: list[ActionJudgment],
) -> list[ActionResolutionFrame]:
    judgment_by_id = {judgment.action_id: judgment for judgment in judgments}
    seen: set[str] = set()
    cleaned: list[ActionResolutionFrame] = []

    for resolution in resolutions:
        judgment = judgment_by_id.get(resolution.action_id)
        if judgment is None or resolution.action_id in seen:
            continue
        cleaned.append(
            ActionResolutionFrame(
                action_id=judgment.action_id,
                sequence=judgment.sequence,
                actor=judgment.actor,
                action_type=judgment.action_type,
                outcome_band=judgment.outcome_band,
                resolution_source=judgment.resolution_source,
                effect_reasoning=resolution.effect_reasoning,
                resolved_fact=resolution.resolved_fact,
            )
        )
        seen.add(resolution.action_id)

    missing = [
        judgment
        for judgment in judgments
        if judgment.action_id not in seen
    ]
    if missing:
        missing_ids = ", ".join(judgment.action_id for judgment in missing)
        raise ValueError(f"TurnResolution missing action resolutions: {missing_ids}")
    return cleaned
