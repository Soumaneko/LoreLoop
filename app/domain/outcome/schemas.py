from typing import Literal

from pydantic import BaseModel, Field


OutcomeBand = Literal[
    "critical_failure",
    "failure",
    "partial_success",
    "success",
    "critical_success",
]

InformationPolicy = Literal[
    "none",
    "ambiguous_hint_only",
    "partial_disclosure_with_cost",
    "clear_answer_allowed",
    "clear_answer_with_advantage",
]

RiskLevel = Literal["low", "medium", "high"]


class OutcomeFrame(BaseModel):
    action_id: str
    sequence: int
    actor: str
    action_type: str

    dice: str | None = None
    roll_value: int | None = None
    dice_sides: int | None = None

    outcome_band: OutcomeBand
    required_consequence: str
    information_policy: InformationPolicy

    risk_level: RiskLevel = "medium"
    narrative_permission: list[str] = Field(default_factory=list)
    narrative_constraints: list[str] = Field(default_factory=list)


class OutcomeResolutionResult(BaseModel):
    outcomes: list[OutcomeFrame] = Field(default_factory=list)
    total_outcomes: int
