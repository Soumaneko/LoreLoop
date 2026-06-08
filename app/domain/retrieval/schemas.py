from pydantic import BaseModel, Field

from app.domain.action.schemas import Anchor, AtomicAction


class CandidateRetrievalInput(BaseModel):
    turn_intent: str
    atomic_actions: list[AtomicAction] = Field(default_factory=list)
    anchors: list[Anchor] = Field(default_factory=list)
    top_k: int = 20


class MemoryCandidate(BaseModel):
    memory_id: str
    memory_type: str
    subject: str
    content: str
    entities: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None
    salience: float
    certainty: float

    score: float
    score_breakdown: dict[str, float] = Field(default_factory=dict)
    matched_anchors: list[str] = Field(default_factory=list)
    matched_actions: list[str] = Field(default_factory=list)


class CandidateRetrievalResult(BaseModel):
    candidates: list[MemoryCandidate] = Field(default_factory=list)
    total_scanned: int = 0
    total_returned: int = 0
