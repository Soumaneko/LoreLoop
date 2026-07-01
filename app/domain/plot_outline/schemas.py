from pydantic import BaseModel, Field


class PlotBeat(BaseModel):
    beat_id: str
    summary: str
    user_action_execution: str | None = None
    related_entities: list[str] = Field(default_factory=list)
    linked_outcome_ids: list[str] = Field(default_factory=list)
    required_outcomes: list[str] = Field(default_factory=list)
    required_memory_indices: list[int] = Field(default_factory=list)


class PlotOutlineResult(BaseModel):
    context_reasoning: str = Field(min_length=1)
    beats: list[PlotBeat] = Field(default_factory=list)
    constraints_for_scene_writer: list[str] = Field(default_factory=list)
    risks_to_avoid: list[str] = Field(default_factory=list)
    director_event_note: str | None = None
