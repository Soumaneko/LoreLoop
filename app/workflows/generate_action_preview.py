from app.core.llm import LLMGateway
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.narrative_pacer.schemas import NarrativePacingResult
from app.domain.short_term_memory.schemas import ShortTermMemory


def generate_action_preview_workflow(
    *,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    narrative_pacing: NarrativePacingResult,
    short_term_memory: ShortTermMemory,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> ActionPreviewResult:
    agent = ActionPreviewAgent(gateway)
    return agent.generate(
        scene_text=scene_text,
        current_state=current_state,
        core_memory=core_memory,
        narrative_pacing=narrative_pacing,
        short_term_memory=short_term_memory,
        trace_id=trace_id,
    )
