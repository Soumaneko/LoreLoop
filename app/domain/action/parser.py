import json

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def parse(
        self,
        user_input: str,
        scene_context: dict,
        *,
        trace_id: str | None = None,
    ) -> ActionParseResult:
        result = self.llm.run(
            task="action_parse",
            input_data={
                "user_input": user_input,
                "scene_context": json.dumps(scene_context, ensure_ascii=False, indent=2),
            },
            output_schema=ActionParseResult,
            trace_id=trace_id,
        )
        return result
