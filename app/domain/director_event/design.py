"""Initial Event design before actor creation and final composition."""

import json
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json

from .schemas import DirectorEventActorRole


class DirectorEventDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    conception: str = Field(min_length=1, max_length=800)
    subject: str = Field(min_length=1)
    actor_roles: list[DirectorEventActorRole] = Field(max_length=11)
    additional_npcs: list[DirectorEventActorRole] = Field(max_length=3)
    selected_setting_fact_ids: list[str] = Field(min_length=1, max_length=8)
    intent: str = Field(min_length=1)

    @model_validator(mode="after")
    def unique_references(self):
        keys = [role.actor_key for role in [*self.actor_roles, *self.additional_npcs]]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate actor_key across actor lists")
        if len(self.selected_setting_fact_ids) != len(set(self.selected_setting_fact_ids)):
            raise ValueError("selected_setting_fact_ids has duplicate fact_id")
        return self


class DirectorEventDesigner:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def design(
        self, *, input_data: dict, trace_id: str | None = None,
    ) -> DirectorEventDesign:
        """Create the initial design from prepared Event creation inputs."""
        required_keys = {
            actor["actor_key"]
            for actor in json.loads(input_data["required_actors_json"])
        }
        available_fact_ids = {
            fact["fact_id"]
            for setting in json.loads(input_data["settings_json"])
            for field in ("public_facts", "hidden_facts")
            for fact in setting[field]
            if "fact_id" in fact
        }

        def validate_design(design: DirectorEventDesign) -> None:
            if {role.actor_key for role in design.actor_roles} != required_keys:
                raise ValueError("actor_roles must cover exactly every required actor_key")
            if set(design.selected_setting_fact_ids) - available_fact_ids:
                raise ValueError("selected_setting_fact_ids are unavailable")

        return cast(
            DirectorEventDesign,
            self.llm.run(
                task="director_event_design",
                input_data={**input_data, "output_schema_json": schema_json(DirectorEventDesign)},
                output_schema=DirectorEventDesign,
                temperature=0.3,
                trace_id=trace_id,
                output_validator=validate_design,
            ),
        )
