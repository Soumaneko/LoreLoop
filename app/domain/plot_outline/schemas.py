from pydantic import BaseModel, Field

from app.domain.action_judgment.schemas import OutcomeBand


class PlannedEvent(BaseModel):
    event_id: str
    event_fact: str
    linked_outcome_ids: list[str] = Field(default_factory=list)
    involved_entities: list[str] = Field(default_factory=list)


class ActionResolution(BaseModel):
    action_id: str
    outcome_band: OutcomeBand
    resolved_fact: str = Field(min_length=1, max_length=180)


class PlotOutlineResult(BaseModel):
    director_reason: str | None = None
    action_resolutions: list[ActionResolution] = Field(default_factory=list)
    scene_stopping_point: str = Field(min_length=1)
    planned_events: list[PlannedEvent] = Field(default_factory=list)
