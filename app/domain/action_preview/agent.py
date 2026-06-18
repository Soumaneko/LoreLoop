import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary


class ActionPreviewAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        scene_text: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        recent_summaries: list[SceneSummary],
        trace_id: str | None = None,
    ) -> ActionPreviewResult:
        schema = ActionPreviewResult.model_json_schema()

        result = cast(
            ActionPreviewResult,
            self.llm.run(
                task="action_preview",
                input_data={
                    "scene_text": scene_text,
                    "current_state_json": current_state.model_dump_json(indent=2),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "recent_summaries_json": json.dumps(
                        [s.model_dump(mode="json") for s in recent_summaries],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=ActionPreviewResult,
                trace_id=trace_id,
            ),
        )
        return result
