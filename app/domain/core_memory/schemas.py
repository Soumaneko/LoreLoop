from pydantic import BaseModel, Field


class CoreMemoryItem(BaseModel):
    id: str = Field(min_length=1)
    category: str = Field(min_length=1)
    content: str = Field(min_length=1)
    priority: int = Field(default=100, ge=0)


class CoreMemory(BaseModel):
    items: list[CoreMemoryItem]


def create_default_core_memory() -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(
                id="core_player_identity",
                category="player",
                content="主角是卢西安，一名偏好控制法术、善于用话术和局势控制解决问题的人类法师。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_tone",
                category="tone",
                content="剧情风格应保持文字冒险 DM 口吻，重视行动后果、人物反应和世界连续性。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_world_rule",
                category="world",
                content="世界反应应符合当前世界观，不应随意引入与已有设定冲突的新规则。",
                priority=100,
            ),
        ]
    )
