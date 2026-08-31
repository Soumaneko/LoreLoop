from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.roll_modifier.schemas import RollModifier

Difficulty = Literal["简单", "普通", "困难"]

DIFFICULTY_DICE_COUNT: dict[Difficulty, int] = {
    "困难": 1,
    "普通": 2,
    "简单": 3,
}


class RollConfig(BaseModel):
    dice_sides: int = Field(default=20, ge=2, le=1000)
    seed: int | None = None
    difficulty: Difficulty = "困难"


class RollRequest(BaseModel):
    action_id: str
    sequence: int
    actor: list[str]
    action_type: str
    roll_hint: str | None = None


class RollResult(BaseModel):
    action_id: str
    sequence: int
    actor: list[str]
    action_type: str
    dice: str
    value: int
    dice_sides: int
    roll_hint: str | None = None
    raw_value: int | None = None
    all_raw_values: list[int] = Field(default_factory=list)
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
