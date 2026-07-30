from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field


HistorySource = Literal["scene_paragraph", "player_input"]


class ConfirmedScene(Protocol):
    scene_id: str
    turn_index: int
    user_input: str
    scene_text: str


class HistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_type: HistorySource
    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
