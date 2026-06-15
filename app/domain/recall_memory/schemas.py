import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MemoryType = Literal[
    "agreement",
    "fact",
    "trait",
    "relationship",
    "item",
    "event",
]

MemoryLayer = Literal["truth", "claim", "belief", "profile"]

MemorySubtype = Literal[
    "event",
    "fact",
    "rumor",
    "secret",
    "testimony",
    "belief",
    "misunderstanding",
    "item",
    "resource",
    "location_state",
    "relationship",
    "agreement",
    "goal",
    "impression",
    "trait",
]

ExtractorHint = Literal[
    "confirmed_event",
    "reported_info",
    "npc_belief",
    "state_change",
    "relationship_change",
    "item_change",
    "agreement",
    "possible_trait",
    "unknown",
]

ImportanceHint = Literal["low", "medium", "high"]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def _legacy_type_from_subtype(subtype: str | None, layer: str | None) -> MemoryType:
    if subtype in {"agreement"}:
        return "agreement"
    if subtype in {"item", "resource"}:
        return "item"
    if subtype in {"relationship", "impression"}:
        return "relationship"
    if subtype in {"trait"} or layer == "profile":
        return "trait"
    if subtype in {"event"}:
        return "event"
    return "fact"


class Memory(BaseModel):
    type: MemoryType | None = None
    layer: MemoryLayer = "truth"
    subtype: MemorySubtype = "fact"
    subject: str
    content: str

    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None

    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    confidence: float | None = Field(default=None, ge=0, le=1)
    certainty: float | None = Field(default=None, ge=0, le=1)
    salience: float = Field(ge=0, le=1)
    owner: str | None = None

    source_scene_id: str | None = None
    source_text_span: list[str] = Field(default_factory=list)
    source_pending_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_compat_fields(self):
        if self.type is None:
            self.type = _legacy_type_from_subtype(self.subtype, self.layer)
        if self.confidence is None and self.certainty is not None:
            self.confidence = self.certainty
        if self.certainty is None and self.confidence is not None:
            self.certainty = self.confidence
        if self.confidence is None:
            self.confidence = 1.0
        if self.certainty is None:
            self.certainty = self.confidence
        return self


class MemoryExtractResult(BaseModel):
    memories: list[Memory] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    id: str
    type: MemoryType
    layer: MemoryLayer = "truth"
    subtype: MemorySubtype = "fact"
    subject: str
    content: str

    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None

    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    confidence: float = Field(ge=0, le=1)
    certainty: float
    salience: float
    owner: str | None = None

    source_scene_id: str | None = None
    source_text_span: list[str] = Field(default_factory=list)
    source_pending_ids: list[str] = Field(default_factory=list)

    created_at: str = ""
    active: bool = True
    merged_into: str | None = None


class PendingMemoryItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    scene_id: str = ""
    turn_index: int = 0
    raw_text: str
    source_text_span: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None
    extractor_hint: ExtractorHint = "unknown"
    importance_hint: ImportanceHint = "medium"
    created_at: str = Field(default_factory=_utc_now)
    classified: bool = False
    discarded: bool = False
    discard_reason: str | None = None

    @field_validator("source_text_span", mode="before")
    @classmethod
    def _coerce_source_text_span(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        return value


class MemoryDraftExtractResult(BaseModel):
    items: list[PendingMemoryItem] = Field(default_factory=list)


class ClassifiedMemoryCandidate(BaseModel):
    keep: bool = True
    layer: MemoryLayer = "truth"
    subtype: MemorySubtype = "fact"
    subject: str = ""
    content: str = ""
    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None
    owner: str | None = None
    visibility: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    tags: list[str] = Field(default_factory=list)
    source_pending_ids: list[str] = Field(default_factory=list)
    source_scene_id: str | None = None
    source_text_span: list[str] = Field(default_factory=list)
    discard_reason: str | None = None

    def to_memory(self) -> Memory:
        return Memory(
            type=_legacy_type_from_subtype(self.subtype, self.layer),
            layer=self.layer,
            subtype=self.subtype,
            subject=self.subject,
            content=self.content,
            entities=self.entities,
            location=self.location,
            time_label=self.time_label,
            owner=self.owner,
            visibility=self.visibility,
            tags=self.tags,
            confidence=self.confidence,
            certainty=self.confidence,
            salience=self.salience,
            source_scene_id=self.source_scene_id,
            source_text_span=self.source_text_span,
            source_pending_ids=self.source_pending_ids,
        )


class MemoryBatchClassificationResult(BaseModel):
    candidates: list[ClassifiedMemoryCandidate] = Field(default_factory=list)
