from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.current_state.schemas import InventoryItem


class OpeningLocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=600)

    @field_validator("name", "description")
    @classmethod
    def normalize_required_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean


class InitialResources(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inventory: list[InventoryItem] = Field(default_factory=list, max_length=6)
    money: int | None = Field(default=None, ge=0)
    fixed_assets: list[str] = Field(default_factory=list, max_length=3)

    @field_validator("fixed_assets")
    @classmethod
    def normalize_fixed_assets(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(
            value.strip() for value in values if value.strip()
        ))


class OpeningFoundation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    starting_location: OpeningLocation
    protagonist_opening_situation: str = Field(min_length=1, max_length=800)
    initial_resources: InitialResources

    @field_validator("protagonist_opening_situation")
    @classmethod
    def normalize_situation(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean
