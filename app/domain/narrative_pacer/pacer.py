import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.narrative_pacer.schemas import (
    NARRATIVE_TYPE_LABELS,
    NarrativePacingDecision,
    NarrativePacingResult,
    NarrativeType,
)
from app.domain.outcome.schemas import OutcomeResolutionResult
from app.domain.scene_summary.schemas import SceneSummary


ALL_NARRATIVE_TYPES: list[NarrativeType] = [
    "advance",
    "relationship_shift",
    "reveal_information",
    "escalate_pressure",
    "complication",
    "transition",
    "respite",
]


def roll_filter_types(
    outcome_resolution: OutcomeResolutionResult,
    available_types: list[NarrativeType] | None = None,
) -> tuple[list[NarrativeType], list[str]]:
    current = list(available_types or ALL_NARRATIVE_TYPES)
    bands = {outcome.outcome_band for outcome in outcome_resolution.outcomes}
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


class NarrativePacer:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def pace(
        self,
        *,
        user_input: str,
        current_state: CurrentState,
        action_parse_result: ActionParseResult,
        outcome_resolution: OutcomeResolutionResult,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary],
        previous_types: list[NarrativeType] | None = None,
        trace_id: str | None = None,
    ) -> NarrativePacingResult:
        previous_types = list(previous_types or [])
        roll_available, roll_notes = roll_filter_types(outcome_resolution)
        final_available, cooldown_notes = cooldown_filter_types(roll_available, previous_types)

        decision = cast(
            NarrativePacingDecision,
            self.llm.run(
                task="narrative_pace",
                input_data={
                    "available_types_json": json.dumps(
                        [
                            {"id": t, "label": NARRATIVE_TYPE_LABELS[t]}
                            for t in final_available
                        ],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "previous_types_json": json.dumps(previous_types[-2:], ensure_ascii=False, indent=2),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": current_state.model_dump_json(indent=2),
                    "user_input": user_input,
                    "action_parse_result_json": action_parse_result.model_dump_json(indent=2),
                    "outcome_resolution_json": outcome_resolution.model_dump_json(indent=2),
                    "recent_summaries_json": json.dumps(
                        [s.model_dump(mode="json") for s in recent_summaries],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(
                        NarrativePacingDecision.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=NarrativePacingDecision,
                trace_id=trace_id,
            ),
        )

        if decision.selected_type not in final_available:
            raise ValueError(
                f"NarrativePacer selected unavailable type: {decision.selected_type}"
            )

        return NarrativePacingResult(
            available_types=final_available,
            selected_type=decision.selected_type,
            narrative_goal=decision.narrative_goal,
            previous_types=previous_types[-2:],
            roll_filter_notes=roll_notes,
            cooldown_filter_notes=cooldown_notes,
        )
