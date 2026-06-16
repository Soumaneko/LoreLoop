import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return uuid.uuid4().hex


class SceneSummary(BaseModel):
    id: str = Field(default_factory=_new_id)
    level: int = Field(ge=1)
    content: str = Field(min_length=1)
    scene_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    turn_start: int = Field(ge=0)
    turn_end: int = Field(ge=0)
    created_at: str = Field(default_factory=_utc_now)
    active: bool = True
    condensed_into: str | None = None


class SceneSummaryDraft(BaseModel):
    content: str = Field(min_length=1)
