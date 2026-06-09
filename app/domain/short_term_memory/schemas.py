from pydantic import BaseModel, Field


class ShortTermScene(BaseModel):
    scene_id: str
    turn_index: int
    text: str
    created_at: str | None = None


class ShortTermMemory(BaseModel):
    recent_scenes: list[ShortTermScene] = Field(default_factory=list)
    max_items: int = Field(default=5, ge=1)


class ShortTermMemoryStore:
    def __init__(self, max_items: int = 5):
        if max_items < 1:
            raise ValueError("max_items must be >= 1")
        self.max_items = max_items
        self.recent_scenes: list[ShortTermScene] = []

    def add_scene(self, scene: ShortTermScene) -> None:
        self.recent_scenes.append(scene)
        self.recent_scenes = self.recent_scenes[-self.max_items:]

    def get_recent(self) -> ShortTermMemory:
        return ShortTermMemory(
            recent_scenes=[scene.model_copy(deep=True) for scene in self.recent_scenes],
            max_items=self.max_items,
        )
