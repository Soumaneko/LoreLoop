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
                id="core_player_ability",
                category="player",
                content="旁观者之瞳：主角对魔兽世界正史不知情，剧情依旧按照正史大方向发展。当主角试图强行改变重大历史时，给予厄运惩罚；当顺应历史并在私生活层面微调时，给予运势加成。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_tone",
                category="tone",
                content="叙事风格：充满戏剧张力、黑色幽默、贵族式的咏叹调，但底层逻辑必须残酷、真实，符合原著设定。",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_world_rule",
                category="core_tone",
                content="世界反馈：拒绝“龙傲天”式的无脑爽文，并增加低Roll的惩罚权重，玩家的每一个愚蠢决定都必须付出代价（扣血、丢失物品、关系破裂）",
                priority=100,
            ),
            CoreMemoryItem(
                id="core_tone",
                category="tone",
                content="剧情推进：不要过快，注重细节描写（环境、气味、微表情）。",
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
