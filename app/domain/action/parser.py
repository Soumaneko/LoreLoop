import json

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.current_state.schemas import CurrentState


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        trace_id: str | None = None,
    ) -> ActionParseResult:
        result = self.llm.run(
            task="action_parse",
            input_data={
                "user_input": user_input,
                "current_state_json": current_state.model_dump_json(indent=2),
            },
            output_schema=ActionParseResult,
            trace_id=trace_id,
        )
        return result
