import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field


def _new_id() -> str:
    return uuid.uuid4().hex


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class NPCProfilePublicView(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_traits: list[str] = Field(default_factory=list)
    known_abilities: list[str] = Field(default_factory=list)
    faction: str | None = None
    description: str | None = None
    current_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None


class NPCProfileDirectorView(NPCProfilePublicView):
    core_motivation: str | None = None
    hidden_secret: str | None = None
    conflict_style: str | None = None
    social_style: str | None = None
    encounter_potentials: list[str] = Field(default_factory=list)
    director_notes: str | None = None
    source_director_npc_id: str | None = None
    created_turn: int | None = None
    dispatched_turn: int | None = None


class NPCProfileUpdateInput(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_traits: list[str] = Field(default_factory=list, max_length=5)
    known_abilities: list[str] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    description: str | None = None
    current_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None


class NPCProfileIndexedUpdate(BaseModel):
    npc_index: int
    profile: NPCProfileUpdateInput


class NPCProfileUpdateBatch(BaseModel):
    npcs: list[NPCProfileIndexedUpdate] = Field(default_factory=list)


class NPCAliasMatch(BaseModel):
    extracted_text: str
    matched_npc_index: int | None = None
    matched_npc_name: str
    new_alias: str | None = None


NPCSource = Literal["exact_match", "alias_match", "new"]


class RetrievedNPC(BaseModel):
    name: str
    source: NPCSource
    matched_npc_index: int | None = None
    matched_npc_name: str | None = None
    context_span: str | None = None


class NPCExtractionResult(BaseModel):
    alias_matches: list[NPCAliasMatch] = Field(default_factory=list)
    retrieved_npcs: list[RetrievedNPC] = Field(default_factory=list)


class NPCProfile(BaseModel):
    npc_id: str = Field(default_factory=_new_id)
    name: str
    aliases: list[str] = Field(default_factory=list)

    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_traits: list[str] = Field(default_factory=list)
    known_abilities: list[str] = Field(default_factory=list)
    faction: str | None = None
    description: str | None = None

    current_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None
    absent_turns: int = Field(default=0, ge=0)

    core_motivation: str | None = None
    hidden_secret: str | None = None
    conflict_style: str | None = None
    social_style: str | None = None
    encounter_potentials: list[str] = Field(default_factory=list)
    director_notes: str | None = None
    source_director_npc_id: str | None = None
    created_turn: int | None = None
    dispatched_turn: int | None = None

    source_scene_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    updated_at: str = ""
    active: bool = True
    name_hit_count: int = Field(default=1, ge=0)
    alias_hit_counts: dict[str, int] = Field(default_factory=dict)

    def update_view(self) -> NPCProfileUpdateInput:
        return NPCProfileUpdateInput(
            name=self.name,
            aliases=list(self.aliases),
            public_summary=self.public_summary,
            role=self.role,
            appearance=self.appearance,
            personality_traits=list(self.personality_traits),
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            description=self.description,
            current_location=self.current_location,
            attitude_to_player=self.attitude_to_player,
            current_intent=self.current_intent,
        )

    def public_view(self) -> NPCProfilePublicView:
        return NPCProfilePublicView(
            **self.update_view().model_dump(mode="json"),
        )

    def director_view(self) -> NPCProfileDirectorView:
        return NPCProfileDirectorView(
            **self.public_view().model_dump(mode="json"),
            core_motivation=self.core_motivation,
            hidden_secret=self.hidden_secret,
            conflict_style=self.conflict_style,
            social_style=self.social_style,
            encounter_potentials=list(self.encounter_potentials),
            director_notes=self.director_notes,
            source_director_npc_id=self.source_director_npc_id,
            created_turn=self.created_turn,
            dispatched_turn=self.dispatched_turn,
        )


def profile_from_update(
    profile: NPCProfileUpdateInput,
    *,
    npc_id: str | None = None,
    source_scene_ids: list[str] | None = None,
    confidence: float = 0.5,
    updated_at: str | None = None,
    active: bool = True,
    name_hit_count: int = 1,
    alias_hit_counts: dict[str, int] | None = None,
) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id or _new_id(),
        name=profile.name,
        aliases=list(profile.aliases),
        public_summary=profile.public_summary,
        role=profile.role,
        appearance=profile.appearance,
        personality_traits=list(profile.personality_traits),
        known_abilities=list(profile.known_abilities),
        faction=profile.faction,
        description=profile.description,
        current_location=profile.current_location,
        attitude_to_player=profile.attitude_to_player,
        current_intent=profile.current_intent,
        source_scene_ids=source_scene_ids or [],
        confidence=confidence,
        updated_at=updated_at or utc_now(),
        active=active,
        name_hit_count=name_hit_count,
        alias_hit_counts=dict(alias_hit_counts or {}),
    )
