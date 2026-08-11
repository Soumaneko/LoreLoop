from functools import partial
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.core.ids import new_hex_id
from app.core.time import utc_now

DirectorNPCStatus = Literal["prepared", "introduced", "retired"]


class NPCCreationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_ids: list[str] = Field(default_factory=list, max_length=4)


class KnownAbility(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=120)


_new_id = partial(new_hex_id, prefix="director_npc", length=12)


class DirectorNPCDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    draft_id: str = Field(default_factory=_new_id)
    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None
    personality_trait: str = Field(min_length=1, max_length=40)
    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)
    source_setting_ids: list[str] = Field(default_factory=list, max_length=4)
    status: DirectorNPCStatus = "prepared"
    created_turn: int = Field(ge=0)
    introduced_turn: int | None = Field(default=None, ge=0)
    created_at: str = Field(default_factory=utc_now)
    updated_at: str = Field(default_factory=utc_now)

    def private_view(self) -> "DirectorNPCPrivateProfile":
        return DirectorNPCPrivateProfile(
            draft_id=self.draft_id,
            name=self.name,
            role=self.role,
            appearance=self.appearance,
            personality_trait=self.personality_trait,
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            core_motivation=self.core_motivation,
            hidden_secret=self.hidden_secret,
            conflict_style=self.conflict_style,
            social_style=self.social_style,
            source_setting_ids=list(self.source_setting_ids),
            created_turn=self.created_turn,
            introduced_turn=self.introduced_turn,
        )


class DirectorNPCIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None


class DirectorNPCBehavior(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    personality_trait: str = Field(min_length=1, max_length=40)


class DirectorNPCShell(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    known_abilities: list[KnownAbility] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)


class DirectorNPCCreateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    creation_reason: str = Field(min_length=1)
    identity: DirectorNPCIdentity
    behavior: DirectorNPCBehavior
    shell: DirectorNPCShell


class DirectorNPCPrivateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    core_motivation: str
    hidden_secret: str
    conflict_style: str
    social_style: str
    role: str
    faction: str | None = None
    personality_trait: str
    known_abilities: list[KnownAbility] = Field(default_factory=list)
    appearance: str
    name: str
    source_setting_ids: list[str] = Field(default_factory=list)
    created_turn: int
    introduced_turn: int | None = None
