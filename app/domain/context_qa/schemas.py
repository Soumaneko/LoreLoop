from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.action.schemas import AnchorKind
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView


ContextQuestionPurpose = Literal[
    "action_result",
    "continuity",
    "npc_reaction",
    "state_resource",
    "memory_hook",
    "information_boundary",
    "director_context",
]


class RetrievalHint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    kind: AnchorKind = "unknown"
    reason: str = Field(min_length=1)
    normalized: str | None = None

    @model_validator(mode="after")
    def _fill_normalized(self):
        if self.normalized is None or not self.normalized.strip():
            self.normalized = self.text.strip()
        return self


class ContextQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    purpose: ContextQuestionPurpose
    retrieval_hints: list[RetrievalHint] = Field(default_factory=list, max_length=6)


class ContextQuestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    questions: list[ContextQuestion] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def _validate_unique_items(self):
        question_ids = [question.question_id for question in self.questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question_id values must be unique")
        return self


class ContextQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    current_state: CurrentState
    core_memory: CoreMemory
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
