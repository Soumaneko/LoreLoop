from typing import Literal

from pydantic import BaseModel, Field

MemoryType = Literal[
    "agreement",
    "fact",
    "trait",
    "relationship",
    "item",
    "event",
]


class Memory(BaseModel):
    type: MemoryType
    subject: str
    content: str

    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None

    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    certainty: float = Field(ge=0, le=1)
    salience: float = Field(ge=0, le=1)

    source_scene_id: str | None = None
    source_text_span: str | None = None


class MemoryExtractResult(BaseModel):
    memories: list[Memory] = Field(default_factory=list)


class MemoryRecord(BaseModel):
    id: str
    type: MemoryType
    subject: str
    content: str

    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    time_label: str | None = None

    visibility: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    certainty: float
    salience: float

    source_scene_id: str | None = None
    source_text_span: str | None = None

    created_at: str = ""
