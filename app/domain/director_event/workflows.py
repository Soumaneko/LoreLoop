from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventReviewResult,
)
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


class DirectorEventWorkflowResult(BaseModel):
    event_id: str
    active_before: DirectorEvent
    active_after: DirectorEvent | None = None
    review_result: DirectorEventReviewResult | None = None
    notes: list[str] = Field(default_factory=list)


def review_director_event_workflow(
    *,
    active_event: DirectorEvent,
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventReviewer,
    trace_id: str | None = None,
    guidance: str | None = None,
    consume_hooks: list[str] | None = None,
) -> DirectorEventWorkflowResult:
    active = store.get_by_id(active_event.event_id)
    if active is None or active.stage == "resolved":
        raise ValueError("review workflow requires an active DirectorEvent")

    review = reviewer.review(
        active_event=active,
        user_input=user_input,
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
        guidance=guidance,
    )

    if consume_hooks:
        store.mark_hooks_consumed(
            active.event_id,
            list(consume_hooks),
            turn_index=turn_index,
        )

    if review.action == "resolve":
        store.resolve_event(
            active.event_id,
            turn_index=turn_index,
            resolved_reason=review.resolved_reason or review.reason,
        )
        notes = ["resolved_active_event"]
    else:
        store.set_stage(active.event_id, "engaged", turn_index=turn_index)
        notes = ["kept_active_event"]

    store.append_player_contact(
        active.event_id,
        new_player_contact=review.new_player_contact,
        turn_index=turn_index,
    )

    active_after = store.get_by_id(active.event_id)
    return DirectorEventWorkflowResult(
        event_id=active.event_id,
        active_before=active,
        active_after=active_after,
        review_result=review,
        notes=notes,
    )
