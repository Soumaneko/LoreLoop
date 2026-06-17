from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory


def select_required_memories(
    outline: PlotOutlineResult,
    retrieved_memories: CandidateRetrievalResult,
) -> list[MemoryCandidate]:
    required_refs: list[str] = []
    seen_refs: set[str] = set()
    for beat in outline.beats:
        for ref in beat.required_memories:
            normalized_ref = str(ref).strip()
            if normalized_ref and normalized_ref not in seen_refs:
                required_refs.append(normalized_ref)
                seen_refs.add(normalized_ref)

    if not required_refs:
        return []

    selected = []
    selected_ids: set[str] = set()

    for candidate in retrieved_memories.candidates:
        if candidate.memory_id in required_refs:
            selected.append(candidate)
            selected_ids.add(candidate.memory_id)

    unresolved_refs = [
        ref for ref in required_refs
        if ref not in selected_ids
    ]
    if not unresolved_refs:
        return selected

    lowered_refs = [ref.casefold() for ref in unresolved_refs]
    for candidate in retrieved_memories.candidates:
        if candidate.memory_id in selected_ids:
            continue

        searchable_values = [
            candidate.subject,
            candidate.content,
            *candidate.entities,
            *candidate.tags,
        ]
        searchable_values = [
            str(value).strip().casefold()
            for value in searchable_values
            if str(value).strip()
        ]
        if any(
            ref == value or ref in value
            for ref in lowered_refs
            for value in searchable_values
        ):
            selected.append(candidate)
            selected_ids.add(candidate.memory_id)

    return selected


def generate_scene_workflow(
    *,
    outline: PlotOutlineResult,
    current_state: CurrentState,
    core_memory: CoreMemory,
    user_input: str,
    retrieved_memories: CandidateRetrievalResult,
    short_term_memory: ShortTermMemory,
    gateway: LLMGateway,
    turn_index: int,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    trace_id: str | None = None,
) -> SceneWriterResult:
    selected_memories = select_required_memories(outline, retrieved_memories)

    recent_scene = short_term_memory.recent_scenes[-1] if short_term_memory.recent_scenes else None

    agent = SceneWriterAgent(gateway)
    return agent.write(
        outline=outline,
        current_state=current_state,
        core_memory=core_memory,
        user_input=user_input,
        selected_memories=selected_memories,
        entity_profiles=entity_profiles or [],
        recent_scene=recent_scene,
        turn_index=turn_index,
        trace_id=trace_id,
    )
