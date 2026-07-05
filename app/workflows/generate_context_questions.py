from app.core.llm import LLMGateway
from app.domain.context_qa.questioner import ContextQuestionAgent
from app.domain.context_qa.schemas import ContextQuestionInput, ContextQuestionSet


def generate_context_questions_workflow(
    input_data: ContextQuestionInput,
    gateway: LLMGateway,
    *,
    trace_id: str | None = None,
) -> ContextQuestionSet:
    return ContextQuestionAgent(gateway).generate(input_data, trace_id=trace_id)
