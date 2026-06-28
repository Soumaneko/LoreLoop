import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


DirectorEventStatus = Literal["active", "suspended", "archived"]
DirectorEventPhase = Literal["latent", "manifesting", "crisis"]
ReviewDecision = Literal["keep_active", "archive_active", "suspend_active"]
SelectionAction = Literal["activate_suspended", "create_new"]


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorEventStages(BaseModel):
    latent: str = Field(min_length=1)
    manifesting: str = Field(min_length=1)
    crisis: str = Field(min_length=1)


class DirectorEvent(BaseModel):
    event_id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    phase: DirectorEventPhase
    phase_turns_elapsed: int = Field(default=0, ge=0)
    backstage_premise: str = ""
    stages: DirectorEventStages
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)
    manifesting_prevention_hooks: list[str] = Field(default_factory=list)
    crisis_resolution_hooks: list[str] = Field(default_factory=list)
    known_to_player: list[str] | None
    hidden_developments: list[str] | None
    suspended_reason: str | None = None
    resolved_by_hook: str | None = None
    resolved_reason: str | None = None
    resolved_turn: int | None = Field(default=None, ge=0)
    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "phase": self.phase,
            "phase_turns_elapsed": self.phase_turns_elapsed,
            "backstage_premise": self.backstage_premise,
            "current_description": getattr(self.stages, self.phase),
            "related_npcs": self.related_npcs,
            "location_constraints": self.location_constraints,
            "manifesting_prevention_hooks": self.manifesting_prevention_hooks,
            "crisis_resolution_hooks": self.crisis_resolution_hooks,
            "known_to_player": self.known_to_player,
            "hidden_developments": self.hidden_developments,
        }


class DirectorEventDraft(BaseModel):
    title: str = Field(min_length=1)
    backstage_premise: str = Field(min_length=1)
    stages: DirectorEventStages
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)
    manifesting_prevention_hooks: list[str] = Field(min_length=3, max_length=3)
    crisis_resolution_hooks: list[str] = Field(min_length=3, max_length=3)

    def to_event(self, *, turn_index: int, status: DirectorEventStatus = "active") -> DirectorEvent:
        now = utc_now()
        return DirectorEvent(
            title=self.title,
            status=status,
            phase="latent",
            phase_turns_elapsed=0,
            backstage_premise=self.backstage_premise,
            stages=self.stages,
            related_npcs=self.related_npcs,
            location_constraints=self.location_constraints,
            manifesting_prevention_hooks=self.manifesting_prevention_hooks,
            crisis_resolution_hooks=self.crisis_resolution_hooks,
            known_to_player=None,
            hidden_developments=None,
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventReviewResult(BaseModel):
    reason: str = Field(min_length=1)
    decision: ReviewDecision
    evidence: list[str] = Field(default_factory=list)
    suspended_reason: str | None = None
    phase_advance_recommended: bool
    phase_advance_reason: str | None
    known_to_player_updates: list[str] | None
    hidden_development_updates: list[str] | None
    matched_manifesting_prevention_hook: str | None = None
    matched_crisis_resolution_hook: str | None = None

    @model_validator(mode="after")
    def _validate_suspended_reason(self):
        if self.decision == "suspend_active" and not (self.suspended_reason or "").strip():
            self.suspended_reason = self.reason
        if self.phase_advance_recommended:
            if self.decision != "keep_active":
                raise ValueError("phase advance can only be recommended for keep_active")
            if not (self.phase_advance_reason or "").strip():
                raise ValueError(
                    "phase_advance_reason is required when phase advance is recommended"
                )
            if not self.evidence:
                raise ValueError(
                    "evidence is required when phase advance is recommended"
                )
        elif self.phase_advance_reason is not None:
            raise ValueError(
                "phase_advance_reason must be null when phase advance is not recommended"
            )
        if self.matched_manifesting_prevention_hook and self.matched_crisis_resolution_hook:
            raise ValueError(
                "only one director event hook can be matched in a single review"
            )
        return self


class DirectorEventSelectionResult(BaseModel):
    suspension_evaluation: str = Field(min_length=1)
    backstage_undercurrent: str | None = None
    action: SelectionAction
    reason: str = Field(min_length=1)
    activate_event_id: str | None = None
    new_event: DirectorEventDraft | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action == "activate_suspended" and not self.activate_event_id:
            raise ValueError("activate_event_id is required when action is activate_suspended")
        if self.action == "create_new":
            if self.new_event is None:
                raise ValueError("new_event is required when action is create_new")
            if not (self.backstage_undercurrent or "").strip():
                raise ValueError("backstage_undercurrent is required when action is create_new")
        return self
