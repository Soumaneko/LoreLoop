from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.config import RetrievalConfig
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate, PendingMemoryNote


def retrieve_memories_by_entities(
    outline: PlotOutlineResult,
    memory_store: MemoryStore,
    *,
    max_results: int = 10,
) -> list[MemoryCandidate]:
    anchors = build_entity_retrieval_anchors(outline)
    if not anchors:
        return []

    synthetic_action = AtomicAction(
        action_id="_entity_retrieval",
        sequence=1,
        actor="system",
        action_type="observe",
        raw_text="entity retrieval",
        verb="retrieve",
        likely_needs_roll=False,
        confidence=1.0,
    )

    synthetic_parse = ActionParseResult(
        turn_intent="entity retrieval",
        atomic_actions=[synthetic_action],
        anchors=anchors,
    )

    config = RetrievalConfig(
        top_k=max_results,
        rrf_subtype_intent_weight=0.0,
        rrf_scene_context_weight=0.0,
        rrf_layer_intent_weight=0.0,
    )

    with CandidateRetriever(memory_store, config) as retriever:
        result = retriever.retrieve(synthetic_parse, top_k=max_results)
    return result.candidates


def build_entity_retrieval_anchors(outline: PlotOutlineResult) -> list[Anchor]:
    entity_names: list[str] = []
    seen: set[str] = set()
    for beat in outline.beats:
        for name in beat.related_entities:
            stripped = name.strip()
            key = stripped.casefold()
            if stripped and key not in seen:
                entity_names.append(stripped)
                seen.add(key)

    return [
        Anchor(
            text=name,
            normalized=name,
            kind="character",
            source="explicit",
            confidence=0.8,
        )
        for name in entity_names
    ]


def select_required_memories(
    outline: PlotOutlineResult,
    retrieved_memories: CandidateRetrievalResult,
) -> list[MemoryCandidate]:
    selected = []
    selected_ids: set[str] = set()
    indexed_memories = _indexed_retrieved_memories(retrieved_memories)

    for beat in outline.beats:
        for index in beat.required_memory_indices:
            candidate = indexed_memories.get(index)
            if candidate is None or candidate.memory_id in selected_ids:
                continue
            selected.append(candidate)
            selected_ids.add(candidate.memory_id)

    return selected


def _indexed_retrieved_memories(
    retrieved_memories: CandidateRetrievalResult,
) -> dict[int, MemoryCandidate]:
    indexed: dict[int, MemoryCandidate] = {}
    next_index = 0

    for candidate in retrieved_memories.candidates:
        indexed[next_index] = candidate
        next_index += 1

    for note in retrieved_memories.pending_notes:
        indexed[next_index] = _pending_note_to_candidate(note)
        next_index += 1

    return indexed


def _pending_note_to_candidate(note: PendingMemoryNote) -> MemoryCandidate:
    return MemoryCandidate(
        memory_id=f"pending:{note.pending_id}",
        memory_kind=note.memory_kind,
        evidence_mode="inferred",
        subject=note.subject,
        content=note.raw_text,
        entities=note.entities,
        tags=[],
        location=note.location,
        time_label=note.time_label,
        visibility=[],
        owner=None,
        confidence=1.0,
        salience=max(note.score, 0.0),
        source_scene_id=note.scene_id,
        source_text_span=note.source_text_span,
        source_pending_ids=[note.pending_id],
        score=note.score,
        score_breakdown={"pending_score": note.score},
        matched_anchors=note.matched_anchors,
        matched_actions=note.matched_actions,
    )
