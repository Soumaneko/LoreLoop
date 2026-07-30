from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


ContinuityKind = Literal["commitment", "open_thread"]
ContinuityStatus = Literal["active", "resolved", "cancelled"]
ContinuityOperationType = Literal["create", "update", "resolve", "cancel"]


class ContinuityItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: ContinuityKind
    subject: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=200)
    entities: list[str] = Field(default_factory=list, max_length=12)
    location: str | None = Field(default=None, max_length=80)
    deadline: str | None = Field(default=None, max_length=80)


class ContinuityItem(ContinuityItemDraft):
    id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    status: ContinuityStatus = "active"
    created_turn: int = Field(ge=0)
    updated_turn: int = Field(ge=0)
    closed_turn: int | None = Field(default=None, ge=0)


class ContinuityOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: ContinuityOperationType
    item_id: str | None = None
    item: ContinuityItemDraft | None = None
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def _validate_shape(self):
        if self.operation == "create":
            if self.item_id is not None or self.item is None:
                raise ValueError("create requires item and forbids item_id")
        elif self.operation == "update":
            if not self.item_id or self.item is None:
                raise ValueError("update requires item_id and item")
        elif not self.item_id or self.item is not None:
            raise ValueError(
                "resolve/cancel require item_id and forbid item"
            )
        return self


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
