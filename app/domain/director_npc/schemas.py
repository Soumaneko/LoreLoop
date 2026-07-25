import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.npc_profile.schemas import NPCProfilePublicView
DirectorNPCStatus = Literal["standby", "dispatched", "discarded"]
DirectorNPCDispatchAction = Literal["dispatch_standby", "recall_existing", "none"]


class DirectorNPCCreativeBrief(BaseModel):
    setting_relation: str = Field(min_length=1)
    social_position: str = Field(min_length=1)
    life_phase: str = Field(min_length=1)
    desire_pattern: str = Field(min_length=1)
    optional_dimension: str = Field(min_length=1)


def _new_id() -> str:
    return f"director_npc_{uuid.uuid4().hex[:12]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class DirectorNPCDraft(BaseModel):
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
    source_setting_id: str | None = None
    creative_brief: DirectorNPCCreativeBrief | None = None
    status: DirectorNPCStatus = "standby"
    created_turn: int = Field(ge=0)
    dispatched_turn: int | None = Field(default=None, ge=0)
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
            dispatched_turn=self.dispatched_turn,
        )


class DirectorNPCCreateOutput(BaseModel):
    setting_expansion_note: str = Field(min_length=1)
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
    dispatched_turn: int | None = None


class DispatchedDirectorNPCCandidate(BaseModel):
    selected_id: str
    selected_name: str
    public_entity_profile: NPCProfilePublicView | None = None
    director_private_profile: DirectorNPCPrivateProfile


class ExistingNPCCandidate(BaseModel):
    name: str
    role: str | None = None
    current_intent: str | None = None
    attitude_to_player: str | None = None
    absent_turns: int = Field(default=0, ge=0)


class DirectorNPCDispatchDecision(BaseModel):
    reason: str = ""
    action: DirectorNPCDispatchAction
    selected_id: str | None = None
    selected_name: str | None = None

    @model_validator(mode="after")
    def _validate_payload(self):
        if self.action == "none":
            return self
        if not (self.selected_id and self.selected_name):
            raise ValueError("selected_id and selected_name are required when dispatching")
        return self


class DirectorNPCPlotInjection(BaseModel):
    action: Literal["dispatch_standby", "recall_existing"]
    selected_id: str
    selected_name: str
    selected_npc: dict = Field(default_factory=dict)


class DirectorNPCWorkflowResult(BaseModel):
    dispatch: DirectorNPCDispatchDecision | None = None
    plot_injection: DirectorNPCPlotInjection | None = None
    trigger_reason: str | None = None
    skipped_reason: str | None = None
