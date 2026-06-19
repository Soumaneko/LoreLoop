from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.candidate_retriever import CandidateRetriever
from app.domain.retrieval.config import RetrievalConfig
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate, PendingMemoryNote
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory


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

    retriever = CandidateRetriever(memory_store, config)
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
        layer="pending",
        subtype=note.extractor_hint or "note",
        subject="pending memory",
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


def generate_scene_workflow(
    *,
    outline: PlotOutlineResult,
    current_state: CurrentState,
    core_memory: CoreMemory,
    retrieved_memories: CandidateRetrievalResult,
    short_term_memory: ShortTermMemory,
    gateway: LLMGateway,
    turn_index: int,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    memory_store: MemoryStore | None = None,
    trace_id: str | None = None,
) -> SceneWriterResult:
    selected_memories = select_required_memories(outline, retrieved_memories)
    selected_ids = {m.memory_id for m in selected_memories}

    if memory_store is not None:
        entity_memories = retrieve_memories_by_entities(outline, memory_store)
        for mem in entity_memories:
            if mem.memory_id not in selected_ids:
                selected_memories.append(mem)
                selected_ids.add(mem.memory_id)

    recent_scene = short_term_memory.recent_scenes[-1] if short_term_memory.recent_scenes else None

    agent = SceneWriterAgent(gateway)
    return agent.write(
        outline=outline,
        current_state=current_state,
        core_memory=core_memory,
        selected_memories=selected_memories,
        entity_profiles=entity_profiles or [],
        recent_scene=recent_scene,
        turn_index=turn_index,
        trace_id=trace_id,
    )
