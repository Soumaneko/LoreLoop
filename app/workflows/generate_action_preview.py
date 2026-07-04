from app.core.llm import LLMGateway
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_writer.schemas import NextActionDirection
from app.domain.scene_summary.schemas import SceneSummary


def generate_action_preview_workflow(
    *,
    closing_prompt: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    recent_summaries: list[SceneSummary],
    next_action_directions: list[NextActionDirection] | None = None,
    gateway: LLMGateway,
    trace_id: str | None = None,
) -> ActionPreviewResult:
    agent = ActionPreviewAgent(gateway)
    return agent.generate(
        closing_prompt=closing_prompt,
        current_state=current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        next_action_directions=next_action_directions,
        trace_id=trace_id,
    )
