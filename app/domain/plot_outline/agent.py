import json
from typing import cast

from pydantic import BaseModel, Field, model_validator

from app.core.llm import LLMGateway
from app.domain.director_event.views import project_director_event_consumptions
from app.domain.context_facts.views import project_context_facts
from app.domain.plot_outline.schemas import PlotOutlineContext, PlotOutlineResult
from app.domain.plot_outline.views import project_turn_resolution
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)


class _RuntimePlannedEvent(BaseModel):
    event_fact: str = Field(min_length=1)
    linked_outcome_ids: list[str] = Field(default_factory=list)
    involved_entities: list[str] = Field(default_factory=list)


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

        class RuntimePlotOutlineResult(BaseModel):
            director_npc_reason: str | None = None
            planning_reasoning: str = Field(min_length=1)
            planned_events: list[_RuntimePlannedEvent] = Field(default_factory=list)
            must_establish_facts: list[str] = Field(default_factory=list)
            must_not_establish_facts: list[str] = Field(default_factory=list)
            information_boundaries: list[str] = Field(default_factory=list)
            required_entities: list[str] = Field(default_factory=list)
            scene_stopping_point: str = Field(min_length=1)

            @model_validator(mode="after")
            def _require_all_turn_resolution_outcomes(self):
                linked_in_order: list[str] = []
                for event in self.planned_events:
                    for outcome_id in event.linked_outcome_ids:
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
                        f"planned_events reference unknown linked_outcome_ids: {unknown}"
                    )

                missing = [
                    outcome_id
                    for outcome_id in expected_outcome_ids
                    if outcome_id not in linked_ids
                ]
                if missing:
                    raise ValueError(
                        "all turn resolution outcomes must be enacted in planned_events; "
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
            RuntimePlotOutlineResult,
            self.llm.run(
                task="plot_outline",
                input_data={
                    "core_memory_json": context.core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(context.current_state),
                    "user_input": context.user_input,
                    "turn_resolution_json": project_turn_resolution(context.turn_resolution),
                    "context_facts_json": project_context_facts(context.context_facts),
                    "entity_profiles_json": project_entity_profiles(context.entity_profiles),
                    "npc_profiles_json": project_npc_profiles(context.npc_profiles),
                    "director_event_consumptions_json": project_director_event_consumptions(
                        context.director_event_consumptions,
                    ),
                    "director_npc_injection_json": json.dumps(
                        (
                            context.director_npc_injection.model_dump(mode="json")
                            if context.director_npc_injection is not None
                            else None
                        ),
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=RuntimePlotOutlineResult,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
        data = result.model_dump()
        data["planned_events"] = [
            {"event_id": f"e{index}", **event}
            for index, event in enumerate(data["planned_events"], start=1)
        ]
        return PlotOutlineResult.model_validate(data)
