import json
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.llm import LLMGateway
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.domain.turn_resolution.views import project_turn_resolution
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
)

from .schemas import (
    DirectorEvent,
    DirectorEventConsumption,
)
from .views import project_director_event


class _DirectorEventConsumptionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    use_event: bool
    allowed_facts: list[str] = Field(default_factory=list)
    immutable_constraints: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_use_decision(self):
        if self.use_event and not self.allowed_facts:
            raise ValueError("use_event=true requires at least one allowed_fact")
        if not self.use_event and self.allowed_facts:
            raise ValueError("use_event=false requires allowed_facts to be empty")
        return self


class _DirectorEventConsumptionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consumption: _DirectorEventConsumptionDraft


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
        turn_resolution: TurnResolutionResult,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfileDirectorView] | None = None,
        trace_id: str | None = None,
    ) -> list[DirectorEventConsumption]:
        if not events:
            return []

        consume_one = partial(
            self._consume_one,
            user_input=user_input,
            current_state=current_state,
            core_memory=core_memory,
            context_facts=context_facts,
            turn_resolution=turn_resolution,
            entity_profiles=entity_profiles or [],
            npc_profiles=npc_profiles or [],
            trace_id=trace_id,
        )

        with ThreadPoolExecutor(max_workers=len(events)) as executor:
            results = list(executor.map(consume_one, events))

        return [
            DirectorEventConsumption(
                **result.model_dump(),
            )
            for event, result in zip(events, results, strict=True)
            if result.use_event
        ]

    def _consume_one(
        self,
        event: DirectorEvent,
        *,
        user_input: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        context_facts: ContextFactSet,
        turn_resolution: TurnResolutionResult,
        entity_profiles: list[EntityProfileUpdateInput],
        npc_profiles: list[NPCProfileDirectorView],
        trace_id: str | None,
    ) -> _DirectorEventConsumptionDraft:
        result = cast(
            _DirectorEventConsumptionResult,
            self.llm.run(
                task="director_event_consume",
                input_data={
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "user_input": user_input,
                    "context_facts_json": project_context_facts(context_facts),
                    "turn_resolution_json": project_turn_resolution(turn_resolution),
                    "entity_profiles_json": project_entity_profiles(entity_profiles),
                    "npc_profiles_json": project_npc_profiles(npc_profiles),
                    "director_event_json": project_director_event(event),
                    "output_schema_json": json.dumps(
                        _DirectorEventConsumptionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=_DirectorEventConsumptionResult,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
        consumption = result.consumption
        if consumption.event_id != event.event_id:
            raise ValueError(
                "DirectorEventConsumer returned a result for the wrong active event; "
                f"expected {event.event_id}, got {consumption.event_id}"
            )
        return consumption
