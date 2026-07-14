import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.current_state.schemas import WorldClock


DirectorEventStatus = Literal["active", "resolved"]
DirectorEventRole = Literal["arc", "ambient"]
DirectorEventExposure = Literal["hidden", "foreshadowed", "engaged"]
ReviewAction = Literal["hold", "advance", "resolve"]


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorEventEntryPoint(BaseModel):
    trigger_anchors: list[str] = Field(
        min_length=1,
    )
    visible_sign: str = Field(
        min_length=1,
    )
    immediate_effect: str = Field(
        min_length=1,
    )

class DirectorEvent(BaseModel):
    event_id: str = Field(default_factory=_new_id)
    role: DirectorEventRole
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    premise: str = Field(
        min_length=1,
    )
    director_npc_id: str | None = None
    actors: list[str] = Field(default_factory=list)
    objective: str = Field(min_length=1)
    bridge_anchors: list[str] = Field(
        min_length=1,
    )
    milestones: list[str] = Field(
        min_length=3,
        max_length=3,
    )
    milestone_interval_steps: list[int] = Field(
        min_length=2,
        max_length=2,
    )
    next_progress_clock: WorldClock | None = None
    progress_index: int = Field(default=0, ge=0, le=2)
    exposure: DirectorEventExposure = "hidden"
    player_contact_state: str = Field(
        default="玩家尚未接触该事件的任何前台征兆。",
        min_length=1,
        max_length=50,
    )
    entry_points: list[DirectorEventEntryPoint] = Field(default_factory=list)
    last_presented_turn: int | None = Field(default=None, ge=0)
    resolved_reason: str | None = None
    resolved_turn: int | None = Field(default=None, ge=0)
    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("milestone_interval_steps")
    @classmethod
    def _validate_positive_intervals(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("milestone_interval_steps must contain positive phase counts")
        return values

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "role": self.role,
            "title": self.title,
            "premise": self.premise,
            "director_npc_id": self.director_npc_id,
            "actors": self.actors,
            "objective": self.objective,
            "bridge_anchors": self.bridge_anchors,
            "milestones": self.milestones,
            "milestone_interval_steps": self.milestone_interval_steps,
            "next_progress_clock": (
                self.next_progress_clock.model_dump(mode="json")
                if self.next_progress_clock is not None
                else None
            ),
            "progress_index": self.progress_index,
            "exposure": self.exposure,
            "player_contact_state": self.player_contact_state,
            "entry_points": [entry.model_dump(mode="json") for entry in self.entry_points],
            "last_presented_turn": self.last_presented_turn,
        }


_PHASES_PER_DAY = 6


def _float_days_to_steps(days: float) -> int:
    return max(1, round(days * _PHASES_PER_DAY))


class DirectorEventSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    title: str = Field(min_length=1)
    director_npc_id: str | None = Field(
        default=None,
        description=(
            "Exact draft_id when an ambient event uses one supplied standby "
            "DirectorNPC; otherwise null."
        ),
    )
    actors: list[str] = Field(min_length=1)
    objective: str = Field(min_length=1)
    bridge_anchors: list[str] = Field(min_length=1)
    milestones: list[str] = Field(
        min_length=3,
        max_length=3,
    )
    milestone_interval_days: list[float] = Field(
        min_length=2,
        max_length=2,
    )
    premise: str = Field(
        min_length=1,
    )
    entry_points: list[DirectorEventEntryPoint] = Field(min_length=1)

    @field_validator("milestone_interval_days")
    @classmethod
    def _validate_positive_days(cls, values: list[float]) -> list[float]:
        if any(value <= 0 for value in values):
            raise ValueError("milestone_interval_days must contain positive floats")
        return values

    def _compute_interval_steps(self) -> list[int]:
        return [
            _float_days_to_steps(self.milestone_interval_days[0]),
            _float_days_to_steps(self.milestone_interval_days[1]),
        ]

    def to_event(
        self,
        *,
        role: DirectorEventRole,
        turn_index: int,
        current_clock: WorldClock,
        status: DirectorEventStatus = "active",
    ) -> DirectorEvent:
        interval_steps = self._compute_interval_steps()
        now = utc_now()
        return DirectorEvent(
            role=role,
            title=self.title,
            status=status,
            premise=self.premise,
            director_npc_id=self.director_npc_id,
            actors=self.actors,
            objective=self.objective,
            bridge_anchors=self.bridge_anchors,
            milestones=self.milestones,
            milestone_interval_steps=interval_steps,
            next_progress_clock=current_clock.advance_phase(interval_steps[0]),
            progress_index=0,
            exposure="hidden",
            entry_points=list(self.entry_points),
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventReviewResult(BaseModel):
    reason: str = Field(min_length=1)
    action: ReviewAction
    exposure: DirectorEventExposure
    player_contact_state: str = Field(min_length=1, max_length=50)
    evidence: list[str] = Field(default_factory=list)
    resolved_reason: str | None = None

    @model_validator(mode="after")
    def _validate_resolved_reason(self):
        if self.action == "resolve" and not (self.resolved_reason or "").strip():
            self.resolved_reason = self.reason
        return self


class DirectorEventConsumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    use_event: bool
    player_contact_state: str = Field(default="", max_length=50)
    allowed_facts: list[str] = Field(default_factory=list)
    immutable_constraints: list[str] = Field(default_factory=list)
    forbidden_inferences: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_use_decision(self):
        if self.use_event and not self.allowed_facts:
            raise ValueError("use_event=true requires at least one allowed_fact")
        if not self.use_event and self.allowed_facts:
            raise ValueError("use_event=false requires allowed_facts to be empty")
        return self
