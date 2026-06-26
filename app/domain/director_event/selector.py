import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries

from .schemas import DirectorEvent, DirectorEventSelectionResult


class DirectorEventSelector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def select_or_create(
        self,
        *,
        suspended_events: list[DirectorEvent],
        archived_events: list[DirectorEvent],
        scene_text: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
    ) -> DirectorEventSelectionResult:
        return cast(
            DirectorEventSelectionResult,
            self.llm.run(
                task="director_event_select",
                input_data={
                    "suspended_events_json": json.dumps(
                        [event.model_dump(mode="json") for event in suspended_events],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "archived_events_json": json.dumps(
                        [{"title": event.title} for event in archived_events],
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "scene_text": scene_text,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_summaries_json": project_scene_summaries(scene_summaries),
                    "output_schema_json": json.dumps(
                        DirectorEventSelectionResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorEventSelectionResult,
                temperature=0.3,
                trace_id=trace_id,
            ),
        )
