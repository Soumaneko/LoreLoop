from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


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
    money_unit: str = "金币"
    key_items: list[str] = Field(default_factory=list)
    fixed_assets: list[str] = Field(default_factory=list)
    temporary_assets: list[str] = Field(default_factory=list)

    @field_validator("inventory", mode="before")
    @classmethod
    def _coerce_legacy_inventory(cls, value):
        if value is None:
            return []
        if not isinstance(value, list):
            return value
        converted = []
        for item in value:
            if isinstance(item, str):
                if item.strip():
                    converted.append({"name": item.strip(), "quantity": 1})
            else:
                converted.append(item)
        return converted


class CurrentState(BaseModel):
    scene: SceneState
    world_clock: WorldClock = Field(default_factory=WorldClock)
    player: PlayerState
    npcs: list[NPCState] = Field(default_factory=list)
    resources: ResourcesState = Field(default_factory=ResourcesState)

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy_time_label(cls, data):
        if not isinstance(data, dict):
            return data
        if data.get("world_clock") is None:
            scene = data.get("scene")
            time_label = scene.get("time_label") if isinstance(scene, dict) else None
            data["world_clock"] = _world_clock_from_legacy_label(time_label)
        scene = data.get("scene")
        if isinstance(scene, dict):
            scene.pop("time_label", None)
        return data


def _world_clock_from_legacy_label(time_label: str | None) -> dict:
    if not time_label:
        return {"day": 1, "phase": "上午"}

    day = 1
    import re

    match = re.search(r"第\s*(\d+)\s*日", time_label)
    if match:
        day = max(1, int(match.group(1)))

    for phase in PHASE_ORDER:
        if phase in time_label:
            return {"day": day, "phase": phase}
    if "清晨" in time_label or "早晨" in time_label:
        return {"day": day, "phase": "上午"}
    if "傍晚" in time_label:
        return {"day": day, "phase": "黄昏"}
    if "夜" in time_label:
        return {"day": day, "phase": "晚上"}
    return {"day": day, "phase": "上午"}
