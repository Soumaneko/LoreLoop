from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, Anchor
from app.domain.context_facts.extractor import ContextExtractor
from app.domain.context_facts.schemas import ContextExtractInput, ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.recall_memory.store import MemoryStore
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_summary.schemas import SceneSummary
from app.workflows.retrieve_candidates import retrieve_candidates_workflow


def retrieve_context_fact_memories(
    *,
    user_input: str,
    anchors: list[Anchor],
    memory_store: MemoryStore,
    top_k: int = 12,
) -> CandidateRetrievalResult:
    if not anchors:
        return CandidateRetrievalResult()

    return retrieve_candidates_workflow(
        ActionParseResult(
            turn_intent=user_input,
            atomic_actions=[],
            anchors=anchors,
            ambiguities=[],
        ),
        memory_store,
        top_k=top_k,
    )


def extract_context_facts_workflow(
    *,
    user_input: str,
    core_memory: CoreMemory,
    current_state: CurrentState,
    scene_summaries: list[SceneSummary],
    memory_store: MemoryStore,
    anchors: list[Anchor],
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> tuple[ContextFactSet, CandidateRetrievalResult]:
    retrieved_memories = retrieve_context_fact_memories(
        user_input=user_input,
        anchors=anchors,
        memory_store=memory_store,
    )
    facts = ContextExtractor(gateway).extract(
        ContextExtractInput(
            user_input=user_input,
            core_memory=core_memory,
            current_state=current_state,
            scene_summaries=scene_summaries,
            retrieved_memories=retrieved_memories,
            entity_profiles=entity_profiles or [],
            npc_profiles=npc_profiles or [],
        ),
        trace_id=trace_id,
    )
    return facts, retrieved_memories
