import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.views import project_current_state

from .schemas import DirectorEvent, DirectorEventInterventionReviewResult


class DirectorEventInterventionReviewer:
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
        trace_id: str | None = None,
        guidance: str | None = None,
    ) -> DirectorEventInterventionReviewResult:
        if (
            active_event.route_mode != "intervention"
            or active_event.intervention_route is None
        ):
            raise ValueError("InterventionReviewer requires an intervention route")
        event_view = active_event.semantic_view()
        for excluded_field in ("player_contacts",):
            event_view.pop(excluded_field, None)
        return cast(
            DirectorEventInterventionReviewResult,
            self.llm.run(
                task="director_event_intervention_review",
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
                    "director_guidance": guidance or "",
                    "output_schema_json": json.dumps(
                        DirectorEventInterventionReviewResult.model_json_schema(),
                        ensure_ascii=False,
                        indent=2,
                    ),
                },
                output_schema=DirectorEventInterventionReviewResult,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
