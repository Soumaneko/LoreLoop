from pydantic import BaseModel, Field


class SceneState(BaseModel):
    scene_id: str
    location: str
    time_label: str
    participants: list[str] = Field(default_factory=list)
    atmosphere: str | None = None


class PlayerState(BaseModel):
    name: str
    condition: list[str] = Field(default_factory=list)
    position: str | None = None
    visible_status: str | None = None
    intent: str | None = None


class NPCState(BaseModel):
    name: str
    role: str | None = None
    location: str | None = None
    condition: list[str] = Field(default_factory=list)
    attitude_to_player: str | None = None
    current_intent: str | None = None


class ResourcesState(BaseModel):
    inventory: list[str] = Field(default_factory=list)
    money: int | None = None
    key_items: list[str] = Field(default_factory=list)
    fixed_assets: list[str] = Field(default_factory=list)
    temporary_assets: list[str] = Field(default_factory=list)


class CurrentState(BaseModel):
    scene: SceneState
    player: PlayerState
    npcs: list[NPCState] = Field(default_factory=list)
    resources: ResourcesState = Field(default_factory=ResourcesState)
