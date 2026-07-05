from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, Anchor
from app.domain.context_qa.answerer import ContextAnswerAgent
from app.domain.context_qa.schemas import (
    ContextAnswerInput,
    ContextAnswerSet,
    ContextQuestionSet,
)
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.retrieve_candidates import retrieve_candidates_workflow


def retrieve_context_question_memories(
    questions: ContextQuestionSet,
    memory_store: MemoryStore,
    *,
    top_k: int = 8,
) -> CandidateRetrievalResult:
    anchors: list[Anchor] = []
    seen: set[tuple[str, str]] = set()
    for question in questions.questions:
        for hint in question.retrieval_hints:
            normalized = (hint.normalized or hint.text).strip()
            if not normalized:
                continue
            key = (normalized, hint.kind)
            if key in seen:
                continue
            seen.add(key)
            anchors.append(
                Anchor(
                    text=hint.text,
                    normalized=normalized,
                    kind=hint.kind,
                    source="explicit",
                    confidence=1.0,
                )
            )

    if not anchors:
        return CandidateRetrievalResult()

    return retrieve_candidates_workflow(
        ActionParseResult(
            turn_intent=questions.turn_focus,
            atomic_actions=[],
            anchors=anchors,
            ambiguities=[],
        ),
        memory_store,
        top_k=top_k,
    )


def answer_context_questions_workflow(
    *,
    questions: ContextQuestionSet,
    core_memory: CoreMemory,
    current_state: CurrentState,
    director_event: DirectorEvent | None,
    recent_archived_director_event: DirectorEvent | None,
    scene_summaries: list[SceneSummary],
    memory_store: MemoryStore,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> tuple[ContextAnswerSet, CandidateRetrievalResult]:
    retrieved_memories = retrieve_context_question_memories(
        questions,
        memory_store,
    )

    if not questions.questions:
        return (
            ContextAnswerSet(turn_focus=questions.turn_focus, answers=[]),
            retrieved_memories,
        )

    answers = ContextAnswerAgent(gateway).generate(
        ContextAnswerInput(
            core_memory=core_memory,
            current_state=current_state,
            director_event=director_event,
            recent_archived_director_event=recent_archived_director_event,
            scene_summaries=scene_summaries,
            retrieved_memories=retrieved_memories,
            questions=questions,
        ),
        trace_id=trace_id,
    )
    return answers, retrieved_memories
