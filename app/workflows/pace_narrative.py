from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.narrative_pacer import NarrativePacer, NarrativePacingResult, NarrativeType
from app.domain.outcome.schemas import OutcomeResolutionResult
from app.domain.scene_summary.schemas import SceneSummary


def pace_narrative_workflow(
    *,
    user_input: str,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    outcome_resolution: OutcomeResolutionResult,
    core_memory: CoreMemory,
    recent_summaries: list[SceneSummary],
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    gateway: LLMGateway,
    previous_types: list[NarrativeType] | None = None,
    pacer: NarrativePacer | None = None,
    trace_id: str | None = None,
) -> NarrativePacingResult:
    pacer = pacer or NarrativePacer(gateway)
    return pacer.pace(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        outcome_resolution=outcome_resolution,
        core_memory=core_memory,
        recent_summaries=recent_summaries,
        entity_profiles=entity_profiles or [],
        previous_types=previous_types,
        trace_id=trace_id,
    )
