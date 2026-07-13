import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.llm import LLMGateway
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)

from .schemas import (
    DirectorEvent,
    DirectorEventConsumption,
)
from .views import project_director_events


class DirectorEventConsumer:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def consume(
        self,
        *,
        events: list[DirectorEvent],
        user_input: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        context_facts: ContextFactSet,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        trace_id: str | None = None,
    ) -> list[DirectorEventConsumption]:
        if not events:
            return []

        expected_ids = [event.event_id for event in events]

        class RuntimeDirectorEventConsumptionBatch(BaseModel):
            model_config = ConfigDict(extra="forbid")

            consumptions: list[DirectorEventConsumption] = Field(default_factory=list)

            @model_validator(mode="after")
            def _require_one_result_per_event(self):
                actual_ids = [item.event_id for item in self.consumptions]
                if len(actual_ids) != len(set(actual_ids)):
                    raise ValueError("DirectorEventConsumer returned duplicate event_id values")
                if set(actual_ids) != set(expected_ids):
                    raise ValueError(
                        "DirectorEventConsumer must return exactly one result per active event; "
                        f"expected {expected_ids}, got {actual_ids}"
                    )
                return self

        result = cast(
            RuntimeDirectorEventConsumptionBatch,
            self.llm.run(
                task="director_event_consume",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "user_input": user_input,
                    "context_facts_json": project_context_facts(context_facts),
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "director_events_json": project_director_events(events),
                    "output_schema_json": json.dumps(
                        RuntimeDirectorEventConsumptionBatch.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=RuntimeDirectorEventConsumptionBatch,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
        by_id = {item.event_id: item for item in result.consumptions}
        return [by_id[event_id] for event_id in expected_ids]
