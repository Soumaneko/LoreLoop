from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll_modifier.evaluator import RollModifierEvaluator
from app.domain.roll_modifier.schemas import RollModifierBatch


def evaluate_roll_modifiers_workflow(
    parse_result: ActionParseResult,
    current_state: CurrentState,
    gateway: LLMGateway,
    *,
    core_memory: CoreMemory,
    retrieved_memories: CandidateRetrievalResult | None = None,
    roll_actions: list[AtomicAction] | None = None,
    trace_id: str | None = None,
) -> RollModifierBatch:
    evaluator = RollModifierEvaluator(gateway)
    return evaluator.evaluate(
        parse_result=parse_result,
        current_state=current_state,
        core_memory=core_memory,
        retrieved_memories=retrieved_memories,
        roll_actions=roll_actions,
        trace_id=trace_id,
    )
