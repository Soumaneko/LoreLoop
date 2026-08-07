from app.core.llm.gateway import LLMGateway
from app.core.llm.mock import MockLLM


def make_llm_gateway(mock_llm: MockLLM) -> LLMGateway:
    return mock_llm.build_gateway()
