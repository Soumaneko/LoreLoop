from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    field_validator,
    model_validator,
)


ContinuityKind = Literal["commitment", "open_thread"]
ContinuityStatus = Literal["active", "resolved", "cancelled"]
ContinuityOperationType = Literal["create", "update", "resolve", "cancel"]

SUBJECT_HARD_LIMIT = 80
SUBJECT_ACCEPT_LIMIT = 100
SUBJECT_SOFT_LIMIT = 120
PREMISE_HARD_LIMIT = 240
PREMISE_ACCEPT_LIMIT = 300
PREMISE_SOFT_LIMIT = 360
STATE_DELTA_HARD_LIMIT = 240
STATE_DELTA_ACCEPT_LIMIT = 300
STATE_DELTA_SOFT_LIMIT = 360
CURRENT_STATE_HARD_LIMIT = 800
CURRENT_STATE_ACCEPT_LIMIT = 1000
CURRENT_STATE_SOFT_LIMIT = 1200
REASON_HARD_LIMIT = 160
REASON_ACCEPT_LIMIT = 200
REASON_SOFT_LIMIT = 240
CURRENT_STATE_MAX_CHARS = CURRENT_STATE_SOFT_LIMIT

ContinuityRelationReason = Literal[
    "exact_entity_match",
    "alias_match",
    "two_entity_match",
    "subject_topic_match",
    "premise_topic_match",
    "current_state_topic_match",
    "location_match",
]


class ContinuityItemDraft(BaseModel):
    """Creation contract. ``premise`` becomes immutable once persisted."""

    model_config = ConfigDict(extra="forbid")

    kind: ContinuityKind
    subject: str = Field(min_length=1, max_length=SUBJECT_SOFT_LIMIT)
    premise: str = Field(min_length=1, max_length=PREMISE_SOFT_LIMIT)
    entities: list[str] = Field(default_factory=list, max_length=12)
    location: str | None = Field(default=None, max_length=80)
    deadline: str | None = Field(default=None, max_length=80)


class ContinuityItemDraftHard(BaseModel):
    """prompt 展示 / 第一轮硬校验用的 create item（subject 80 / premise 240）。"""

    model_config = ConfigDict(extra="forbid")

    kind: ContinuityKind
    subject: str = Field(min_length=1, max_length=SUBJECT_HARD_LIMIT)
    premise: str = Field(min_length=1, max_length=PREMISE_HARD_LIMIT)
    entities: list[str] = Field(default_factory=list, max_length=12)
    location: str | None = Field(default=None, max_length=80)
    deadline: str | None = Field(default=None, max_length=80)


class ContinuityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    kind: ContinuityKind
    status: ContinuityStatus = "active"
    subject: str = Field(min_length=1, max_length=SUBJECT_SOFT_LIMIT)
    premise: str = Field(min_length=1, max_length=PREMISE_SOFT_LIMIT)
    current_state: str | None = Field(
        default=None,
        min_length=1,
        max_length=CURRENT_STATE_MAX_CHARS,
    )
    # Creation/update drafts are bounded per operation, but a long-lived item
    # may accumulate more entities through monotonic lifecycle updates.
    entities: list[str] = Field(default_factory=list)
    location: str | None = Field(default=None, max_length=80)
    deadline: str | None = Field(default=None, max_length=80)
    created_turn: int = Field(ge=0)
    updated_turn: int = Field(ge=0)
    closed_turn: int | None = Field(default=None, ge=0)


class ContinuityUpdateDraft(BaseModel):
    """Full mutable metadata, one delta, and its replacement snapshot."""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=1, max_length=SUBJECT_SOFT_LIMIT)
    state_delta: str = Field(min_length=1, max_length=STATE_DELTA_SOFT_LIMIT)
    new_current_state: str = Field(
        min_length=1,
        max_length=CURRENT_STATE_MAX_CHARS,
    )
    entities: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="本轮新增的实体；持久化时与旧实体做保序并集。",
    )
    location: str | None = Field(
        default=None,
        max_length=80,
        description="本轮确认的新地点；null 表示地点没有更新。",
    )
    deadline: str | None = Field(
        default=None,
        max_length=80,
        description="本轮确认的新期限；null 表示期限没有更新。",
    )


class _OperationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=REASON_SOFT_LIMIT)

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value


class _OperationBaseHard(BaseModel):
    """prompt 展示 / 第一轮硬校验用的 operation base（reason 硬上限 160）。"""

    model_config = ConfigDict(extra="forbid")

    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=REASON_HARD_LIMIT)

    @field_validator("evidence_ids")
    @classmethod
    def _unique_evidence_ids(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("evidence_ids must not contain duplicates")
        return value


class ContinuityCreateOperation(_OperationBase):
    operation: Literal["create"]
    item: ContinuityItemDraft


class ContinuityUpdateOperation(_OperationBase):
    operation: Literal["update"]
    item_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=SUBJECT_SOFT_LIMIT)
    state_delta: str = Field(min_length=1, max_length=STATE_DELTA_SOFT_LIMIT)
    new_current_state: str = Field(
        min_length=1,
        max_length=CURRENT_STATE_MAX_CHARS,
    )
    entities: list[str] = Field(
        default_factory=list,
        max_length=12,
        description=(
            "本轮新确认、需要加入事项索引的实体；只需输出新增项，"
            "持久化时会与旧实体合并，不能删除旧实体。"
        ),
    )
    location: str | None = Field(
        default=None,
        max_length=80,
        description="本轮确认的新地点；null 表示地点没有更新。",
    )
    deadline: str | None = Field(
        default=None,
        max_length=80,
        description="本轮确认的新期限；null 表示期限没有更新。",
    )

    def to_draft(self) -> ContinuityUpdateDraft:
        return ContinuityUpdateDraft.model_validate(self.model_dump(
            include={
                "subject",
                "state_delta",
                "new_current_state",
                "entities",
                "location",
                "deadline",
            }
        ))


class ContinuityResolveOperation(_OperationBase):
    operation: Literal["resolve"]
    item_id: str = Field(min_length=1)


class ContinuityCancelOperation(_OperationBase):
    operation: Literal["cancel"]
    item_id: str = Field(min_length=1)


ContinuityOperation = Annotated[
    ContinuityCreateOperation
    | ContinuityUpdateOperation
    | ContinuityResolveOperation
    | ContinuityCancelOperation,
    Field(discriminator="operation"),
]


class ContinuityCreateOperationHard(_OperationBaseHard):
    operation: Literal["create"]
    item: ContinuityItemDraftHard


class ContinuityUpdateOperationHard(_OperationBaseHard):
    operation: Literal["update"]
    item_id: str = Field(min_length=1)
    subject: str = Field(min_length=1, max_length=SUBJECT_HARD_LIMIT)
    state_delta: str = Field(min_length=1, max_length=STATE_DELTA_HARD_LIMIT)
    new_current_state: str = Field(
        min_length=1,
        max_length=CURRENT_STATE_HARD_LIMIT,
    )
    entities: list[str] = Field(default_factory=list, max_length=12)
    location: str | None = Field(default=None, max_length=80)
    deadline: str | None = Field(default=None, max_length=80)


class ContinuityResolveOperationHard(_OperationBaseHard):
    operation: Literal["resolve"]
    item_id: str = Field(min_length=1)


class ContinuityCancelOperationHard(_OperationBaseHard):
    operation: Literal["cancel"]
    item_id: str = Field(min_length=1)


ContinuityOperationHard = Annotated[
    ContinuityCreateOperationHard
    | ContinuityUpdateOperationHard
    | ContinuityResolveOperationHard
    | ContinuityCancelOperationHard,
    Field(discriminator="operation"),
]


class ContinuityProjectionOutput(RootModel[list[ContinuityOperationHard]]):
    """prompt 展示 / 第一轮硬校验用的 operation 数组 schema。"""


class ContinuityProjectionOutputLoose(RootModel[list[Any]]):
    """Gateway 宽松 wire：顶层仍是数组，元素由 output_validator 逐条解析。"""


class ContinuityProjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operations: list[ContinuityOperation] = Field(default_factory=list)


class ContinuityApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created_ids: list[str] = Field(default_factory=list)
    updated_ids: list[str] = Field(default_factory=list)
    resolved_ids: list[str] = Field(default_factory=list)
    cancelled_ids: list[str] = Field(default_factory=list)
    duplicate_creates_skipped: int = Field(default=0, ge=0)


class ContinuityLogEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: int = Field(ge=0)
    evidence_id: str = Field(min_length=1)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)


class ContinuityLifecycleLog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    log_id: int = Field(ge=1)
    item_id: str = Field(min_length=1)
    operation: ContinuityOperationType
    turn_index: int = Field(ge=0)
    state_delta: str | None = Field(default=None, max_length=STATE_DELTA_SOFT_LIMIT)
    reason: str = Field(min_length=1, max_length=REASON_SOFT_LIMIT)
    migration_created: bool = False
    evidence: list[ContinuityLogEvidence] = Field(default_factory=list)


class ContinuityHistoryLink(BaseModel):
    """A structural pointer into Confirmed History, never a fact source.

    The node-specific semantic basis is derived from the persisted lifecycle
    log and the immutable item fields at bridge time. It lets the retriever
    judge node -> evidence support per link instead of trusting the whole
    evidence group of a matched item.
    """

    model_config = ConfigDict(extra="forbid")

    continuity_id: str = Field(min_length=1)
    continuity_status: ContinuityStatus
    operation: ContinuityOperationType
    evidence_id: str = Field(min_length=1)
    evidence_hash: str | None = Field(default=None, min_length=64, max_length=64)
    triggering_intent_ids: list[str] = Field(min_length=1)
    match_reasons: list[ContinuityRelationReason] = Field(min_length=1)
    intent_match_reasons: dict[str, list[ContinuityRelationReason]] = Field(
        default_factory=dict
    )
    eligibility_reasons: list[ContinuityRelationReason] = Field(
        default_factory=list
    )
    evidence_position: int = Field(ge=0)
    evidence_basis_primary: str | None = Field(default=None, max_length=640)
    evidence_basis_context: str | None = Field(default=None, max_length=900)
    basis_complete: bool = True


class ContinuityHistoryBridgeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    links: list[ContinuityHistoryLink] = Field(default_factory=list)
    matched_continuity_ids: list[str] = Field(default_factory=list)
    matched_active_continuities: int = Field(default=0, ge=0)
    matched_archived_continuities: int = Field(default=0, ge=0)


class ContinuityMatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item: ContinuityItem
    matched_entities: list[str] = Field(default_factory=list)
    matched_location: bool = False
    matched_deadline: bool = False
    score: float = Field(ge=0)


class ContinuitySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ContinuityMatch] = Field(default_factory=list)
    active_count: int = Field(default=0, ge=0)
