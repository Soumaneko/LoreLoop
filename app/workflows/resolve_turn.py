from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.roll.schemas import RollResultBatch
from app.domain.turn_resolution import TurnResolutionAgent, TurnResolutionResult
from app.domain.director_event.schemas import DirectorEvent


def resolve_turn_workflow(
    *,
    user_input: str,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    roll_results: RollResultBatch,
    core_memory: CoreMemory,
    context_facts: ContextFactSet | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    director_event: DirectorEvent | None = None,
    recent_archived_director_event: DirectorEvent | None = None,
    gateway: LLMGateway,
    resolver: TurnResolutionAgent | None = None,
    trace_id: str | None = None,
) -> TurnResolutionResult:
    resolver = resolver or TurnResolutionAgent(gateway)
    return resolver.resolve(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        roll_results=roll_results,
        core_memory=core_memory,
        context_facts=context_facts,
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
        director_event=director_event,
        recent_archived_director_event=recent_archived_director_event,
        trace_id=trace_id,
    )
