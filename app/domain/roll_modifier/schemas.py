from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class LLMRollModifierBatchLoose(BaseModel):
    """Gateway-only loose container for per-modifier salvage."""

    model_config = ConfigDict(extra="allow")

    modifiers: list[Any] = Field(default_factory=list)
