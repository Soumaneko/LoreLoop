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
                content="主角是卢西安，一名风流的人类法师。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_tone",
                category="tone",
                content="剧情风格应保持文字冒险剧情风格，重视行动后果、人物反应和世界连续性。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_world_rule",
                category="world",
                content="当前世界是经典旧世时代的艾泽拉斯，不应该出现违背世界设定的情节。",
                priority=100,
            ),
        ]
    )
