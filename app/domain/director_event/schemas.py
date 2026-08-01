import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.current_state.schemas import WorldClock
from app.domain.director.schemas import DirectorResourceRef


DirectorEventStatus = Literal["active", "resolved"]
DirectorEventRole = Literal["arc", "ambient"]
DirectorEventExposure = Literal["hidden", "foreshadowed", "engaged"]
DirectorEventRouteMode = Literal["default", "intervention"]
InterventionRouteStatus = Literal["active", "succeeded", "failed"]
DirectorEventResolutionKind = Literal[
    "default",
    "alternate",
    "intervention_success",
    "intervention_failure",
]
InterventionOutcome = Literal["success", "failure"]
ReviewAction = Literal["hold", "advance", "redirect", "resolve"]
InterventionReviewStatus = Literal["ongoing", "success", "failure"]
PLAYER_CONTACT_MAX_LENGTH = 200
PLAYER_CONTACT_MAX_RECORDS = 5


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


class PlayerContactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    contact: str = Field(min_length=1, max_length=PLAYER_CONTACT_MAX_LENGTH)


class DirectorEventInterventionRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")

    triggered_turn: int = Field(ge=0)
    redirect_reason: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)
    plan_reason: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    success_state: str = Field(min_length=1)
    failure_state: str = Field(min_length=1)
    duration_phases: int = Field(ge=1, le=60)
    deadline_clock: WorldClock
    status: InterventionRouteStatus = "active"
    resolved_turn: int | None = Field(default=None, ge=0)


class DirectorEventInterventionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    success_state: str = Field(min_length=1)
    failure_state: str = Field(min_length=1)
    duration_phases: int = Field(ge=1, le=60)

    def to_route(
        self,
        *,
        triggered_turn: int,
        redirect_reason: str,
        evidence: list[str],
        current_clock: WorldClock,
    ) -> DirectorEventInterventionRoute:
        return DirectorEventInterventionRoute(
            triggered_turn=triggered_turn,
            redirect_reason=redirect_reason,
            evidence=list(evidence),
            plan_reason=self.reason,
            goal=self.goal,
            success_state=self.success_state,
            failure_state=self.failure_state,
            duration_phases=self.duration_phases,
            deadline_clock=current_clock.advance_phase(self.duration_phases),
        )


class DirectorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    role: DirectorEventRole
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    premise: str = Field(
        min_length=1,
    )
    actors: list[str] = Field(default_factory=list)
    actor_refs: list[DirectorResourceRef] = Field(default_factory=list)
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
    progress_index: int = Field(default=1, ge=1, le=3)
    exposure: DirectorEventExposure = "hidden"
    player_contacts: list[PlayerContactRecord] = Field(
        default_factory=list,
        max_length=PLAYER_CONTACT_MAX_RECORDS,
    )
    entry_points: list[DirectorEventEntryPoint] = Field(default_factory=list)
    last_presented_turn: int | None = Field(default=None, ge=0)
    route_mode: DirectorEventRouteMode = "default"
    intervention_route: DirectorEventInterventionRoute | None = None
    resolution_kind: DirectorEventResolutionKind | None = None
    resolved_state: str | None = None
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

    @model_validator(mode="after")
    def _validate_route_state(self):
        if self.route_mode == "default" and self.intervention_route is not None:
            raise ValueError("default route cannot have an intervention_route")
        if self.route_mode == "intervention" and self.intervention_route is None:
            raise ValueError("intervention route requires intervention_route")
        if (
            self.status == "active"
            and self.intervention_route is not None
            and self.intervention_route.status != "active"
        ):
            raise ValueError("active intervention event requires an active route")
        return self

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "role": self.role,
            "title": self.title,
            "premise": self.premise,
            "actors": self.actors,
            "actor_refs": [
                actor_ref.model_dump(mode="json")
                for actor_ref in self.actor_refs
            ],
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
            "player_contacts": [
                contact.model_dump(mode="json") for contact in self.player_contacts
            ],
            "last_presented_turn": self.last_presented_turn,
            "route_mode": self.route_mode,
            "intervention_route": (
                self.intervention_route.model_dump(mode="json")
                if self.intervention_route is not None
                else None
            ),
        }


_PHASES_PER_DAY = 6


def _float_days_to_steps(days: float) -> int:
    return max(1, round(days * _PHASES_PER_DAY))


class DirectorEventSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    title: str = Field(min_length=1)
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
            actors=self.actors,
            actor_refs=[],
            objective=self.objective,
            bridge_anchors=self.bridge_anchors,
            milestones=self.milestones,
            milestone_interval_steps=interval_steps,
            next_progress_clock=current_clock.advance_phase(interval_steps[0]),
            progress_index=1,
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
    new_player_contact: str | None = Field(
        min_length=1,
        max_length=PLAYER_CONTACT_MAX_LENGTH,
    )
    evidence: list[str] = Field(default_factory=list)
    resolved_reason: str | None = None

    @model_validator(mode="after")
    def _validate_resolved_reason(self):
        if self.action == "redirect" and not self.evidence:
            raise ValueError("redirect requires evidence")
        if self.action == "redirect" and self.exposure != "engaged":
            raise ValueError("redirect requires engaged exposure")
        if self.action == "resolve" and not (self.resolved_reason or "").strip():
            self.resolved_reason = self.reason
        return self


class DirectorEventInterventionReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1)
    status: InterventionReviewStatus
    new_player_contact: str | None = Field(
        min_length=1,
        max_length=PLAYER_CONTACT_MAX_LENGTH,
    )
    evidence: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_evidence(self):
        if self.status in {"success", "failure"} and not self.evidence:
            raise ValueError("resolved intervention review requires evidence")
        return self
