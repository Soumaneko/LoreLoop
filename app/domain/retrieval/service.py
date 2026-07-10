from app.domain.action.schemas import ActionParseResult
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.config import RetrievalConfig
from app.domain.retrieval.schemas import CandidateRetrievalResult


def retrieve_candidates(
    parse_result: ActionParseResult,
    memory_store: MemoryStore,
    *,
    top_k: int | None = None,
    config: RetrievalConfig | None = None,
) -> CandidateRetrievalResult:
    with CandidateRetriever(
        memory_store,
        config,
    ) as retriever:
        return retriever.retrieve(parse_result, top_k=top_k)
