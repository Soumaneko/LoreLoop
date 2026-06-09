from pydantic import BaseModel, Field


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


class RollResultBatch(BaseModel):
    dice_formula: str
    dice_sides: int
    total_rolls: int
    results: list[RollResult]
