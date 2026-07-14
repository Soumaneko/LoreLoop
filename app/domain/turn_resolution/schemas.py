from typing import Literal

from pydantic import BaseModel, Field, model_validator


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
    intended_goal: str | None = None
    resolution_source: ResolutionSource
    dice: str | None = None
    roll_value: int | None = None
    raw_value: int | None = None
    dice_sides: int | None = None
    outcome_band: OutcomeBand
    risk_level: RiskLevel = "medium"


class ActionResolutionFrame(BaseModel):
    action_id: str
    sequence: int
    actor: list[str]
    action_type: str
    outcome_band: OutcomeBand
    effect_reasoning: str = Field(min_length=1, max_length=120)
    resolved_fact: str = Field(min_length=1, max_length=120)


class TurnResolutionResult(BaseModel):
    resolution_reasoning: str = ""
    action_resolutions: list[ActionResolutionFrame] = Field(default_factory=list)
    total_resolutions: int = 0

    @model_validator(mode="after")
    def _fill_total(self):
        self.total_resolutions = len(self.action_resolutions)
        return self
