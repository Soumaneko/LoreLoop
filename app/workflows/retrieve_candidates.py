from dataclasses import dataclass, field

from app.domain.action.schemas import ActionParseResult
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.config import RetrievalConfig
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate


@dataclass
class RetrieveCandidatesResult:
    candidates: list[MemoryCandidate] = field(default_factory=list)
    total_scanned: int = 0
    total_returned: int = 0


def retrieve_candidates_workflow(
    parse_result: ActionParseResult,
    memory_store: MemoryStore,
    *,
    pending_memory_store: PendingMemoryStore | None = None,
    top_k: int | None = None,
    config: RetrievalConfig | None = None,
) -> CandidateRetrievalResult:
    retriever = CandidateRetriever(
        memory_store,
        config,
        pending_memory_store=pending_memory_store,
    )
    return retriever.retrieve(parse_result, top_k=top_k)
