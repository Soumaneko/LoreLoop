import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_event.views import (
    project_director_event,
    turn_resolution_director_event_instructions,
)
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import RollResultBatch
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
    project_retrieved_memories,
    project_scene_summaries,
)

from .schemas import (
    NARRATIVE_TYPE_LABELS,
    ActionResolutionFrame,
    NarrativeType,
    OutcomeBand,
    RiskLevel,
    RollJudgment,
    TurnResolutionDecision,
    TurnResolutionResult,
)


ALL_NARRATIVE_TYPES: list[NarrativeType] = [
    "advance",
    "relationship_shift",
    "reveal_information",
    "escalate_pressure",
    "complication",
    "transition",
    "respite",
]


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


def roll_filter_types(
    judgments: list[RollJudgment],
    available_types: list[NarrativeType] | None = None,
) -> tuple[list[NarrativeType], list[str]]:
    current = list(available_types or ALL_NARRATIVE_TYPES)
    bands = {judgment.outcome_band for judgment in judgments}
    notes: list[str] = []

    has_critical_success = "critical_success" in bands
    has_critical_failure = "critical_failure" in bands
    success_like = bool(bands & {"success", "critical_success", "partial_success"})
    failure_like = bool(bands & {"failure", "critical_failure"})

    if has_critical_success and has_critical_failure:
        notes.append("critical_success and critical_failure both present; roll filter keeps all narrative types.")
        return current, notes

    if has_critical_failure:
        filtered = [t for t in current if t in {"escalate_pressure", "complication"}]
        if not filtered:
            filtered = ["advance"]
            notes.append("critical_failure left no available type; fallback to advance.")
        else:
            notes.append("critical_failure without critical_success allows only pressure or complication.")
        return filtered, notes

    if not failure_like:
        current = [t for t in current if t not in {"escalate_pressure", "complication"}]
        notes.append("no failure-like outcome; removed pressure and complication.")

    if not success_like:
        current = [t for t in current if t not in {"reveal_information", "transition"}]
        notes.append("no success-like outcome; removed information reveal and transition.")

    if not current:
        current = ["advance"]
        notes.append("roll filter left no available type; fallback to advance.")

    return current, notes


def cooldown_filter_types(
    available_types: list[NarrativeType],
    previous_types: list[NarrativeType],
) -> tuple[list[NarrativeType], list[str]]:
    current = list(available_types)
    previous_two = previous_types[-2:]
    notes: list[str] = []

    if previous_two:
        last_type = previous_two[-1]
        if last_type != "advance" and last_type in current:
            current = [t for t in current if t != last_type]
            notes.append(f"removed previous narrative type: {last_type}.")

    if len(previous_two) >= 2 and previous_two[-2:] == ["advance", "advance"]:
        current = [t for t in current if t != "advance"]
        notes.append("removed advance after two consecutive advance chapters.")

    if any(t in {"transition", "respite"} for t in previous_two):
        current = [t for t in current if t not in {"transition", "respite"}]
        notes.append("removed transition and respite due to shared cooldown.")

    if not current:
        current = ["advance"]
        notes.append("cooldown filter left no available type; fallback to advance.")

    return current, notes


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
        retrieved_memories: CandidateRetrievalResult,
        scene_summaries: list[SceneSummary],
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        director_event: DirectorEvent | None = None,
        recent_archived_director_event: DirectorEvent | None = None,
        previous_types: list[NarrativeType] | None = None,
        trace_id: str | None = None,
    ) -> TurnResolutionResult:
        previous_types = list(previous_types or [])
        judgments = build_roll_judgments(action_parse_result, roll_results)
        roll_available, roll_notes = roll_filter_types(judgments)
        final_available, cooldown_notes = cooldown_filter_types(roll_available, previous_types)

        if not judgments:
            return TurnResolutionResult(
                selected_type="advance",
                available_types=["advance"],
                previous_types=previous_types[-2:],
                roll_filter_notes=["no rolled actions; fallback to advance."],
                cooldown_filter_notes=[],
                action_resolutions=[],
            )

        schema = TurnResolutionDecision.model_json_schema()
        if "properties" in schema and "selected_type" in schema["properties"]:
            schema["properties"]["selected_type"]["enum"] = list(final_available)

        decision = cast(
            TurnResolutionDecision,
            self.llm.run(
                task="turn_resolution",
                input_data={
                    "available_types_json": json.dumps(
                        [
                            {"id": t, "label": NARRATIVE_TYPE_LABELS[t]}
                            for t in final_available
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "roll_judgments_json": json.dumps(
                        [judgment.model_dump(mode="json") for judgment in judgments],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "user_input": user_input,
                    "retrieved_memories_json": project_retrieved_memories(retrieved_memories),
                    "scene_summaries_json": project_scene_summaries(scene_summaries),
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
                output_schema=TurnResolutionDecision,
                temperature=0.6,
                trace_id=trace_id,
            ),
        )

        if decision.selected_type not in final_available:
            raise ValueError(f"TurnResolution selected unavailable type: {decision.selected_type}")

        action_resolutions = _validate_action_resolutions(
            decision.action_resolutions,
            judgments,
        )
        return TurnResolutionResult(
            selected_type=decision.selected_type,
            available_types=final_available,
            previous_types=previous_types[-2:],
            roll_filter_notes=roll_notes,
            cooldown_filter_notes=cooldown_notes,
            action_resolutions=action_resolutions,
        )


def _validate_action_resolutions(
    resolutions: list[ActionResolutionFrame],
    judgments: list[RollJudgment],
) -> list[ActionResolutionFrame]:
    judgment_by_id = {judgment.action_id: judgment for judgment in judgments}
    seen: set[str] = set()
    cleaned: list[ActionResolutionFrame] = []

    for resolution in resolutions:
        judgment = judgment_by_id.get(resolution.action_id)
        if judgment is None or resolution.action_id in seen:
            continue
        if resolution.outcome_band != judgment.outcome_band:
            resolution = resolution.model_copy(
                update={"outcome_band": judgment.outcome_band}
            )
        cleaned.append(resolution)
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
