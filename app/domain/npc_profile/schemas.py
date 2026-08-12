from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)

from app.core.ids import new_hex_id
from app.core.time import utc_now


NPCDisclosureLevel = Literal["remote", "face_to_face", "intimate"]

NPC_DISCLOSURE_ORDER: dict[NPCDisclosureLevel, int] = {
    "remote": 0,
    "face_to_face": 1,
    "intimate": 2,
}

_DISCLOSURE_IDENTITY_FIELDS = frozenset(
    {"name", "role", "aliases", "faction", "public_summary"}
)
_DISCLOSURE_REMOTE_FIELDS = _DISCLOSURE_IDENTITY_FIELDS | frozenset(
    {"social_style", "attitude_to_player"}
)
_DISCLOSURE_FACE_TO_FACE_FIELDS = _DISCLOSURE_REMOTE_FIELDS | frozenset(
    {"appearance", "personality_trait", "conflict_style", "known_abilities"}
)


class KnownAbility(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=20)
    description: str = Field(default="", max_length=120)


class LLMKnownAbility(BaseModel):
    """LLM-output ability: description is required and shows up in the JSON Schema.

    Distinct from the persistent :class:`KnownAbility` so the player editor may
    save empty descriptions while generated NPCs always carry one.
    """

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=120)

#: disclosure_level -> 该级允许可见的字段集合；None 表示全量（intimate）。
DISCLOSED_FIELD_SETS: dict[NPCDisclosureLevel, frozenset[str] | None] = {
    "remote": _DISCLOSURE_REMOTE_FIELDS,
    "face_to_face": _DISCLOSURE_FACE_TO_FACE_FIELDS,
    "intimate": None,
}

class NPCProfilePublicView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(default=None, max_length=40)
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None


class NPCProfileDirectorView(NPCProfilePublicView):
    disclosure_level: NPCDisclosureLevel | None = None
    core_motivation: str | None = None
    hidden_secret: str | None = None
    conflict_style: str | None = None
    social_style: str | None = None
    director_notes: str | None = None
    source_director_npc_id: str | None = None
    source_setting_ids: list[str] = Field(default_factory=list)
    created_turn: int | None = None
    introduced_turn: int | None = None


class NPCProfileUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(default=None, max_length=40)
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None

    @field_validator("personality_trait", mode="before")
    @classmethod
    def _trait_empty_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value




class NPCProfileBackfillIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None


class NPCProfileBackfillBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    personality_trait: str = Field(min_length=1, max_length=40)


class NPCProfileBackfillShell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    known_abilities: list[LLMKnownAbility] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)


class NPCProfileBackfillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    identity: NPCProfileBackfillIdentity
    behavior: NPCProfileBackfillBehavior
    shell: NPCProfileBackfillShell


class NPCProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(default_factory=new_hex_id)
    name: str
    aliases: list[str] = Field(default_factory=list)
    transient_aliases: dict[str, int] = Field(default_factory=dict)

    public_summary: str | None = None
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(default=None, max_length=40)
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None
    current_intent: str | None = None

    core_motivation: str | None = None
    hidden_secret: str | None = None
    conflict_style: str | None = None
    social_style: str | None = None
    director_notes: str | None = None
    source_director_npc_id: str | None = None
    source_setting_ids: list[str] = Field(default_factory=list)
    created_turn: int | None = None
    introduced_turn: int | None = None
    disclosure_level: NPCDisclosureLevel = "remote"

    source_scene_ids: list[str] = Field(default_factory=list)
    updated_at: str = ""
    active: bool = True
    name_hit_count: int = Field(default=0, ge=0)
    alias_hit_counts: dict[str, int] = Field(default_factory=dict)

    def update_view(self) -> NPCProfileUpdateInput:
        return NPCProfileUpdateInput(
            name=self.name,
            aliases=list(self.aliases),
            public_summary=self.public_summary,
            role=self.role,
            appearance=self.appearance,
            personality_trait=self.personality_trait,
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            last_known_location=self.last_known_location,
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
            disclosure_level=self.disclosure_level,
            core_motivation=self.core_motivation,
            hidden_secret=self.hidden_secret,
            conflict_style=self.conflict_style,
            social_style=self.social_style,
            director_notes=self.director_notes,
            source_director_npc_id=self.source_director_npc_id,
            source_setting_ids=list(self.source_setting_ids),
            created_turn=self.created_turn,
            introduced_turn=self.introduced_turn,
        )

    def disclosure_view(
        self,
        level: NPCDisclosureLevel | None = None,
    ) -> NPCProfileDirectorView:
        """Render the profile at its disclosure level (or an explicit level).

        Only fields granted by ``disclosure_level`` are exposed; everything
        above it is dropped so player-facing modules never see hidden info.
        ``disclosure_level`` itself is director meta and never carried here.
        """
        data = self.director_view().model_dump(mode="json")
        allowed = DISCLOSED_FIELD_SETS[level or self.disclosure_level]
        if allowed is not None:
            data = {
                key: value
                for key, value in data.items()
                if key in allowed
            }
        data.pop("disclosure_level", None)
        return NPCProfileDirectorView.model_validate(data)


def profile_from_update(
    profile: NPCProfileUpdateInput,
    *,
    npc_id: str | None = None,
    source_scene_ids: list[str] | None = None,
    updated_at: str | None = None,
    active: bool = True,
    name_hit_count: int = 0,
    alias_hit_counts: dict[str, int] | None = None,
) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id or new_hex_id(),
        name=profile.name,
        aliases=list(profile.aliases),
        public_summary=profile.public_summary,
        role=profile.role,
        appearance=profile.appearance,
        personality_trait=profile.personality_trait,
        known_abilities=list(profile.known_abilities),
        faction=profile.faction,
        last_known_location=profile.last_known_location,
        attitude_to_player=profile.attitude_to_player,
        current_intent=profile.current_intent,
        source_scene_ids=source_scene_ids or [],
        updated_at=updated_at or utc_now(),
        active=active,
        name_hit_count=name_hit_count,
        alias_hit_counts=dict(alias_hit_counts or {}),
    )
