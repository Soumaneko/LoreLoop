"""Final Event composition from its design and completed actor profiles."""

import json
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.director_npc.schemas import DirectorNPCDraft

from .design import DirectorEventDesign
from app.domain.current_state.schemas import WorldClock

from .schemas import DirectorActorRef, DirectorEvent, DirectorEventFact, EventHook, WorldImpact
from .views import project_creation_settings


class DirectorEventComposition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conception: str = Field(min_length=1, max_length=800)
    subject: str = Field(min_length=1)
    current_situation: list[Annotated[str, Field(min_length=1)]] = Field(min_length=1)
    intent: str = Field(min_length=1)
    plan: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)
    hook_pool: list[EventHook] = Field(min_length=1)
    world_impact: WorldImpact
    title: str = Field(min_length=1)
    duration_phases: int = Field(ge=1, le=140)


    def to_event(
        self, *, turn_index: int, current_clock: WorldClock,
        actors: list[DirectorActorRef], source_setting_ids: list[str],
        source_setting_fact_ids: list[str],
    ) -> DirectorEvent:
        return DirectorEvent(
            **self.model_dump(exclude={"current_situation", "duration_phases"}),
            current_situation=[DirectorEventFact(fact=fact) for fact in self.current_situation],
            actors=actors,
            source_setting_ids=source_setting_ids,
            source_setting_fact_ids=source_setting_fact_ids,
            deadline_clock=current_clock.advance_phase(self.duration_phases),
            created_turn=turn_index, updated_turn=turn_index,
        )


class DirectorEventComposer:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def compose(
        self,
        *,
        input_data: dict,
        design: DirectorEventDesign,
        required_actors: list[dict],
        new_npcs: dict[str, DirectorNPCDraft],
        trace_id: str | None = None,
    ) -> DirectorEventComposition:
        """Combine prepared Selector context, the design, and completed NPC profiles."""
        roles = {role.actor_key: role.role_in_event for role in design.actor_roles}
        if set(roles) != {actor["actor_key"] for actor in required_actors}:
            raise ValueError("actor_roles must cover exactly every required actor_key")
        new_keys = {actor["actor_key"] for actor in required_actors if actor["source"] == "new"}
        new_keys.update(role.actor_key for role in design.additional_npcs)
        if set(new_npcs) != new_keys:
            raise ValueError("new_npcs must cover exactly every new actor_key")
        actors = [
            {
                "actor_key": actor["actor_key"],
                "role_in_event": roles[actor["actor_key"]],
                "profile": (
                    actor["profile"] if actor["source"] == "existing"
                    else new_npcs[actor["actor_key"]].private_view().model_dump(mode="json")
                ),
            }
            for actor in required_actors
        ]
        actors.extend(
            {
                "actor_key": role.actor_key,
                "role_in_event": role.role_in_event,
                "profile": new_npcs[role.actor_key].private_view().model_dump(mode="json"),
            }
            for role in design.additional_npcs
        )
        return cast(
            DirectorEventComposition,
            self.llm.run(
                task="director_event_compose",
                input_data={
                    **input_data,
                    "settings_json": json.dumps(
                        project_creation_settings(
                            json.loads(input_data["settings_json"]),
                            design.selected_setting_fact_ids,
                        ),
                        ensure_ascii=False, indent=2,
                    ),
                    "event_design_json": design.model_dump_json(indent=2),
                    "actors_json": json.dumps(actors, ensure_ascii=False, indent=2),
                    "output_schema_json": schema_json(DirectorEventComposition),
                },
                output_schema=DirectorEventComposition,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
