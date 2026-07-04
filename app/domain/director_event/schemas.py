import re
import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


DirectorEventStatus = Literal["active", "suspended", "archived"]
ReviewDecision = Literal["keep_active", "archive_active", "suspend_active"]
SelectionAction = Literal["activate_suspended", "create_new"]


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
    summary: str = Field(
        min_length=1,
        description="A natural way this backstage event can enter frontstage play.",
    )
    natural_contexts: list[str] = Field(default_factory=list)
    involved_npcs: list[str] = Field(default_factory=list)
    pressure: str = Field(
        min_length=1,
        description="The immediate pressure, opportunity, or disturbance this entry point creates.",
    )

    @field_validator("summary", "pressure")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("natural_contexts", "involved_npcs")
    @classmethod
    def _validate_list_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)


class DirectorEvent(BaseModel):
    event_id: str = Field(default_factory=_new_id)
    title: str = Field(min_length=1)
    status: DirectorEventStatus = "active"
    backstage_premise: str = ""
    player_involved: bool = Field(
        default=False,
        description="Whether the player has already directly engaged with this event.",
    )
    entry_points: list[DirectorEventEntryPoint] = Field(default_factory=list)
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)
    driving_tension: str = Field(
        default="",
        description="Why this event is still active and unresolved.",
    )
    suspended_reason: str | None = None
    resolved_reason: str | None = None
    resolved_turn: int | None = Field(default=None, ge=0)
    created_turn: int = Field(ge=0)
    updated_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("backstage_premise", "driving_tension")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("related_npcs", "location_constraints")
    @classmethod
    def _validate_lists_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)

    def semantic_view(self) -> dict:
        return {
            "event_id": self.event_id,
            "title": self.title,
            "backstage_premise": self.backstage_premise,
            "player_involved": self.player_involved,
            "entry_points": [entry.model_dump(mode="json") for entry in self.entry_points],
            "related_npcs": self.related_npcs,
            "location_constraints": self.location_constraints,
            "driving_tension": self.driving_tension,
        }


class DirectorEventDraft(BaseModel):
    title: str = Field(min_length=1)
    backstage_premise: str = Field(
        min_length=1,
        description="Hidden event facts without dates, deadlines, countdowns, seasons, lunar cycles, hours, or other time anchors.",
    )
    entry_points: list[DirectorEventEntryPoint] = Field(min_length=1)
    related_npcs: list[str] = Field(default_factory=list)
    location_constraints: list[str] = Field(default_factory=list)
    driving_tension: str = Field(
        min_length=1,
        description="The unresolved active tension that explains why this director event is still not over.",
    )

    @field_validator("backstage_premise", "driving_tension")
    @classmethod
    def _validate_no_time_anchors(cls, value: str) -> str:
        return _reject_time_anchors(value)

    @field_validator("related_npcs", "location_constraints")
    @classmethod
    def _validate_lists_no_time_anchors(cls, values: list[str]) -> list[str]:
        return _reject_time_anchors_in_list(values)

    def to_event(self, *, turn_index: int, status: DirectorEventStatus = "active") -> DirectorEvent:
        now = utc_now()
        return DirectorEvent(
            title=self.title,
            status=status,
            backstage_premise=self.backstage_premise,
            entry_points=list(self.entry_points),
            related_npcs=self.related_npcs,
            location_constraints=self.location_constraints,
            driving_tension=self.driving_tension,
            created_turn=turn_index,
            updated_turn=turn_index,
            created_at=now,
            updated_at=now,
        )


class DirectorEventReviewResult(BaseModel):
    reason: str = Field(min_length=1)
    decision: ReviewDecision
    player_involved: bool = Field(
        description="True if the confirmed scene shows the player has directly engaged with this event.",
    )
    evidence: list[str] = Field(default_factory=list)
    suspended_reason: str | None = None

    @model_validator(mode="after")
    def _validate_suspended_reason(self):
        if self.decision == "suspend_active" and not (self.suspended_reason or "").strip():
            self.suspended_reason = self.reason
        return self


class DirectorEventSelectionResult(BaseModel):
    suspension_evaluation: str = Field(min_length=1)
    backstage_undercurrent: str | None = None
    action: SelectionAction
    reason: str = Field(min_length=1)
    activate_event_id: str | None = None
    new_event: DirectorEventDraft | None = None

    @field_validator("backstage_undercurrent")
    @classmethod
    def _validate_backstage_undercurrent_no_time_anchors(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _reject_time_anchors(value)

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
