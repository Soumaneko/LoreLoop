from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


CoreMemoryKind = Literal[
    "world_invariant",
    "protagonist_invariant",
    "narrative_preference",
    "system_convention",
    "ability_rule",
]
CoreMemoryStrength = Literal[
    "hard_constraint",
    "strong_preference",
]
CoreMemorySource = Literal["player", "campaign_setup"]


class CoreMemoryItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    kind: CoreMemoryKind
    strength: CoreMemoryStrength
    source: CoreMemorySource
    content: str = Field(min_length=1, max_length=2000)

    @field_validator("id", "content")
    @classmethod
    def strip_text(cls, value: str) -> str:
        clean = value.strip()
        if not clean:
            raise ValueError("must not be blank")
        return clean


class CoreMemory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    items: list[CoreMemoryItem]

    def without_protagonist(self) -> "CoreMemory":
        """Projection that excludes protagonist profile and backstory items."""
        return CoreMemory(
            items=[
                item
                for item in self.items
                if item.kind != "protagonist_invariant"
            ]
        )
