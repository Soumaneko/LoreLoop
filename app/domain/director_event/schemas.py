import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.current_state.schemas import WorldClock


DirectorEventStatus = Literal["active", "resolved"]
DirectorEventExposure = Literal["hidden", "foreshadowed", "engaged"]
ReviewAction = Literal["hold", "advance", "resolve"]
SelectionAction = Literal["create_new", "none"]


def _new_id() -> str:
    return f"director_event_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


_TIME_ANCHOR_RE = re.compile(
    r"("
    r"\d+\s*(?:日|天|个月|月|年|时辰|小时|分钟)(?:内|后|前)?"
    r"|[一二三四五六七八九十百半数几两]\s*(?:日|天|个月|月|年|时辰)(?:内|后|前)?"
    r"|[一二三四五六七八九十百数几两]\s*刻钟(?:内|后|前)?"
    r"|月圆|月相|朔月|满月|新月|半月"
    r"|子时|丑时|寅时|卯时|辰时|巳时|午时|未时|申时|酉时|戌时|亥时"
    r"|入夜|夜间|夜半|清晨|黎明|黄昏|傍晚|正午|午后|昨夜|明日|前夕"
    r"|节庆|节前|节后|期限|截止|倒计时|日期|时日"
    r")"
)


def _reject_time_anchors(value: str) -> str:
    if _TIME_ANCHOR_RE.search(value):
        raise ValueError("director event text must not contain time anchors")
    return value


def _reject_time_anchors_in_list(values: list[str]) -> list[str]:
    return [_reject_time_anchors(value) for value in values]


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

    @field_validator("visible_sign", "immediate_effect")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("trigger_anchors")
    @classmethod
    def _validate_list_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)


class DirectorEvent(BaseModel):
    event_id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    premise: str = Field(
        min_length=1,
    )
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
    entry_points: list[DirectorEventEntryPoint] = Field(default_factory=list)
    last_presented_turn: int | None = Field(default=None, ge=0)
    resolved_reason: str | None = None
    resolved_turn: int | None = Field(default=None, ge=0)
    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("premise", "objective")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("actors", "bridge_anchors", "milestones")
    @classmethod
    def _validate_lists_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)

    @field_validator("milestone_interval_steps")
    @classmethod
    def _validate_positive_intervals(cls, values: list[int]) -> list[int]:
        if any(value < 1 for value in values):
            raise ValueError("milestone_interval_steps must contain positive phase counts")
        return values

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "premise": self.premise,
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
            "entry_points": [entry.model_dump(mode="json") for entry in self.entry_points],
            "last_presented_turn": self.last_presented_turn,
        }


_PHASES_PER_DAY = 6


def _float_days_to_steps(days: float) -> int:
    return max(1, round(days * _PHASES_PER_DAY))


class DirectorEventDraft(BaseModel):
    title: str = Field(min_length=1)
    premise: str = Field(
        min_length=1,
    )
    actors: list[str] = Field(default_factory=list)
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
    entry_points: list[DirectorEventEntryPoint] = Field(min_length=1)

    @field_validator("premise", "objective")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("actors", "bridge_anchors", "milestones")
    @classmethod
    def _validate_lists_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)

    @field_validator("milestone_interval_days")
    @classmethod
    def _validate_non_negative_days(cls, values: list[float]) -> list[float]:
        if any(value < 0 for value in values):
            raise ValueError("milestone_interval_days must contain non-negative floats")
        return values

    def _compute_interval_steps(self) -> list[int]:
        return [
            _float_days_to_steps(self.milestone_interval_days[0]),
            _float_days_to_steps(self.milestone_interval_days[1]),
        ]

    def to_event(
        self,
        *,
        turn_index: int,
        current_clock: WorldClock,
        status: DirectorEventStatus = "active",
    ) -> DirectorEvent:
        interval_steps = self._compute_interval_steps()
        now = utc_now()
        return DirectorEvent(
            title=self.title,
            status=status,
            premise=self.premise,
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
    evidence: list[str] = Field(default_factory=list)
    resolved_reason: str | None = None

    @model_validator(mode="after")
    def _validate_resolved_reason(self):
        if self.action == "resolve" and not (self.resolved_reason or "").strip():
            self.resolved_reason = self.reason
        return self


EventUseMode = Literal["none", "foreshadow", "intersect", "foreground", "aftermath"]


class EventUseInstruction(BaseModel):
    mode: EventUseMode
    entry_point_id: str | None = None
    allowed_effect: str | None = None
    reason: str = Field(min_length=1)


class DirectorEventSelectionResult(BaseModel):
    bridge_evaluation: str = Field(min_length=1)
    world_undercurrent: str | None = None
    action: SelectionAction
    reason: str = Field(min_length=1)
    new_event: DirectorEventDraft | None = None

    @field_validator("world_undercurrent")
    @classmethod
    def _validate_world_undercurrent_no_time_anchors(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_time_anchors(value)

    @model_validator(mode="after")
    def _validate_action_payload(self):
        if self.action == "create_new":
            if self.new_event is None:
                raise ValueError("new_event is required when action is create_new")
            if not (self.world_undercurrent or "").strip():
                raise ValueError("world_undercurrent is required when action is create_new")
        if self.action == "none" and self.new_event is not None:
            raise ValueError("new_event must be null when action is none")
        return self
