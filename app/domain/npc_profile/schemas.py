from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.core.ids import new_hex_id
from app.core.time import utc_now


NPCDisclosureLevel = Literal["remote", "face_to_face", "intimate"]

#: ``personality_trait`` 是用于快速消费的简短稳定标签，所有生成、
#: mutation 与持久化契约共用同一边界。
NPC_PERSONALITY_TRAIT_MAX_LENGTH = 40

NPC_DISCLOSURE_ORDER: dict[NPCDisclosureLevel, int] = {
    "remote": 0,
    "face_to_face": 1,
    "intimate": 2,
}

_DISCLOSURE_IDENTITY_FIELDS = frozenset(
    {
        "name",
        "role",
        "aliases",
        "faction",
        "last_scene_summary",
        "last_scene_turn",
    }
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

#: ``last_scene_summary`` 描述最近一次实际参与场景时的公开表现，程序 + evidence
#: 驱动写入，不开放人工编辑。
NPC_LAST_SCENE_SUMMARY_MAX_LENGTH = 240

class NPCProfilePublicView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    aliases: list[str] = Field(default_factory=list)
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(
        default=None,
        max_length=NPC_PERSONALITY_TRAIT_MAX_LENGTH,
    )
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None
    last_scene_summary: str | None = Field(
        default=None,
        max_length=NPC_LAST_SCENE_SUMMARY_MAX_LENGTH,
    )
    last_scene_turn: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _last_scene_fields_mutual_null(self):
        if (self.last_scene_summary is None) != (self.last_scene_turn is None):
            raise ValueError(
                "last_scene_summary and last_scene_turn must both be set or "
                "both be null"
            )
        return self


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
    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(
        default=None,
        max_length=NPC_PERSONALITY_TRAIT_MAX_LENGTH,
    )
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None

    @field_validator("personality_trait", mode="before")
    @classmethod
    def _trait_empty_to_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value




class NPCProfileBackfillOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)


class NPCProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(default_factory=new_hex_id)
    name: str
    aliases: list[str] = Field(default_factory=list)
    transient_aliases: dict[str, int] = Field(default_factory=dict)

    role: str | None = None
    appearance: str | None = None
    personality_trait: str | None = Field(
        default=None,
        max_length=NPC_PERSONALITY_TRAIT_MAX_LENGTH,
    )
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    faction: str | None = None
    last_known_location: str | None = None
    attitude_to_player: str | None = None

    #: 最近一次实际参与场景时的公开表现；``last_scene_summary`` 与
    #: ``last_scene_turn`` 由程序与 evidence 一起写入，二者必须同时存在或同时为空。
    last_scene_summary: str | None = Field(
        default=None,
        max_length=NPC_LAST_SCENE_SUMMARY_MAX_LENGTH,
    )
    last_scene_turn: int | None = Field(default=None, ge=0)

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

    @model_validator(mode="after")
    def _last_scene_fields_mutual_null(self):
        if (self.last_scene_summary is None) != (self.last_scene_turn is None):
            raise ValueError(
                "last_scene_summary and last_scene_turn must both be set or "
                "both be null"
            )
        return self

    def update_view(self) -> NPCProfileUpdateInput:
        return NPCProfileUpdateInput(
            name=self.name,
            aliases=list(self.aliases),
            role=self.role,
            appearance=self.appearance,
            personality_trait=self.personality_trait,
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            last_known_location=self.last_known_location,
            attitude_to_player=self.attitude_to_player,
        )

    def public_view(self) -> NPCProfilePublicView:
        return NPCProfilePublicView(
            **self.update_view().model_dump(mode="json"),
            last_scene_summary=self.last_scene_summary,
            last_scene_turn=self.last_scene_turn,
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
        role=profile.role,
        appearance=profile.appearance,
        personality_trait=profile.personality_trait,
        known_abilities=list(profile.known_abilities),
        faction=profile.faction,
        last_known_location=profile.last_known_location,
        attitude_to_player=profile.attitude_to_player,
        source_scene_ids=source_scene_ids or [],
        updated_at=updated_at or utc_now(),
        active=active,
        name_hit_count=name_hit_count,
        alias_hit_counts=dict(alias_hit_counts or {}),
    )
