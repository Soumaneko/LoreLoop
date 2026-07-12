import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator


MemoryKind = Literal[
    "commitment",
    "open_thread",
    "action_constraint",
    "resource_access",
    "relationship_state",
]

EvidenceMode = Literal["observed", "reported", "believed", "inferred"]

MemoryStatus = Literal["active", "superseded", "invalidated"]

ImportanceHint = Literal["low", "medium", "high"]

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


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

class MemoryRecord(Memory):
    id: str
    status: MemoryStatus = "active"
    status_reason: str | None = None
    superseded_by: str | None = None
    created_at: str = ""
    updated_at: str = ""

    @property
    def active(self) -> bool:
        return self.status == "active"


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

class MemoryDraftExtractResult(BaseModel):
    items: list[PendingMemoryItem] = Field(default_factory=list)


class ClassifiedMemoryCandidate(BaseModel):
    reason: str = Field(min_length=1)
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
