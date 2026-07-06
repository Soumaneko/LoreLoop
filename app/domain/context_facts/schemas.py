from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_summary.schemas import SceneSummary


class ContextFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_id: str = Field(min_length=1)
    fact: str = Field(min_length=1)


class ContextFactDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact: str = Field(min_length=1)


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
    ) -> "ContextFactSet":
        return cls(
            turn_focus=turn_focus,
            facts=[
                ContextFact(
                    fact_id=f"f{index}",
                    fact=ContextFactDraft.model_validate(fact).fact,
                )
                for index, fact in enumerate(facts, start=1)
            ],
        )


class ContextFactDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    facts: list[ContextFactDraft] = Field(default_factory=list, max_length=12)


class ContextExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
    director_event: DirectorEvent | None = None
    recent_archived_director_event: DirectorEvent | None = None
    scene_summaries: list[SceneSummary] = Field(default_factory=list)
    retrieved_memories: CandidateRetrievalResult
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
