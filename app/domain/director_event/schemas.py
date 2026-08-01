import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.director.schemas import DirectorResourceRef

DirectorEventRole = Literal["arc", "ambient"]
DirectorEventStage = Literal["hidden", "engaged", "resolved"]
EventHookKind = Literal["auditory", "visual", "rumor", "item", "environment"]
ReviewAction = Literal["hold", "resolve"]
PLAYER_CONTACT_MAX_LENGTH = 200
PLAYER_CONTACT_MAX_RECORDS = 5


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlayerContactRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    contact: str = Field(min_length=1, max_length=PLAYER_CONTACT_MAX_LENGTH)


class EventHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hook_id: str = Field(min_length=1, max_length=80)
    kind: EventHookKind
    content: str = Field(min_length=1)
    trigger_anchors: list[str] = Field(min_length=1)
    consumed: bool = False
    consumed_turn: int | None = Field(default=None, ge=0)


class WorldImpact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = Field(min_length=1)
    setting_hint: str | None = None


class DirectorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    role: DirectorEventRole
    title: str = Field(min_length=1)
    stage: DirectorEventStage = "hidden"

    subject: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    method: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)

    hook_pool: list[EventHook] = Field(default_factory=list)

    actors: list[str] = Field(default_factory=list)
    actor_refs: list[DirectorResourceRef] = Field(default_factory=list)

    world_impact: WorldImpact | None = None

    player_contacts: list[PlayerContactRecord] = Field(
        default_factory=list,
        max_length=PLAYER_CONTACT_MAX_RECORDS,
    )
    last_presented_turn: int | None = Field(default=None, ge=0)

    resolved_reason: str | None = None
    resolved_turn: int | None = Field(default=None, ge=0)

    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("hook_pool", mode="before")
    @classmethod
    def _dedupe_hook_ids(cls, value):
        if not isinstance(value, list):
            return value
        seen: set[str] = set()
        kept: list[dict] = []
        for hook in value:
            hook_id = hook.get("hook_id") if isinstance(hook, dict) else getattr(hook, "hook_id", None)
            if hook_id is None or hook_id in seen:
                continue
            seen.add(hook_id)
            kept.append(hook)
        return kept

    def is_active(self) -> bool:
        return self.stage in {"hidden", "engaged"}

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "role": self.role,
            "title": self.title,
            "stage": self.stage,
            "subject": self.subject,
            "intent": self.intent,
            "method": self.method,
            "macro_intent": self.macro_intent,
            "actors": self.actors,
            "actor_refs": [
                actor_ref.model_dump(mode="json")
                for actor_ref in self.actor_refs
            ],
            "player_contacts": [
                contact.model_dump(mode="json")
                for contact in self.player_contacts
            ],
            "last_presented_turn": self.last_presented_turn,
        }


class DirectorEventSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    title: str = Field(min_length=1)
    actors: list[str] = Field(min_length=1)
    subject: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    method: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)
    hook_pool: list[EventHook] = Field(min_length=1)
    world_impact: WorldImpact

    def to_event(
        self,
        *,
        role: DirectorEventRole,
        turn_index: int,
    ) -> DirectorEvent:
        now = utc_now()
        return DirectorEvent(
            role=role,
            title=self.title,
            stage="hidden",
            subject=self.subject,
            intent=self.intent,
            method=self.method,
            macro_intent=self.macro_intent,
            hook_pool=list(self.hook_pool),
            actors=list(self.actors),
            actor_refs=[],
            world_impact=self.world_impact,
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventReviewResult(BaseModel):
    reason: str = Field(min_length=1)
    action: ReviewAction
    new_player_contact: str | None = Field(
        min_length=1,
        max_length=PLAYER_CONTACT_MAX_LENGTH,
    )
    resolved_reason: str | None = None

    @model_validator(mode="after")
    def _validate_resolved_reason(self):
        if self.action == "resolve" and not (self.resolved_reason or "").strip():
            self.resolved_reason = self.reason
        return self
