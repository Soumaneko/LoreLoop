from pydantic import BaseModel, Field


class PlannedEvent(BaseModel):
    event_id: str
    event_fact: str
    linked_outcome_ids: list[str] = Field(default_factory=list)
    involved_entities: list[str] = Field(default_factory=list)
    required_memory_indices: list[int] = Field(default_factory=list)


class PlotOutlineResult(BaseModel):
    planning_reasoning: str = Field(min_length=1)
    planned_events: list[PlannedEvent] = Field(default_factory=list)
    must_establish_facts: list[str] = Field(default_factory=list)
    must_not_establish_facts: list[str] = Field(default_factory=list)
    information_boundaries: list[str] = Field(default_factory=list)
    required_entities: list[str] = Field(default_factory=list)
    scene_stopping_point: str = Field(min_length=1)
    director_event_reason: str | None = None
    director_npc_reason: str | None = None
