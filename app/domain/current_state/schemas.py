from typing import Literal

from pydantic import BaseModel, Field


WorldPhase = Literal["凌晨", "上午", "下午", "黄昏", "晚上", "深夜"]


PHASE_ORDER: list[WorldPhase] = ["凌晨", "上午", "下午", "黄昏", "晚上", "深夜"]


class WorldClock(BaseModel):
    day: int = Field(default=1, ge=1)
    phase: WorldPhase = "上午"

    @property
    def label(self) -> str:
        return f"第{self.day}日{self.phase}"

    def advance_phase(self, steps: int = 1) -> "WorldClock":
        if steps < 0:
            raise ValueError("steps must be >= 0")
        index = PHASE_ORDER.index(self.phase) + steps
        day = self.day + index // len(PHASE_ORDER)
        phase = PHASE_ORDER[index % len(PHASE_ORDER)]
        return WorldClock(day=day, phase=phase)


class SceneState(BaseModel):
    scene_id: str
    location: str
    participants: list[str] = Field(default_factory=list)
    atmosphere: str | None = None


class PlayerState(BaseModel):
    name: str
    condition: list[str] = Field(default_factory=list)
    position: str | None = None
    visible_status: str | None = None


class NPCState(BaseModel):
    name: str
    role: str | None = None
    location: str | None = None
    condition: list[str] = Field(default_factory=list)
    attitude_to_player: str | None = None
    current_intent: str | None = None
    absent_turns: int = Field(default=0, ge=0)


class InventoryItem(BaseModel):
    name: str = Field(min_length=1)
    quantity: int = Field(default=1, ge=1)


class ResourcesState(BaseModel):
    inventory: list[InventoryItem] = Field(default_factory=list)
    money: int | None = None
    money_unit: str | None = None
    fixed_assets: list[str] = Field(default_factory=list)
    temporary_assets: list[str] = Field(default_factory=list)


class CurrentState(BaseModel):
    scene: SceneState
    world_clock: WorldClock = Field(default_factory=WorldClock)
    player: PlayerState
    npcs: list[NPCState] = Field(default_factory=list)
    resources: ResourcesState = Field(default_factory=ResourcesState)
