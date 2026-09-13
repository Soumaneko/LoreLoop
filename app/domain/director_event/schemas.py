from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.ids import new_hex_id
from app.core.time import utc_now
from app.domain.current_state.schemas import WorldClock
from app.domain.director.schemas import DirectorResourceRef

DirectorEventStage = Literal["hidden", "engaged", "resolved"]
EventHookKind = Literal["auditory", "visual", "rumor", "item", "environment"]


_new_id = partial(new_hex_id, prefix="director_event", length=12)


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


class DirectorEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1)
    stage: DirectorEventStage = "hidden"

    subject: str = Field(min_length=1)
    intent: str = Field(min_length=1)
    method: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)
    conception: str = Field(default="", max_length=800)

    hook_pool: list[EventHook] = Field(default_factory=list)

    actors: list[DirectorActorRef] = Field(default_factory=list)
    source_setting_ids: list[str] = Field(default_factory=list, max_length=4)
    source_setting_fact_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
    )

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
            "title": self.title,
            "stage": self.stage,
            "subject": self.subject,
            "conception": self.conception,
            "intent": self.intent,
            "method": self.method,
            "macro_intent": self.macro_intent,
            "deadline_clock": self.deadline_clock.model_dump(mode="json"),
            "actors": [
                actor.model_dump(mode="json")
                for actor in self.actors
            ],
            "source_setting_ids": list(self.source_setting_ids),
        }


class DirectorEventSelectionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    current_story_direction: str = Field(min_length=1)
    conception: str = Field(min_length=1, max_length=800)
    subject: str = Field(min_length=1)
    actor_roles: list[DirectorEventActorRole] = Field(
        default_factory=list,
        max_length=6,
    )
    selected_setting_fact_ids: list[str] = Field(
        default_factory=list,
        max_length=8,
        description=(
            "本事件实际采用的 Setting Fact ID；必须从 settings_json 中"
            "仍带 fact_id 的事实里选择。"
        ),
    )
    intent: str = Field(min_length=1)
    method: str = Field(min_length=1)
    macro_intent: str = Field(min_length=1)
    hook_pool: list[EventHook] = Field(min_length=1)
    world_impact: WorldImpact
    title: str = Field(min_length=1)
    duration_phases: int = Field(ge=1, le=140)

    @field_validator("actor_roles")
    @classmethod
    def _unique_actor_keys(cls, value):
        keys = [item.actor_key for item in value]
        if len(keys) != len(set(keys)):
            raise ValueError("actor_roles has duplicate actor_key")
        return value

    @field_validator("selected_setting_fact_ids")
    @classmethod
    def _unique_setting_fact_ids(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("selected_setting_fact_ids has duplicate fact_id")
        return value

    def to_event(
        self,
        *,
        turn_index: int,
        current_clock: WorldClock,
        source_setting_ids: list[str] | None = None,
        actors: list[DirectorActorRef] | None = None,
    ) -> DirectorEvent:
        now = utc_now()
        return DirectorEvent(
            title=self.title,
            stage="hidden",
            subject=self.subject,
            intent=self.intent,
            method=self.method,
            macro_intent=self.macro_intent,
            conception=self.conception,
            hook_pool=list(self.hook_pool),
            actors=list(actors or []),
            source_setting_ids=list(source_setting_ids or []),
            source_setting_fact_ids=list(self.selected_setting_fact_ids),
            world_impact=self.world_impact,
            deadline_clock=current_clock.advance_phase(self.duration_phases),
            final_world_impact=None,
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventSelectionOutput(DirectorEventSelectionResult):
    """LLM boundary: an instantiated Event must name its Setting facts."""

    model_config = ConfigDict(extra="forbid", validate_default=True)

    selected_setting_fact_ids: list[str] = Field(
        default_factory=list, min_length=1, max_length=8
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
