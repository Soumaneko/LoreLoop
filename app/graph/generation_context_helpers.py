from app.domain.action.schemas import ActionParseResult, Anchor
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.service import retrieve_candidates
from app.domain.retrieval.schemas import CandidateRetrievalResult


def retrieve_context_fact_memories(
    *,
    user_input: str,
    anchors: list[Anchor],
    memory_store: MemoryStore,
    top_k: int = 12,
) -> CandidateRetrievalResult:
    if not anchors:
        return CandidateRetrievalResult()

    return retrieve_candidates(
        ActionParseResult(
            turn_intent=user_input,
            atomic_actions=[],
            anchors=anchors,
            ambiguities=[],
        ),
        memory_store,
        top_k=top_k,
    )
