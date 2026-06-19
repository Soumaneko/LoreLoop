import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries


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
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "recent_summaries_json": project_scene_summaries(recent_summaries),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=ActionPreviewResult,
                trace_id=trace_id,
            ),
        )
        return result
