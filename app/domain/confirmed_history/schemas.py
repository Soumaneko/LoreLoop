from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


HistorySource = Literal["scene_paragraph", "player_input"]


class HistoryDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    source_type: HistorySource
    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
