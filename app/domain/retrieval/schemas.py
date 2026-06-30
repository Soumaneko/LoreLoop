from pydantic import BaseModel, Field

from app.domain.action.schemas import Anchor, AtomicAction


class CandidateRetrievalInput(BaseModel):
    turn_intent: str
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    top_k: int = 8


class MemoryCandidate(BaseModel):
    memory_id: str
    memory_kind: str
    evidence_mode: str
    subject: str
    content: str
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None
    visibility: list[str] = Field(default_factory=list)
    owner: str | None = None
    confidence: float = 1.0
    salience: float
    source_scene_id: str | None = None
    source_text_span: list[str] = Field(default_factory=list)
    source_pending_ids: list[str] = Field(default_factory=list)

    score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    matched_anchors: list[str] = Field(default_factory=list)
    matched_actions: list[str] = Field(default_factory=list)


class PendingMemoryNote(BaseModel):
    pending_id: str
    scene_id: str
    turn_index: int
    raw_text: str
    source_text_span: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None
    memory_kind: str
    subject: str
    owner: str | None = None
    importance_hint: str = "medium"
    score: float = 0.0
    matched_anchors: list[str] = Field(default_factory=list)
    matched_actions: list[str] = Field(default_factory=list)


class CandidateRetrievalResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    pending_notes: list[PendingMemoryNote] = Field(default_factory=list)
    total_scanned: int = 0
    total_returned: int = 0
