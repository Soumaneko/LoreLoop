import json
from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.views import project_current_state, project_scene_summaries

from .schemas import (
    DirectorEvent,
    DirectorEventExpiryBatch,
)


def _expiry_inputs(
    *,
    events: list[DirectorEvent],
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
) -> dict:
    event_views = []
    for event in events:
        view = event.semantic_view()
        view["world_impact"] = (
            event.world_impact.model_dump(mode="json")
            if event.world_impact is not None else None
        )
        event_views.append(view)
    return {
        "events_json": json.dumps(
            event_views,
            ensure_ascii=False,
            indent=2,
        ),
        "user_input": user_input,
        "scene_facts_json": scene_facts,
        "current_state_json": project_current_state(current_state),
        "core_memory_json": core_memory.model_dump_json(indent=2),
        "scene_summaries_json": project_scene_summaries(scene_summaries),
    }


class DirectorEventExpiryReviewer:
    """判断已到期 engaged 事件的本轮参与情况及脱离后的最终影响。"""

    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def review_batch(
        self,
        *,
        events: list[DirectorEvent],
        user_input: str,
        scene_facts: str,
        current_state: CurrentState,
        core_memory: CoreMemory,
        scene_summaries: list[SceneSummary],
        trace_id: str | None = None,
    ) -> DirectorEventExpiryBatch:
        if not events or any(
            event.stage != "engaged" or not current_state.world_clock.has_reached(event.deadline_clock)
            for event in events
        ):
            raise ValueError("expiry review requires due engaged Events")
        inputs = _expiry_inputs(
            events=events,
            user_input=user_input,
            scene_facts=scene_facts,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
        )
        inputs["output_schema_json"] = schema_json(
            DirectorEventExpiryBatch
        )
        expected_ids = {event.event_id for event in events}

        def validator(batch) -> None:
            if {review.event_id for review in batch.reviews} != expected_ids:
                raise ValueError(
                    "expiry batch must cover every supplied event"
                )

        return cast(
            DirectorEventExpiryBatch,
            self.llm.run(
                task="director_event_expire",
                input_data=inputs,
                output_schema=DirectorEventExpiryBatch,
                temperature=0.2,
                trace_id=trace_id,
                output_validator=validator,
            ),
        )
