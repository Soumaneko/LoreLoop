from pydantic import BaseModel, Field

from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDispatchDecision
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.turn_resolution.schemas import TurnResolutionResult


class PlotOutlineContext(BaseModel):
    user_input: str
    current_state: CurrentState
    action_parse_result: ActionParseResult
    turn_resolution: TurnResolutionResult
    core_memory: CoreMemory
    retrieved_memories: CandidateRetrievalResult
    scene_summaries: list[SceneSummary] = Field(default_factory=list)
    entity_profiles: list[EntityProfileUpdateInput] = Field(default_factory=list)
    director_event: DirectorEvent | None = None
    recent_archived_director_event: DirectorEvent | None = None
    director_npc_injection: DirectorNPCDispatchDecision | None = None


def assemble_context(
    *,
    user_input: str,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    turn_resolution: TurnResolutionResult,
    core_memory: CoreMemory,
    retrieved_memories: CandidateRetrievalResult,
    scene_summaries: list[SceneSummary] | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    director_event: DirectorEvent | None = None,
    recent_archived_director_event: DirectorEvent | None = None,
    director_npc_injection: DirectorNPCDispatchDecision | None = None,
) -> PlotOutlineContext:
    return PlotOutlineContext(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        turn_resolution=turn_resolution,
        core_memory=core_memory,
        retrieved_memories=retrieved_memories,
        scene_summaries=scene_summaries or [],
        entity_profiles=entity_profiles or [],
        director_event=director_event,
        recent_archived_director_event=recent_archived_director_event,
        director_npc_injection=director_npc_injection,
    )
