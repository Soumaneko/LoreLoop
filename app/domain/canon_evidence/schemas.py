from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CanonEvidenceSource = Literal["scene_paragraph", "player_input"]


class CanonEvidenceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_type: CanonEvidenceSource
    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)


class CanonEvidenceHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source_type: CanonEvidenceSource
    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    context_before: str | None = None
    context_after: str | None = None
    matched_entities: list[str] = Field(default_factory=list)
    matched_terms: list[str] = Field(default_factory=list)
    retrieval_channels: list[str] = Field(default_factory=list)
    score: float = Field(ge=0)


class CanonQueryEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query_id: str = Field(min_length=1)
    claim: str = Field(min_length=1)
    evidence: list[CanonEvidenceHit] = Field(default_factory=list)


class CanonEvidenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[CanonQueryEvidence] = Field(default_factory=list)
    indexed_documents: int = Field(default=0, ge=0)
    indexed_turns: int = Field(default=0, ge=0)
