from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState
from app.domain.outcome import OutcomeResolutionResult, OutcomeResolver
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import RollResultBatch


def resolve_outcomes_workflow(
    parse_result: ActionParseResult,
    roll_results: RollResultBatch,
    current_state: CurrentState,
    *,
    retrieved_memories: CandidateRetrievalResult | None = None,
    resolver: OutcomeResolver | None = None,
) -> OutcomeResolutionResult:
    resolver = resolver or OutcomeResolver()
    return resolver.resolve(
        parse_result=parse_result,
        roll_results=roll_results,
        current_state=current_state,
        retrieved_memories=retrieved_memories,
    )
