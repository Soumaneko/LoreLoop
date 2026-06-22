import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


DirectorEventStatus = Literal["active", "suspended", "archived"]
ReviewDecision = Literal["keep_active", "archive_active", "suspend_active"]
SelectionAction = Literal["activate_suspended", "create_new"]


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorEvent(BaseModel):
    event_id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    description: str = Field(min_length=1)
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)
    suspended_reason: str | None = None
    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "description": self.description,
            "related_npcs": self.related_npcs,
            "location_constraints": self.location_constraints,
        }


class DirectorEventDraft(BaseModel):
    title: str = Field(min_length=1)
    description: str = Field(min_length=1)
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)

    def to_event(self, *, turn_index: int, status: DirectorEventStatus = "active") -> DirectorEvent:
        now = utc_now()
        return DirectorEvent(
            title=self.title,
            status=status,
            description=self.description,
            related_npcs=self.related_npcs,
            location_constraints=self.location_constraints,
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

    @model_validator(mode="after")
    def _validate_suspended_reason(self):
        if self.decision == "suspend_active" and not (self.suspended_reason or "").strip():
            self.suspended_reason = self.reason
        return self


class DirectorEventSelectionResult(BaseModel):
    action: SelectionAction
    reason: str = Field(min_length=1)
    activate_event_id: str | None = None
    new_event: DirectorEventDraft | None = None

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action == "activate_suspended" and not self.activate_event_id:
            raise ValueError("activate_event_id is required when action is activate_suspended")
        if self.action == "create_new" and self.new_event is None:
            raise ValueError("new_event is required when action is create_new")
        return self
