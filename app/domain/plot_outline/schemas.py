from pydantic import BaseModel, Field


class PlotBeat(BaseModel):
    beat_id: str
    summary: str
    linked_action_ids: list[str] = Field(default_factory=list)
    linked_roll_ids: list[str] = Field(default_factory=list)
    required_memories: list[str] = Field(default_factory=list)
    state_implications: list[str] = Field(default_factory=list)
    tone_notes: list[str] = Field(default_factory=list)


class PlotOutlineResult(BaseModel):
    outline_summary: str
    beats: list[PlotBeat] = Field(default_factory=list)
    constraints_for_scene_writer: list[str] = Field(default_factory=list)
    risks_to_avoid: list[str] = Field(default_factory=list)
