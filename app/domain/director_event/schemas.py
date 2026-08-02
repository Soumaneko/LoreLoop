import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.current_state.schemas import WorldClock
from app.domain.director.schemas import DirectorResourceRef

DirectorEventRole = Literal["arc", "ambient"]
DirectorEventStage = Literal["hidden", "engaged", "resolved"]
EventHookKind = Literal["auditory", "visual", "rumor", "item", "environment"]


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorActorRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    ref: DirectorResourceRef | None = None


class DirectorEventActorRole(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    actor_key: str = Field(min_length=1, max_length=40)
    role_in_event: str = Field(min_length=1, max_length=200)


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

    actors: list[DirectorActorRef] = Field(default_factory=list)

    world_impact: WorldImpact | None = None
    deadline_clock: WorldClock
    final_world_impact: WorldImpact | None = None

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
            "deadline_clock": self.deadline_clock.model_dump(mode="json"),
            "actors": [
                actor.model_dump(mode="json")
                for actor in self.actors
            ],
        }


class DirectorEventSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    title: str = Field(min_length=1)
    duration_phases: int = Field(ge=1, le=140)
    actor_roles: list[DirectorEventActorRole] = Field(
        default_factory=list,
        max_length=6,
    )
    subject: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    method: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)
    hook_pool: list[EventHook] = Field(min_length=1)
    world_impact: WorldImpact

    @field_validator("actor_roles")
    @classmethod
    def _unique_actor_keys(cls, value):
        keys = [item.actor_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("actor_roles has duplicate actor_key")
        return value

    def to_event(
        self,
        *,
        role: DirectorEventRole,
        turn_index: int,
        current_clock: WorldClock,
        actors: list[DirectorActorRef] | None = None,
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
            actors=list(actors or []),
            world_impact=self.world_impact,
            deadline_clock=current_clock.advance_phase(self.duration_phases),
            final_world_impact=None,
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventHiddenReviewResult(BaseModel):
    event_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    action: Literal["keep_hidden", "engage"]
    consume_hook_ids: list[str] = Field(default_factory=list, max_length=16)


class DirectorEventHiddenReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: list[DirectorEventHiddenReviewResult] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_event_ids(self):
        event_ids = [review.event_id for review in self.reviews]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("hidden review batch has duplicate event_id")
        return self


class DirectorEventEngagedReviewResult(BaseModel):
    event_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    action: Literal["engage", "resolve"]
    resolved_reason: str | None = None
    player_still_involved: bool | None = None
    final_world_impact: WorldImpact | None = None

    @model_validator(mode="after")
    def _validate_resolved_reason(self):
        if self.action == "resolve" and not (self.resolved_reason or "").strip():
            self.resolved_reason = self.reason
        if self.player_still_involved is True and self.final_world_impact is not None:
            raise ValueError(
                "an involved player cannot receive an automatic final_world_impact"
            )
        if self.player_still_involved is False and self.final_world_impact is None:
            raise ValueError(
                "a disengaged player requires final_world_impact"
            )
        if self.player_still_involved is None and self.final_world_impact is not None:
            raise ValueError(
                "final_world_impact requires a deadline involvement decision"
            )
        return self


class DirectorEventEngagedReviewBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviews: list[DirectorEventEngagedReviewResult] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique_event_ids(self):
        event_ids = [review.event_id for review in self.reviews]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("engaged review batch has duplicate event_id")
        return self
