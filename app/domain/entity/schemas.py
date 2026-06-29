import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


EntityType = Literal["npc", "item", "location", "organization"]


def _new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class EntityProfileUpdateInput(BaseModel):
    entity_type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)

    summary: str | None = None

    role: str | None = None
    personality_traits: list[str] = Field(default_factory=list)
    known_abilities: list[str] = Field(default_factory=list)
    faction: str | None = None

    item_type: str | None = None
    item_source: str | None = None

    location_type: str | None = None
    features: list[str] = Field(default_factory=list)
    parent_location: str | None = None

    description: str | None = None


class EntityProfileUpdateBatch(BaseModel):
    entities: list["EntityProfileIndexedUpdate"] = Field(default_factory=list)


class EntityProfileIndexedUpdate(BaseModel):
    entity_index: int
    profile: EntityProfileUpdateInput


class EntityAliasMatch(BaseModel):
    extracted_text: str
    matched_entity_index: int | None = None
    matched_entity_name: str
    matched_entity_type: EntityType
    new_alias: str | None = None


class RetrievedEntity(BaseModel):
    name: str
    entity_type: EntityType
    source: Literal["exact_match", "alias_match", "new"]
    matched_entity_index: int | None = None
    matched_entity_name: str | None = None
    context_span: str | None = None


class EntityExtractionResult(BaseModel):
    alias_matches: list[EntityAliasMatch] = Field(default_factory=list)
    retrieved_entities: list[RetrievedEntity] = Field(default_factory=list)


class EntityProfile(BaseModel):
    entity_id: str = Field(default_factory=_new_id)
    entity_type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)

    summary: str | None = None

    role: str | None = None
    personality_traits: list[str] = Field(default_factory=list)
    known_abilities: list[str] = Field(default_factory=list)
    faction: str | None = None

    item_type: str | None = None
    item_source: str | None = None

    location_type: str | None = None
    features: list[str] = Field(default_factory=list)
    parent_location: str | None = None

    description: str | None = None

    source_scene_ids: list[str] = Field(default_factory=list)
    source_memory_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    updated_at: str = ""
    active: bool = True
    name_hit_count: int = Field(default=1, ge=0)
    alias_hit_counts: dict[str, int] = Field(default_factory=dict)

    def semantic_view(self) -> EntityProfileUpdateInput:
        return EntityProfileUpdateInput(
            entity_type=self.entity_type,
            name=self.name,
            aliases=list(self.aliases),
            summary=self.summary,
            role=self.role,
            personality_traits=list(self.personality_traits),
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            item_type=self.item_type,
            item_source=self.item_source,
            location_type=self.location_type,
            features=list(self.features),
            parent_location=self.parent_location,
            description=self.description,
        )


def profile_from_semantic(
    profile: EntityProfileUpdateInput,
    *,
    entity_id: str | None = None,
    source_scene_ids: list[str] | None = None,
    source_memory_ids: list[str] | None = None,
    confidence: float = 0.5,
    updated_at: str | None = None,
    active: bool = True,
    name_hit_count: int = 1,
    alias_hit_counts: dict[str, int] | None = None,
) -> EntityProfile:
    return EntityProfile(
        entity_id=entity_id or _new_id(),
        entity_type=profile.entity_type,
        name=profile.name,
        aliases=list(profile.aliases),
        summary=profile.summary,
        role=profile.role,
        personality_traits=list(profile.personality_traits),
        known_abilities=list(profile.known_abilities),
        faction=profile.faction,
        item_type=profile.item_type,
        item_source=profile.item_source,
        location_type=profile.location_type,
        features=list(profile.features),
        parent_location=profile.parent_location,
        description=profile.description,
        source_scene_ids=source_scene_ids or [],
        source_memory_ids=source_memory_ids or [],
        confidence=confidence,
        updated_at=updated_at or utc_now(),
        active=active,
        name_hit_count=name_hit_count,
        alias_hit_counts=dict(alias_hit_counts or {}),
    )
