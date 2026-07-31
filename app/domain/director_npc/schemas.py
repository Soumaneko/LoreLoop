import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
DirectorNPCStatus = Literal["prepared", "introduced", "retired"]


class DirectorNPCCreativeBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    world_connection: str = Field(min_length=1)
    social_position: str = Field(min_length=1)
    life_phase: str = Field(min_length=1)
    desire_pattern: str = Field(min_length=1)
    optional_dimension: str = Field(min_length=1)


class NPCCreationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=300)
    anchors: list[str] = Field(default_factory=list, max_length=8)


def _new_id() -> str:
    return f"director_npc_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorNPCDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str = Field(default_factory=_new_id)
    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None
    personality_traits: list[str] = Field(min_length=2, max_length=5)
    known_abilities: list[str] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)
    encounter_potentials: list[str] = Field(min_length=3, max_length=5)
    creation_anchors: list[str] = Field(default_factory=list, max_length=8)
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
            personality_traits=list(self.personality_traits),
            known_abilities=list(self.known_abilities),
            faction=self.faction,
            core_motivation=self.core_motivation,
            hidden_secret=self.hidden_secret,
            conflict_style=self.conflict_style,
            social_style=self.social_style,
            encounter_potentials=list(self.encounter_potentials),
            created_turn=self.created_turn,
            introduced_turn=self.introduced_turn,
        )


class DirectorNPCCreateOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    creation_reason: str = Field(min_length=1)
    core_motivation: str = Field(min_length=1)
    hidden_secret: str = Field(min_length=1)
    conflict_style: str = Field(min_length=1)
    social_style: str = Field(min_length=1)
    role: str = Field(min_length=1)
    faction: str | None = None
    personality_traits: list[str] = Field(min_length=2, max_length=5)
    known_abilities: list[str] = Field(default_factory=list, max_length=5)
    appearance: str = Field(min_length=1)
    name: str = Field(min_length=1)
    encounter_potentials: list[str] = Field(min_length=3, max_length=5)


class DirectorNPCPrivateProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    draft_id: str
    core_motivation: str
    hidden_secret: str
    conflict_style: str
    social_style: str
    role: str
    faction: str | None = None
    personality_traits: list[str] = Field(default_factory=list)
    known_abilities: list[str] = Field(default_factory=list)
    appearance: str
    name: str
    encounter_potentials: list[str] = Field(default_factory=list)
    created_turn: int
    introduced_turn: int | None = None
