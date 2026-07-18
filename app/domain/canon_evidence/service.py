from app.domain.narrative_canon_query.schemas import (
    CanonEntityReference,
    NarrativeCanonQuerySet,
)
from app.domain.short_term_memory.schemas import ShortTermMemoryStore

from .retriever import CanonEvidenceRetriever
from .schemas import CanonEvidenceSet
from .store import CanonEvidenceStore


def rebuild_and_retrieve_canon_evidence(
    *,
    query_set: NarrativeCanonQuerySet,
    short_term_store: ShortTermMemoryStore,
    evidence_store: CanonEvidenceStore,
    entity_catalog: list[CanonEntityReference],
    evidence_per_query: int = 5,
) -> CanonEvidenceSet:
    """Rebuild from confirmed raw history, then retrieve without derived facts."""
    evidence_store.rebuild(short_term_store.list_all(), entity_catalog)
    return CanonEvidenceRetriever(evidence_store, entity_catalog).retrieve(
        query_set,
        evidence_per_query=evidence_per_query,
    )
