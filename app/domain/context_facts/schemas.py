from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.action.schemas import Anchor
from app.domain.confirmed_history.schemas import HistorySource
from app.domain.continuity import ContinuitySelection
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.schemas import SettingKind


class ContextFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class ContextFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    holder: str = Field(min_length=1)
    source: str = Field(min_length=1)
    fact: str = Field(min_length=1)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)

    def render_fact(self) -> str:
        return f"{self.holder.strip()}已知({self.source.strip()}): {self.fact.strip()}"


class ContextFactSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    facts: list[ContextFact] = Field(default_factory=list, max_length=12)

    @model_validator(mode="after")
    def _validate_unique_fact_ids(self):
        fact_ids = [fact.fact_id for fact in self.facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("fact_id values must be unique")
        return self

    @classmethod
    def from_drafts(
        cls,
        *,
        turn_focus: str,
        facts: list[ContextFactDraft],
        known_evidence_ids: set[str] | None = None,
    ) -> "ContextFactSet":
        drafts = [
            ContextFactDraft.model_validate(fact)
            for fact in facts
        ]
        if known_evidence_ids is not None:
            unknown = sorted({
                evidence_id
                for fact in drafts
                for evidence_id in fact.evidence_ids
                if evidence_id not in known_evidence_ids
            })
            if unknown:
                raise ValueError(
                    f"unknown context history evidence IDs: {', '.join(unknown)}"
                )
        return cls(
            turn_focus=turn_focus,
            facts=[
                ContextFact(
                    fact_id=f"f{index}",
                    fact=fact.render_fact(),
                    evidence_ids=list(fact.evidence_ids),
                )
                for index, fact in enumerate(drafts, start=1)
            ],
        )


class ContextFactDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    facts: list[ContextFactDraft] = Field(default_factory=list, max_length=12)


class ContextSearchEntity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list, max_length=8)


class ContextSearchIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=1)
    focus: Literal["entity", "relationship", "location", "topic"]
    entities: list[ContextSearchEntity] = Field(default_factory=list, max_length=5)
    topics: list[str] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def _validate_search_material(self):
        if not self.entities and not self.topics:
            raise ValueError("context search intent requires entities or topics")
        return self


class ContextSearchPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intents: list[ContextSearchIntent] = Field(default_factory=list)


class SupportingEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class AdjacentEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    text: str = Field(min_length=1)


class ContextHistoryHit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    source_type: HistorySource
    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    paragraph_index: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1)
    text_truncated: bool = False
    original_text_chars: int | None = Field(default=None, ge=1)
    context_before: list[AdjacentEvidence] = Field(default_factory=list)
    context_after: list[AdjacentEvidence] = Field(default_factory=list)
    matched_intent_ids: list[str] = Field(min_length=1)
    matched_entities: list[str] = Field(default_factory=list)
    matched_topics: list[str] = Field(default_factory=list)
    score: float = Field(ge=0)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    supporting_evidence: list[SupportingEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_evidence_consistency(self):
        if self.text_truncated:
            if (
                self.original_text_chars is None
                or self.original_text_chars <= len(self.text)
            ):
                raise ValueError(
                    "truncated evidence requires a larger original_text_chars"
                )
        elif self.original_text_chars is not None:
            raise ValueError(
                "original_text_chars is only valid for truncated evidence"
            )
        supporting_ids = set(self.supporting_evidence_ids)
        provided_ids = {item.evidence_id for item in self.supporting_evidence}
        if supporting_ids != provided_ids:
            raise ValueError(
                "supporting_evidence_ids must match supporting_evidence exactly"
            )
        if len(self.supporting_evidence_ids) != len(supporting_ids):
            raise ValueError("supporting_evidence_ids must not contain duplicates")
        if self.evidence_id in supporting_ids:
            raise ValueError("supporting evidence must not duplicate the main evidence")
        if len(self.context_before) != len({
            item.evidence_id for item in self.context_before
        }):
            raise ValueError("context_before must not contain duplicate evidence IDs")
        if len(self.context_after) != len({
            item.evidence_id for item in self.context_after
        }):
            raise ValueError("context_after must not contain duplicate evidence IDs")
        return self


class ContextHistoryEvidenceSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hits: list[ContextHistoryHit] = Field(default_factory=list)
    indexed_documents: int = Field(default=0, ge=0)
    indexed_turns: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_global_evidence_uniqueness(self):
        seen: set[str] = set()
        for hit in self.hits:
            evidence_ids = [
                hit.evidence_id,
                *hit.supporting_evidence_ids,
                *(item.evidence_id for item in hit.context_before),
                *(item.evidence_id for item in hit.context_after),
            ]
            for evidence_id in evidence_ids:
                if evidence_id in seen:
                    raise ValueError(
                        f"evidence ID appears more than once: {evidence_id}"
                    )
                seen.add(evidence_id)
        return self


class SettingMatch(BaseModel):
    """A relevant World Setting admitted to Context Facts, public facts only."""

    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)
    kind: SettingKind
    name: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    public_facts: list[str] = Field(default_factory=list)
    matched_location: bool = False
    matched_name_in_input: bool = False
    matched_entities: list[str] = Field(default_factory=list)
    matched_topics: list[str] = Field(default_factory=list)
    score: float = Field(ge=0)


class SettingSelection(BaseModel):
    """Bounded selection of relevant public World Settings for the extractor."""

    model_config = ConfigDict(extra="forbid")

    settings: list[SettingMatch] = Field(default_factory=list, max_length=4)
    active_count: int = Field(default=0, ge=0)


class ContextExtractorInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    scene_summaries: list[SceneSummary] = Field(default_factory=list)
    history_evidence: ContextHistoryEvidenceSet
    continuity: ContinuitySelection
    settings: SettingSelection = Field(default_factory=SettingSelection)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)


class ContextFactsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    scene_summaries: list[SceneSummary] = Field(default_factory=list)
    matched_anchors: list[Anchor] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    current_turn_index: int = Field(ge=0)


class ContextFactsResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    search_plan: ContextSearchPlan
    history_evidence: ContextHistoryEvidenceSet
    continuity: ContinuitySelection
    settings: SettingSelection = Field(default_factory=SettingSelection)
    context_facts: ContextFactSet
