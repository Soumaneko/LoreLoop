from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.action.schemas import AnchorKind
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_summary.schemas import SceneSummary


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


class ContextQuestionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

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

    @classmethod
    def from_drafts(
        cls,
        *,
        turn_focus: str,
        questions: list[ContextQuestionDraft],
    ) -> "ContextQuestionSet":
        return cls(
            turn_focus=turn_focus,
            questions=[
                ContextQuestion(
                    question_id=f"q{index}",
                    question=question.question,
                    purpose=question.purpose,
                    retrieval_hints=question.retrieval_hints,
                )
                for index, question in enumerate(questions, start=1)
            ],
        )


class ContextQuestionDraftSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    questions: list[ContextQuestionDraft] = Field(default_factory=list, max_length=5)


class ContextQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_input: str = Field(min_length=1)
    current_state: CurrentState
    core_memory: CoreMemory
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)


ContextAnswerSupportStatus = Literal[
    "supported",
    "unsupported_by_memory_or_summary",
]


class ContextQuestionForAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    purpose: ContextQuestionPurpose


class ContextAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    answer: str = Field(min_length=1)
    support_status: ContextAnswerSupportStatus
    memory_indices: list[int] = Field(default_factory=list)
    scene_summary_indices: list[int] = Field(default_factory=list)


class ContextAnswerSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_focus: str = Field(min_length=1)
    answers: list[ContextAnswer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_unique_answer_ids(self):
        answer_ids = [answer.question_id for answer in self.answers]
        if len(answer_ids) != len(set(answer_ids)):
            raise ValueError("question_id values must be unique")
        return self


class ContextAnswerInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_memory: CoreMemory
    current_state: CurrentState
    director_event: DirectorEvent | None = None
    recent_archived_director_event: DirectorEvent | None = None
    scene_summaries: list[SceneSummary] = Field(default_factory=list)
    retrieved_memories: CandidateRetrievalResult
    questions: ContextQuestionSet
