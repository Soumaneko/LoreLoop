from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfilePublicView
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.helpers import build_entity_retrieval_anchors
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory

__all__ = [
    "build_entity_retrieval_anchors",
    "generate_scene_workflow",
]


def generate_scene_workflow(
    *,
    outline: PlotOutlineResult,
    current_state: CurrentState,
    core_memory: CoreMemory,
    short_term_memory: ShortTermMemory,
    gateway: LLMGateway,
    turn_index: int,
    context_facts: ContextFactSet | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfilePublicView] | None = None,
    max_chars: int = 1500,
    trace_id: str | None = None,
) -> SceneWriterResult:
    recent_scene = short_term_memory.recent_scenes[-1] if short_term_memory.recent_scenes else None

    agent = SceneWriterAgent(gateway)
    return agent.write(
        outline=outline,
        current_state=current_state,
        core_memory=core_memory,
        context_facts=context_facts or ContextFactSet(turn_focus="No Context Facts", facts=[]),
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
        recent_scene=recent_scene,
        turn_index=turn_index,
        max_chars=max_chars,
        trace_id=trace_id,
    )
