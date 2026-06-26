import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MemoryKind = Literal[
    "commitment",
    "open_thread",
    "action_constraint",
    "resource_access",
    "relationship_state",
    "belief",
]

EvidenceMode = Literal["observed", "reported", "believed", "inferred"]

MemoryStatus = Literal["active", "superseded", "invalidated"]

ImportanceHint = Literal["low", "medium", "high"]

# Legacy type aliases remain importable during the on-disk migration window.
MemoryLayer = Literal["truth", "claim", "belief", "profile"]
MemorySubtype = Literal[
    "event", "fact", "rumor", "secret", "testimony", "belief",
    "misunderstanding", "item", "resource", "location_state",
    "relationship", "agreement", "goal", "impression", "trait",
]
ExtractorHint = Literal[
    "confirmed_event", "reported_info", "npc_belief", "state_change",
    "relationship_change", "item_change", "agreement", "possible_trait",
    "unknown",
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


def legacy_memory_kind(layer: str | None, subtype: str | None) -> MemoryKind:
    subtype = str(subtype or "")
    if subtype in {"agreement"}:
        return "commitment"
    if subtype in {"state_change"}:
        return "action_constraint"
    if subtype in {"item_change"}:
        return "resource_access"
    if subtype in {"relationship_change", "possible_trait"}:
        return "relationship_state"
    if subtype in {"reported_info", "npc_belief", "claim"}:
        return "belief"
    if subtype in {"goal", "event", "location_state"}:
        return "open_thread"
    if subtype in {"resource", "item"}:
        return "resource_access"
    if subtype in {"relationship", "impression", "trait"}:
        return "relationship_state"
    if subtype in {"belief", "misunderstanding", "rumor", "secret", "testimony"}:
        return "belief"
    if layer in {"claim", "belief"}:
        return "belief"
    return "open_thread"


def legacy_evidence_mode(layer: str | None) -> EvidenceMode:
    return {
        "truth": "observed",
        "claim": "reported",
        "belief": "believed",
        "profile": "inferred",
    }.get(str(layer or ""), "inferred")


class Memory(BaseModel):
    memory_kind: MemoryKind
    evidence_mode: EvidenceMode
    subject: str = Field(min_length=1)
    content: str = Field(min_length=1)

    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None

    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    confidence: float = Field(default=1.0, ge=0, le=1)
    salience: float = Field(default=0.5, ge=0, le=1)
    owner: str | None = None

    source_scene_id: str | None = None
    source_text_span: list[str] = Field(default_factory=list)
    source_pending_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_fields(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "memory_kind" not in data:
            data["memory_kind"] = legacy_memory_kind(
                data.get("layer"),
                data.get("subtype"),
            )
        if "evidence_mode" not in data:
            data["evidence_mode"] = legacy_evidence_mode(data.get("layer"))
        return data

    @property
    def layer(self) -> str:
        return {
            "observed": "truth",
            "reported": "claim",
            "believed": "belief",
            "inferred": "profile",
        }[self.evidence_mode]

    @property
    def subtype(self) -> str:
        return {
            "commitment": "agreement",
            "open_thread": "goal",
            "action_constraint": "fact",
            "resource_access": "resource",
            "relationship_state": "relationship",
            "belief": (
                "belief" if self.evidence_mode == "believed" else "testimony"
            ),
        }[self.memory_kind]


class MemoryRecord(Memory):
    id: str
    status: MemoryStatus = "active"
    status_reason: str | None = None
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_status(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "status" not in data:
            data["status"] = "active" if data.get("active", True) else "superseded"
        if "superseded_by" not in data:
            data["superseded_by"] = data.get("merged_into")
        return data

    @property
    def active(self) -> bool:
        return self.status == "active"

    @property
    def merged_into(self) -> str | None:
        return self.superseded_by


class PendingMemoryItem(BaseModel):
    id: str = Field(default_factory=_new_id)
    scene_id: str = ""
    turn_index: int = 0
    memory_kind: MemoryKind
    subject: str = Field(min_length=1)
    raw_text: str = Field(min_length=1)
    source_text_span: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    owner: str | None = None
    location: str | None = None
    time_label: str | None = None
    importance_hint: ImportanceHint = "medium"
    created_at: str = Field(default_factory=_utc_now)
    classified: bool = False
    discarded: bool = False
    discard_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_pending(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "memory_kind" not in data:
            data["memory_kind"] = legacy_memory_kind(
                None,
                data.get("extractor_hint"),
            )
        if not str(data.get("subject") or "").strip():
            data["subject"] = str(data.get("raw_text") or "未命名记忆")[:80]
        return data

    @field_validator("source_text_span", mode="before")
    @classmethod
    def _coerce_source_text_span(cls, value):
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value else []
        return value

    @property
    def extractor_hint(self) -> str:
        return self.memory_kind


class MemoryDraftExtractResult(BaseModel):
    items: list[PendingMemoryItem] = Field(default_factory=list)


class ClassifiedMemoryCandidate(BaseModel):
    keep: bool = True
    memory_kind: MemoryKind | None = None
    evidence_mode: EvidenceMode | None = None
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

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_candidate(cls, value):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if data.get("keep", True):
            if "memory_kind" not in data:
                data["memory_kind"] = legacy_memory_kind(
                    data.get("layer"), data.get("subtype")
                )
            if "evidence_mode" not in data:
                data["evidence_mode"] = legacy_evidence_mode(data.get("layer"))
        return data

    @model_validator(mode="after")
    def _validate_kept_candidate(self):
        if self.keep:
            missing = [
                field
                for field in ("memory_kind", "evidence_mode", "subject", "content")
                if not getattr(self, field)
            ]
            if missing:
                raise ValueError(
                    "kept memory candidate missing fields: " + ", ".join(missing)
                )
            if not self.source_pending_ids:
                raise ValueError("kept memory candidate requires source_pending_ids")
        elif not (self.discard_reason or "").strip():
            raise ValueError("discarded candidate requires discard_reason")
        return self

    def to_memory(self) -> Memory:
        if not self.keep:
            raise ValueError("discarded candidate cannot become Memory")
        return Memory(
            memory_kind=self.memory_kind,
            evidence_mode=self.evidence_mode,
            subject=self.subject,
            content=self.content,
            entities=self.entities,
            location=self.location,
            time_label=self.time_label,
            owner=self.owner,
            visibility=self.visibility,
            tags=self.tags,
            confidence=self.confidence,
            salience=self.salience,
            source_scene_id=self.source_scene_id,
            source_text_span=self.source_text_span,
            source_pending_ids=self.source_pending_ids,
        )


class MemoryBatchClassificationResult(BaseModel):
    candidates: list[ClassifiedMemoryCandidate] = Field(default_factory=list)
