from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


SettingKind = Literal[
    "place",
    "organization",
    "historical_event",
    "institution_or_custom",
    "phenomenon_or_rule",
    "knowledge_or_tradition",
    "resource_or_network",
]
SettingVisibility = Literal["player_known", "director_private", "mixed"]
SettingMaturity = Literal["seed", "usable"]
SettingAvailability = Literal["available", "reserved", "consumed", "rejected"]
SettingSeedResolution = Literal["new", "existing", "ignore"]
SettingPreparationDecision = Literal["use_as_is", "develop", "reject"]


def _new_setting_id() -> str:
    return f"setting_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SettingSourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str = Field(min_length=1)
    source_type: Literal[
        "core_memory",
        "scene_text",
        "npc_profile_public",
        "npc_profile_private",
    ]
    source_ref: str = Field(min_length=1)
    visibility: Literal["player_known", "director_private"]
    content: str = Field(min_length=1)


class SettingSeedCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1)
    kind: SettingKind
    basic_concept: str = Field(min_length=1)
    source_key: str = Field(min_length=1)
    source_span: str = Field(min_length=1)
    resolution: SettingSeedResolution
    matched_setting_id: str | None = None
    related_setting_ids: list[str] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _validate_resolution(self):
        if self.resolution == "existing" and not self.matched_setting_id:
            raise ValueError("matched_setting_id is required for existing resolution")
        if self.resolution != "existing" and self.matched_setting_id is not None:
            raise ValueError(
                "matched_setting_id is only valid for existing resolution"
            )
        return self


class SettingSeedExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[SettingSeedCandidate] = Field(default_factory=list, max_length=8)


class WorldSettingSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_key: str
    source_type: str
    source_ref: str
    source_turn: int = Field(ge=0)
    source_span: str
    visibility: Literal["player_known", "director_private"]


class SettingAnchorPacket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str
    label: str
    kind: SettingKind
    definition: str = Field(min_length=1)
    setting_facts: list[str] = Field(default_factory=list, max_length=16)
    relation_to_known_world: list[str] = Field(default_factory=list, max_length=5)
    world_effects: list[str] = Field(
        default_factory=list,
        max_length=5,
        description=(
            "How this concept affects the world, including social systems, "
            "places, institutions, resources, risks, knowledge, or daily life."
        ),
    )
    scope_and_boundaries: list[str] = Field(default_factory=list, max_length=5)
    player_known_facts: list[int] = Field(default_factory=list, max_length=16)
    director_private_facts: list[int] = Field(default_factory=list, max_length=16)


class SettingAnchorPreparation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str
    decision: SettingPreparationDecision
    reason: str = Field(min_length=1)
    anchor: SettingAnchorPacket | None = None

    @model_validator(mode="after")
    def _validate_anchor(self):
        if self.decision == "reject":
            if self.anchor is not None:
                raise ValueError("rejected setting must not include an anchor")
            return self
        if self.anchor is None:
            raise ValueError("usable setting must include an anchor")
        if self.anchor.setting_id != self.setting_id:
            raise ValueError("anchor setting_id must match preparation setting_id")
        return self


class SettingAnchorPreparationBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preparations: list[SettingAnchorPreparation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_setting_ids(self):
        setting_ids = [
            preparation.setting_id for preparation in self.preparations
        ]
        if len(setting_ids) != len(set(setting_ids)):
            raise ValueError("preparation setting_id values must be unique")
        return self


class WorldSetting(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(default_factory=_new_setting_id)
    label: str = Field(min_length=1)
    kind: SettingKind
    basic_concept: str = Field(min_length=1)
    visibility: SettingVisibility
    maturity: SettingMaturity = "seed"
    availability: SettingAvailability = "available"
    prepared_anchor: SettingAnchorPacket | None = None
    reserved_by: str | None = None
    consumed_by_director_npc_id: str | None = None
    rejection_reason: str | None = None
    related_setting_ids: list[str] = Field(default_factory=list)
    sources: list[WorldSettingSource] = Field(default_factory=list)
    created_turn: int = Field(ge=0)
    last_seen_turn: int = Field(ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)
