from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


WorldPhase = Literal["凌晨", "清晨", "上午", "下午", "黄昏", "晚上", "深夜"]


PHASE_ORDER: list[WorldPhase] = [
    "凌晨",
    "清晨",
    "上午",
    "下午",
    "黄昏",
    "晚上",
    "深夜",
]


class WorldClock(BaseModel):
    day: int = Field(default=1, ge=1)
    phase: WorldPhase = "清晨"

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

    def has_reached(self, deadline: "WorldClock") -> bool:
        current_index = (self.day - 1) * len(PHASE_ORDER) + PHASE_ORDER.index(
            self.phase
        )
        deadline_index = (deadline.day - 1) * len(PHASE_ORDER) + PHASE_ORDER.index(
            deadline.phase
        )
        return current_index >= deadline_index


class ParticipantState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    attitude_to_player: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _discard_removed_condition_fields(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("condition", None)
        data.pop("npc_conditions", None)
        return data


class SceneState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    location: str
    participants: list[ParticipantState] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _discard_removed_condition_fields(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("scene_condition", None)
        return data

    @field_validator("participants", mode="before")
    @classmethod
    def _upgrade_participant_names(cls, value):
        if not isinstance(value, list):
            return value
        return [
            {"name": item}
            if isinstance(item, str)
            else item
            for item in value
        ]

    @property
    def participant_names(self) -> list[str]:
        return [participant.name for participant in self.participants]

    @model_validator(mode="after")
    def _participant_names_must_be_unique(self):
        names = [participant.name.strip().casefold() for participant in self.participants]
        if len(names) != len(set(names)):
            raise ValueError("participant names must be unique")
        return self

class PlayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    visible_status: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _discard_removed_condition_field(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        data.pop("condition", None)
        return data


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
    model_config = ConfigDict(extra="forbid")

    scene: SceneState
    world_clock: WorldClock = Field(default_factory=WorldClock)
    player: PlayerState
    resources: ResourcesState = Field(default_factory=ResourcesState)

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_scene_state(cls, value: Any):
        """Preserve in-scene NPC state while loading pre-participant saves."""
        if not isinstance(value, dict):
            return value

        data = dict(value)
        player_value = data.get("player")
        if isinstance(player_value, dict):
            player = dict(player_value)
            player.pop("position", None)
            player.pop("condition", None)
            data["player"] = player
        scene_value = data.get("scene")
        scene = dict(scene_value) if isinstance(scene_value, dict) else scene_value
        legacy_npcs = data.pop("npcs", [])
        if isinstance(scene, dict):
            scene.pop("atmosphere", None)
            raw_participants = scene.get("participants", [])
            participant_data = [
                {"name": item} if isinstance(item, str) else dict(item)
                for item in raw_participants
                if isinstance(item, (str, dict))
            ]
            legacy_by_name = {
                str(item.get("name", "")).strip().casefold(): item
                for item in legacy_npcs
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            }
            for participant in participant_data:
                participant.pop("condition", None)
                participant.pop("npc_conditions", None)
                legacy = legacy_by_name.get(
                    str(participant.get("name", "")).strip().casefold()
                )
                if legacy is None:
                    continue
                participant.setdefault(
                    "attitude_to_player",
                    legacy.get("attitude_to_player"),
                )
            scene.pop("scene_condition", None)
            scene["participants"] = participant_data
            data["scene"] = scene
        return data
