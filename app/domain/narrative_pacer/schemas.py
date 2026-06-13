from typing import Literal

from pydantic import BaseModel, Field, model_validator


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


class NarrativePacingDecision(BaseModel):
    selected_type: NarrativeType
    narrative_goal: str

    @model_validator(mode="after")
    def _validate_goal_sentence_count(self):
        goal = self.narrative_goal.strip()
        if not goal:
            raise ValueError("narrative_goal must not be empty")
        separators = ["。", "！", "？", ".", "!", "?"]
        sentence_count = sum(goal.count(sep) for sep in separators)
        if sentence_count > 2:
            raise ValueError("narrative_goal should be 1-2 sentences")
        self.narrative_goal = goal
        return self


class NarrativePacingResult(BaseModel):
    available_types: list[NarrativeType] = Field(default_factory=list)
    selected_type: NarrativeType
    narrative_goal: str
    previous_types: list[NarrativeType] = Field(default_factory=list)
    roll_filter_notes: list[str] = Field(default_factory=list)
    cooldown_filter_notes: list[str] = Field(default_factory=list)
