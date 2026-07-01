from pydantic import BaseModel, Field


class NextActionDirection(BaseModel):
    direction: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    anchors: list[str] = Field(default_factory=list)


class SceneWriterResult(BaseModel):
    scene_reasoning: str = ""
    scene_text: str
    next_action_directions: list[NextActionDirection] = Field(default_factory=list, max_length=5)
