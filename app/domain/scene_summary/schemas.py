from pydantic import BaseModel, Field

from app.core.ids import new_hex_id
from app.core.time import utc_now


class SceneSummary(BaseModel):
    id: str = Field(default_factory=new_hex_id)
    level: int = Field(ge=1)
    content: str = Field(min_length=1)
    scene_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    turn_start: int = Field(ge=0)
    turn_end: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)
    active: bool = True
    condensed_into: str | None = None


class SceneSummaryDraft(BaseModel):
    content: str = Field(min_length=1)
