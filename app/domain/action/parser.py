import json

from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory import create_default_core_memory
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        core_memory: CoreMemory | None = None,
        recent_summaries: list[SceneSummary] | None = None,
        trace_id: str | None = None,
    ) -> ActionParseResult:
        core_memory = core_memory or create_default_core_memory()
        result = self.llm.run(
            task="action_parse",
            input_data={
                "user_input": user_input,
                "current_state_json": current_state.model_dump_json(indent=2),
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "recent_summaries_json": json.dumps(
                    [summary.model_dump(mode="json") for summary in (recent_summaries or [])],
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            output_schema=ActionParseResult,
            trace_id=trace_id,
        )
        return result
