import re

from pydantic import BaseModel, Field, field_validator


class PlotBeat(BaseModel):
    beat_id: str
    summary: str
    user_action_execution: str | None = None
    protagonist_response_guidance: str = Field(min_length=1)
    related_entities: list[str] = Field(default_factory=list)
    linked_outcome_ids: list[str] = Field(default_factory=list)
    required_outcomes: list[str] = Field(default_factory=list)
    required_memory_indices: list[int] = Field(default_factory=list)
    state_implications: list[str] = Field(default_factory=list)
    tone_notes: list[str] = Field(default_factory=list)

    @field_validator("protagonist_response_guidance")
    @classmethod
    def _require_concrete_protagonist_response(cls, value: str) -> str:
        cleaned = value.strip()
        optional_patterns = (
            r"可选择",
            r"任选",
        )
        if any(re.search(pattern, cleaned) for pattern in optional_patterns):
            raise ValueError(
                "protagonist_response_guidance must prescribe one concrete response, "
                "not optional alternatives"
            )
        return cleaned


class PlotOutlineResult(BaseModel):
    outline_summary: str
    beats: list[PlotBeat] = Field(default_factory=list)
    constraints_for_scene_writer: list[str] = Field(default_factory=list)
    risks_to_avoid: list[str] = Field(default_factory=list)
    director_event_note: str | None = None
