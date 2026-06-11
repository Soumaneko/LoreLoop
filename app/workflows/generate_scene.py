from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory


def generate_scene_workflow(
    *,
    outline: PlotOutlineResult,
    current_state: CurrentState,
    core_memory: CoreMemory,
    user_input: str,
    retrieved_memories: CandidateRetrievalResult,
    short_term_memory: ShortTermMemory,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> SceneWriterResult:
    required_ids: set[str] = set()
    for beat in outline.beats:
        required_ids.update(beat.required_memories)

    selected_memories = [
        m for m in retrieved_memories.candidates if m.memory_id in required_ids
    ]

    recent_scene = short_term_memory.recent_scenes[-1] if short_term_memory.recent_scenes else None

    agent = SceneWriterAgent(gateway)
    return agent.write(
        outline=outline,
        current_state=current_state,
        core_memory=core_memory,
        user_input=user_input,
        selected_memories=selected_memories,
        recent_scene=recent_scene,
        trace_id=trace_id,
    )
