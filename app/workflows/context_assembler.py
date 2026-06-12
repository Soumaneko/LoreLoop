from pydantic import BaseModel

from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.outcome.schemas import OutcomeResolutionResult
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import RollResultBatch
from app.domain.short_term_memory.schemas import ShortTermMemory


class PlotOutlineContext(BaseModel):
    user_input: str
    current_state: CurrentState
    action_parse_result: ActionParseResult
    roll_results: RollResultBatch
    outcome_resolution: OutcomeResolutionResult
    core_memory: CoreMemory
    short_term_memory: ShortTermMemory
    retrieved_memories: CandidateRetrievalResult


def assemble_context(
    *,
    user_input: str,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    roll_results: RollResultBatch,
    outcome_resolution: OutcomeResolutionResult,
    core_memory: CoreMemory,
    short_term_memory: ShortTermMemory,
    retrieved_memories: CandidateRetrievalResult,
) -> PlotOutlineContext:
    return PlotOutlineContext(
        user_input=user_input,
        current_state=current_state,
        action_parse_result=action_parse_result,
        roll_results=roll_results,
        outcome_resolution=outcome_resolution,
        core_memory=core_memory,
        short_term_memory=short_term_memory,
        retrieved_memories=retrieved_memories,
    )
