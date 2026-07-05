from pydantic import BaseModel, Field

from app.domain.action.schemas import ActionParseResult
from app.domain.context_qa.schemas import ContextAnswerSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCPlotInjection
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.turn_resolution.schemas import TurnResolutionResult


class PlotOutlineContext(BaseModel):
    user_input: str
    current_state: CurrentState
    action_parse_result: ActionParseResult
    turn_resolution: TurnResolutionResult
    core_memory: CoreMemory
    context_answers: ContextAnswerSet = Field(
        default_factory=lambda: ContextAnswerSet(turn_focus="No Context QA", answers=[])
    )
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    npc_profiles: list[NPCProfileDirectorView] = Field(default_factory=list)
    director_event: DirectorEvent | None = None
    recent_archived_director_event: DirectorEvent | None = None
    director_npc_injection: DirectorNPCPlotInjection | None = None


def assemble_context(
    *,
    user_input: str,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    turn_resolution: TurnResolutionResult,
    core_memory: CoreMemory,
    context_answers: ContextAnswerSet | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    director_event: DirectorEvent | None = None,
    recent_archived_director_event: DirectorEvent | None = None,
    director_npc_injection: DirectorNPCPlotInjection | None = None,
) -> PlotOutlineContext:
    return PlotOutlineContext(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        turn_resolution=turn_resolution,
        core_memory=core_memory,
        context_answers=context_answers or ContextAnswerSet(turn_focus="No Context QA", answers=[]),
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
        director_event=director_event,
        recent_archived_director_event=recent_archived_director_event,
        director_npc_injection=director_npc_injection,
    )
