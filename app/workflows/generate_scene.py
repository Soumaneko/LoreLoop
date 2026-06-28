from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.helpers import (
    build_entity_retrieval_anchors,
    retrieve_memories_by_entities,
    select_required_memories,
)
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.short_term_memory.schemas import ShortTermMemory

__all__ = [
    "build_entity_retrieval_anchors",
    "generate_scene_workflow",
    "retrieve_memories_by_entities",
    "select_required_memories",
]


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
    recent_summaries: list[SceneSummary] | None = None,
    memory_store: MemoryStore | None = None,
    max_chars: int = 1500,
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
        recent_summaries=recent_summaries or [],
        recent_scene=recent_scene,
        turn_index=turn_index,
        max_chars=max_chars,
        trace_id=trace_id,
    )
