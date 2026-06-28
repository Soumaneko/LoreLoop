import json
from typing import cast

from pydantic import model_validator

from app.core.llm import LLMGateway
from app.domain.director_event.views import (
    plot_outline_director_event_instructions,
    project_director_event,
)
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.plot_outline.views import (
    project_retrieved_memories,
    project_turn_resolution,
)
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_scene_summaries,
)
from app.workflows.context_assembler import PlotOutlineContext


class PlotOutlineAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        context: PlotOutlineContext,
        *,
        trace_id: str | None = None,
    ) -> PlotOutlineResult:
        expected_outcome_ids = [
            frame.action_id
            for frame in context.turn_resolution.action_resolutions
        ]

        class RuntimePlotOutlineResult(PlotOutlineResult):
            @model_validator(mode="after")
            def _require_all_turn_resolution_outcomes(self):
                linked_in_order: list[str] = []
                for beat in self.beats:
                    for outcome_id in beat.linked_outcome_ids:
                        if outcome_id not in linked_in_order:
                            linked_in_order.append(outcome_id)

                linked_ids = set(linked_in_order)
                expected_ids = set(expected_outcome_ids)
                unknown = [
                    outcome_id
                    for outcome_id in linked_in_order
                    if outcome_id not in expected_ids
                ]
                if unknown:
                    raise ValueError(
                        f"plot beats reference unknown linked_outcome_ids: {unknown}"
                    )

                missing = [
                    outcome_id
                    for outcome_id in expected_outcome_ids
                    if outcome_id not in linked_ids
                ]
                if missing:
                    raise ValueError(
                        "all turn resolution outcomes must be enacted in plot beats; "
                        f"missing linked_outcome_ids: {missing}"
                    )

                if linked_in_order != expected_outcome_ids:
                    raise ValueError(
                        "turn resolution outcomes must be enacted in order; "
                        f"expected {expected_outcome_ids}, got {linked_in_order}"
                    )
                return self

        schema = RuntimePlotOutlineResult.model_json_schema()

        result = cast(
            PlotOutlineResult,
            self.llm.run(
                task="plot_outline",
                input_data={
                    "core_memory_json": context.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(context.current_state),
                    "user_input": context.user_input,
                    "turn_resolution_json": project_turn_resolution(context.turn_resolution),
                    "retrieved_memories_json": project_retrieved_memories(context.retrieved_memories),
                    "scene_summaries_json": project_scene_summaries(context.scene_summaries),
                    "entity_profiles_json": project_entity_profiles(context.entity_profiles),
                    "director_event_json": project_director_event(
                        context.director_event,
                        recent_archived_event=context.recent_archived_director_event,
                    ),
                    "director_event_instructions": plot_outline_director_event_instructions(
                        context.director_event,
                        recent_archived_event=context.recent_archived_director_event,
                    ),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=RuntimePlotOutlineResult,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
        return PlotOutlineResult.model_validate(result.model_dump())
