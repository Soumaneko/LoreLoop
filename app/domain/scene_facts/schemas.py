from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState


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
        scene_focus: str,
        facts: list[SceneFactDraft],
    ) -> "SceneFactSet":
        return cls(
            scene_focus=scene_focus,
            facts=[
                SceneFact(
                    fact_id=f"f{index}",
                    fact=SceneFactDraft.model_validate(fact).fact,
                )
                for index, fact in enumerate(facts, start=1)
            ],
        )


class SceneFactDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_focus: str = Field(min_length=1)
    facts: list[SceneFactDraft] = Field(default_factory=list, max_length=12)


class SceneFactExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_text: str = Field(min_length=1)
    core_memory: CoreMemory
    current_state: CurrentState
