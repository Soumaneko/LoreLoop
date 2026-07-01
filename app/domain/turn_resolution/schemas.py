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

NarrativeType = Literal[
    "advance",
    "relationship_shift",
    "reveal_information",
    "escalate_pressure",
    "complication",
    "transition",
    "respite",
]


NARRATIVE_TYPE_LABELS: dict[NarrativeType, str] = {
    "advance": "推进",
    "relationship_shift": "关系变化",
    "reveal_information": "揭示信息",
    "escalate_pressure": "升压",
    "complication": "麻烦/并发症",
    "transition": "过渡",
    "respite": "休整",
}


class RollJudgment(BaseModel):
    action_id: str
    sequence: int
    actor: str
    action_type: str
    action_text: str
    intended_goal: str | None = None
    dice: str | None = None
    roll_value: int | None = None
    raw_value: int | None = None
    dice_sides: int | None = None
    outcome_band: OutcomeBand
    risk_level: RiskLevel = "medium"


class ActionResolutionFrame(BaseModel):
    action_id: str
    sequence: int
    actor: str
    action_type: str
    outcome_band: OutcomeBand
    effect_reasoning: str = Field(min_length=1, max_length=120)
    resolved_fact: str = Field(min_length=1, max_length=120)


class TurnResolutionResult(BaseModel):
    resolution_reasoning: str = ""
    selected_type: NarrativeType
    available_types: list[NarrativeType] = Field(default_factory=list)
    previous_types: list[NarrativeType] = Field(default_factory=list)
    roll_filter_notes: list[str] = Field(default_factory=list)
    cooldown_filter_notes: list[str] = Field(default_factory=list)
    action_resolutions: list[ActionResolutionFrame] = Field(default_factory=list)
    total_resolutions: int = 0

    @model_validator(mode="after")
    def _fill_total(self):
        self.total_resolutions = len(self.action_resolutions)
        return self
