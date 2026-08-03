from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SettingKind = Literal["faction", "location", "item", "lore"]
SettingFact = Annotated[str, Field(min_length=1, max_length=100)]
SettingID = Annotated[str, Field(min_length=1, max_length=100)]


def _new_setting_id() -> str:
    return f"setting_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingCreateOutput(BaseModel):
    """Creative output produced by the dedicated Setting creator."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=100)
    public_facts: list[SettingFact] = Field(default_factory=list, max_length=5)
    hidden_facts: list[SettingFact] = Field(default_factory=list, max_length=3)

    @field_validator("public_facts", "hidden_facts")
    @classmethod
    def _clean_facts(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("setting facts must be unique")
        return cleaned

    @model_validator(mode="after")
    def _require_concrete_facts(self):
        if not self.public_facts and not self.hidden_facts:
            raise ValueError("setting requires at least one concrete fact")
        overlap = set(self.public_facts) & set(self.hidden_facts)
        if overlap:
            raise ValueError("a setting fact cannot be both public and hidden")
        return self


class WorldSetting(BaseModel):
    """A static, Director-created non-character world asset."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    setting_id: SettingID = Field(default_factory=_new_setting_id)
    kind: SettingKind
    name: str = Field(min_length=1, max_length=10)
    summary: str = Field(min_length=1, max_length=100)
    public_facts: list[SettingFact] = Field(default_factory=list, max_length=5)
    hidden_facts: list[SettingFact] = Field(default_factory=list, max_length=3)
    related_setting_ids: list[SettingID] = Field(default_factory=list, max_length=6)
    created_turn: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    @field_validator("public_facts", "hidden_facts", "related_setting_ids")
    @classmethod
    def _unique_values(cls, values: list[str]) -> list[str]:
        cleaned = [str(value).strip() for value in values if str(value).strip()]
        if len(cleaned) != len(set(cleaned)):
            raise ValueError("setting list values must be unique")
        return cleaned

    @model_validator(mode="after")
    def _validate_setting(self):
        if not self.public_facts and not self.hidden_facts:
            raise ValueError("setting requires at least one concrete fact")
        if set(self.public_facts) & set(self.hidden_facts):
            raise ValueError("a setting fact cannot be both public and hidden")
        if self.setting_id in self.related_setting_ids:
            raise ValueError("setting cannot relate to itself")
        return self

    def director_view(self) -> dict:
        return self.model_dump(
            mode="json",
            exclude={"created_at", "updated_at"},
        )

    def reference_view(self) -> dict:
        """Compact identity used for Director selection and asset creation."""
        return self.model_dump(
            mode="json",
            include={
                "setting_id",
                "kind",
                "name",
                "summary",
            },
        )

    def dedup_view(self) -> dict:
        """Name-level identity used by the Setting creator for deduplication."""
        return self.model_dump(
            mode="json",
            include={
                "kind",
                "name",
                "summary",
            },
        )

    def public_view(self) -> dict:
        return self.model_dump(
            mode="json",
            include={
                "setting_id",
                "kind",
                "name",
                "summary",
                "public_facts",
                "related_setting_ids",
            },
        )
