import hashlib
from typing import Literal

from pydantic import BaseModel, Field


class NPCUsage(BaseModel):
    name: str = Field(min_length=1, description="输入人物资料中的档案姓名")
    surfaces: list[str] = Field(min_length=1, description="正文中实际指代此人的姓名、称谓或描述")
    presence: Literal["appeared", "mentioned_only"]


class NextActionDirection(BaseModel):
    direction: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    anchors: list[str] = Field(default_factory=list)


class SceneWriterResult(BaseModel):
    scene_reasoning: str = ""
    scene_text: str
    npc_usages: list[NPCUsage] = Field(default_factory=list)
    npc_usages_text_hash: str | None = None
    closing_prompt: str = ""
    next_action_directions: list[NextActionDirection] = Field(default_factory=list, max_length=5)

    def usages_for_text(self, scene_text: str) -> list[NPCUsage]:
        if self.npc_usages_text_hash != hashlib.sha256(scene_text.encode("utf-8")).hexdigest():
            return []
        return self.npc_usages
