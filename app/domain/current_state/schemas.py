from typing import Literal

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


class SceneState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: str
    location: str
    participants: list[ParticipantState] = Field(default_factory=list)

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


class FinalSceneFrame(BaseModel):
    """Scene-ending snapshot produced by the LLM; compiled deterministically.

    The LLM only audits the spatio-temporal flow of the confirmed prose and
    freezes the final frame: where the protagonist ends up, the absolute
    day/phase at the end, and who is physically present. All set arithmetic
    (participant diff, relocation eviction, clock phase steps) is computed by
    the deterministic engine.
    """

    model_config = ConfigDict(extra="forbid")

    final_location: str | None = Field(default=None, min_length=1)
    day_offset: int = Field(default=0, ge=0)
    final_phase: WorldPhase | None = None
    active_present_npcs: list[str] = Field(default_factory=list)
    departed_npcs: list[str] = Field(default_factory=list)
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "final_location",
        "active_present_npcs",
        "departed_npcs",
        mode="before",
    )
    @classmethod
    def _strip_names(cls, value):
        if isinstance(value, str):
            return value.strip() or None
        if isinstance(value, list):
            # Only strip strings; non-string items pass through untouched so
            # Pydantic's own type validation rejects them as a normal
            # (recoverable) ValidationError instead of crashing here.
            return [
                item.strip() if isinstance(item, str) else item
                for item in value
                if not isinstance(item, str) or item.strip()
            ]
        return value
