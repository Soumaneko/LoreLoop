from typing import Literal

from pydantic import BaseModel, Field


OutcomeBand = Literal[
    "critical_failure",
    "failure",
    "partial_success",
    "success",
    "critical_success",
]

RiskLevel = Literal["low", "medium", "high"]
ResolutionSource = Literal["automatic", "roll"]


class ActionJudgment(BaseModel):
    action_id: str
    sequence: int
    actor: list[str]
    action_type: str
    action_text: str
    resolution_source: ResolutionSource
    dice: str | None = None
    roll_value: int | None = None
    raw_value: int | None = None
    dice_sides: int | None = None
    outcome_band: OutcomeBand
    risk_level: RiskLevel = "medium"


class PlayerRollModifier(BaseModel):
    label: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    value: int


class PlayerActionResolution(BaseModel):
    action_text: str = Field(min_length=1)
    resolution_source: ResolutionSource
    outcome_band: OutcomeBand
    dice: str | None = None
    raw_values: list[int] = Field(default_factory=list)
    modifiers: list[PlayerRollModifier] = Field(default_factory=list)
    modifier_total: int = 0
    final_value: int | None = None
    dice_sides: int | None = None


class PlayerTurnResolution(BaseModel):
    actions: list[PlayerActionResolution] = Field(default_factory=list)
