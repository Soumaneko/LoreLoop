from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState

NpcPresence = Literal["appeared", "mentioned_only"]


class SceneNpcEvidence(BaseModel):
    """Evidence for a supplied NPC; identity and presence belong to Composer."""

    model_config = ConfigDict(extra="forbid")
    primary_surface: str = Field(min_length=1, description="输入人物的档案姓名")
    evidence_fact_ids: list[str] = Field(default_factory=list)
    evidence_paragraph_ids: list[str] = Field(default_factory=list)


class SceneNpcMention(SceneNpcEvidence):
    """Program-assembled identity and evidence for downstream projections."""

    presence: NpcPresence
    surfaces: list[str] = Field(default_factory=list)


class SceneFact(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)


class SceneFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact: str = Field(min_length=1)


class SceneFactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_focus: str = Field(min_length=1)
    facts: list[SceneFact] = Field(default_factory=list, max_length=12)
    npc_mentions: list[SceneNpcMention] = Field(default_factory=list)
    degraded: bool = False
    degraded_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_references(self):
        ids = {fact.fact_id for fact in self.facts}
        if len(ids) != len(self.facts):
            raise ValueError("fact_id values must be unique")
        if any(set(mention.evidence_fact_ids) - ids for mention in self.npc_mentions):
            raise ValueError("npc_mentions reference unknown fact ids")
        return self

    @classmethod
    def from_drafts(cls, *, scene_focus, facts, npc_mentions=None):
        return cls(
            scene_focus=scene_focus,
            facts=[SceneFact(fact_id=f"f{index}", fact=SceneFactDraft.model_validate(fact).fact)
                   for index, fact in enumerate(facts, start=1)],
            npc_mentions=list(npc_mentions or []),
        )


class SceneFactDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_focus: str = Field(min_length=1)
    facts: list[SceneFactDraft] = Field(default_factory=list, max_length=12)
    npc_mentions: list[SceneNpcEvidence] = Field(default_factory=list)


class SceneFactDraftSetLoose(BaseModel):
    model_config = ConfigDict(extra="allow")
    scene_focus: str | None = None
    facts: list[Any] = Field(default_factory=list)
    npc_mentions: list[Any] = Field(default_factory=list)


class SceneParagraphInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class SceneFactExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scene_text: str = ""
    core_memory: CoreMemory
    current_state: CurrentState
    scene_paragraphs: list[SceneParagraphInput] = Field(default_factory=list)
    used_npcs: list[dict[str, Any]] = Field(default_factory=list)
