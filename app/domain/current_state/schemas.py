from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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


class TimedCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    condition: str = Field(min_length=1)
    expires_after_phases: int = Field(ge=-1)

    @field_validator("condition")
    @classmethod
    def _condition_must_not_be_blank(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("condition must not be blank")
        return clean

    @field_validator("expires_after_phases")
    @classmethod
    def _expiry_must_be_permanent_or_positive(cls, value: int) -> int:
        if value == 0:
            raise ValueError("expires_after_phases must be -1 or a positive integer")
        return value


class ParticipantState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    npc_conditions: list[TimedCondition] = Field(default_factory=list)
    attitude_to_player: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _upgrade_legacy_conditions(cls, value: Any):
        if not isinstance(value, dict):
            return value
        data = dict(value)
        legacy = data.pop("condition", [])
        raw_conditions = data.get("npc_conditions", legacy)
        if isinstance(raw_conditions, list):
            data["npc_conditions"] = [
                {
                    "condition": item,
                    "expires_after_phases": -1,
                }
                if isinstance(item, str)
                else item
                for item in raw_conditions
            ]
        return data

    @model_validator(mode="after")
    def _npc_conditions_must_be_unique(self):
        _validate_unique_conditions(self.npc_conditions, "npc_conditions")
        return self


class SceneState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    location: str
    participants: list[ParticipantState] = Field(default_factory=list)
    scene_condition: list[TimedCondition] = Field(default_factory=list)

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

    @field_validator("scene_condition", mode="before")
    @classmethod
    def _upgrade_scene_conditions(cls, value):
        if not isinstance(value, list):
            return value
        return [
            {
                "condition": item,
                "expires_after_phases": -1,
            }
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

    @model_validator(mode="after")
    def _scene_conditions_must_be_unique(self):
        _validate_unique_conditions(self.scene_condition, "scene_condition")
        return self


class PlayerState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    condition: list[str] = Field(default_factory=list)
    visible_status: str | None = None


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
                legacy = legacy_by_name.get(
                    str(participant.get("name", "")).strip().casefold()
                )
                if legacy is None:
                    continue
                participant.setdefault("condition", legacy.get("condition", []))
                participant.setdefault(
                    "attitude_to_player",
                    legacy.get("attitude_to_player"),
                )
            scene["participants"] = participant_data
            data["scene"] = scene
        return data


def _validate_unique_conditions(
    conditions: list[TimedCondition],
    label: str,
) -> None:
    values = [item.condition.casefold() for item in conditions]
    if len(values) != len(set(values)):
        raise ValueError(f"{label} values must be unique")
