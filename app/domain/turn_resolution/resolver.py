import json
from typing import cast

from pydantic import BaseModel, Field

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_event.views import (
    project_director_event,
    turn_resolution_director_event_instructions,
)
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.roll.schemas import RollResultBatch
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)

from .schemas import (
    ActionResolutionFrame,
    OutcomeBand,
    RiskLevel,
    RollJudgment,
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


def build_roll_judgments(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
) -> list[RollJudgment]:
    action_by_id = {
        action.action_id: action
        for action in parse_result.atomic_actions
    }
    judgments: list[RollJudgment] = []

    for roll in roll_results.results:
        action = action_by_id.get(roll.action_id)
        if action is None:
            continue
        band = map_roll_to_band(roll.value, roll.dice_sides)
        judgments.append(
            RollJudgment(
                action_id=roll.action_id,
                sequence=roll.sequence,
                actor=roll.actor,
                action_type=roll.action_type,
                action_text=action.raw_text,
                intended_goal=action.goal,
                dice=roll.dice,
                roll_value=roll.value,
                raw_value=roll.raw_value,
                dice_sides=roll.dice_sides,
                outcome_band=band,
                risk_level=infer_risk_level(action, band),
            )
        )
    return judgments


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
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        director_event: DirectorEvent | None = None,
        recent_archived_director_event: DirectorEvent | None = None,
        trace_id: str | None = None,
    ) -> TurnResolutionResult:
        judgments = build_roll_judgments(action_parse_result, roll_results)

        if not judgments:
            return TurnResolutionResult(
                resolution_reasoning="本回合没有需要掷骰解释的行动，回合判定回退为默认推进。",
                action_resolutions=[],
            )

        schema = _TurnResolutionOutput.model_json_schema()

        decision = cast(
            _TurnResolutionOutput,
            self.llm.run(
                task="turn_resolution",
                input_data={
                    "roll_judgments_json": json.dumps(
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
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "director_event_json": project_director_event(
                        director_event,
                        recent_archived_event=recent_archived_director_event,
                    ),
                    "director_event_instructions": turn_resolution_director_event_instructions(
                        director_event,
                        recent_archived_event=recent_archived_director_event,
                    ),
                    "output_schema_json": json.dumps(
                        schema,
                        ensure_ascii=False,
                        indent=2,
                    ),
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
    judgments: list[RollJudgment],
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
