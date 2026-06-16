from typing import Literal

from pydantic import BaseModel, Field, model_validator


ModifierSource = Literal["situational", "attribute"]


class RollModifier(BaseModel):
    action_id: str
    source: ModifierSource = "situational"
    label: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    value: int = Field(ge=-5, le=5)

    @model_validator(mode="after")
    def _reject_zero_value(self):
        if self.value == 0:
            raise ValueError("modifier value must be non-zero")
        return self


class RollModifierBatch(BaseModel):
    modifiers: list[RollModifier] = Field(default_factory=list)


class LLMRollModifier(BaseModel):
    action_id: str
    label: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    value: int = Field(ge=-5, le=5)

    @model_validator(mode="after")
    def _reject_zero_value(self):
        if self.value == 0:
            raise ValueError("modifier value must be non-zero")
        return self


class LLMRollModifierBatch(BaseModel):
    modifiers: list[LLMRollModifier] = Field(default_factory=list)
