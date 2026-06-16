from pydantic import BaseModel, Field, model_validator

from app.domain.roll_modifier.schemas import RollModifier


class RollConfig(BaseModel):
    dice_sides: int = Field(default=20, ge=2, le=1000)
    roll_only_when_needed: bool = True
    min_rolls: int = Field(default=2, ge=0)
    seed: int | None = None


class RollRequest(BaseModel):
    action_id: str
    sequence: int
    actor: str
    action_type: str
    roll_hint: str | None = None


class RollResult(BaseModel):
    action_id: str
    sequence: int
    actor: str
    action_type: str
    dice: str
    value: int
    dice_sides: int
    roll_hint: str | None = None
    raw_value: int | None = None
    modifiers: list[RollModifier] = Field(default_factory=list)

    @model_validator(mode="after")
    def _fill_raw_value(self):
        if self.raw_value is None:
            self.raw_value = self.value
        return self


class RollResultBatch(BaseModel):
    dice_formula: str
    dice_sides: int
    total_rolls: int
    results: list[RollResult]
