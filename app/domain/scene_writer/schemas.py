from pydantic import BaseModel, Field


class SceneWriterResult(BaseModel):
    scene_text: str
    changes_summary: list[str] = Field(default_factory=list)
