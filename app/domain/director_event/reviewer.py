import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries

from .schemas import DirectorEvent, DirectorEventReviewResult


class DirectorEventReviewer:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def review(
        self,
        *,
        active_event: DirectorEvent,
        user_input: str,
        scene_facts: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
    ) -> DirectorEventReviewResult:
        event_view = active_event.semantic_view()
        event_view.update(
            status=active_event.status,
            created_turn=active_event.created_turn,
            updated_turn=active_event.updated_turn,
        )
        return cast(
            DirectorEventReviewResult,
            self.llm.run(
                task="director_event_review",
                input_data={
                    "active_event_json": json.dumps(
                        event_view,
                        ensure_ascii=False,
                        indent=2,
                    ),
                    "user_input": user_input,
                    "scene_facts_json": scene_facts,
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "scene_summaries_json": project_scene_summaries(scene_summaries),
                    "output_schema_json": json.dumps(
                        DirectorEventReviewResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorEventReviewResult,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
