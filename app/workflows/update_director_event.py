from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventReviewResult,
    DirectorEventSelectionResult,
)
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


class DirectorEventWorkflowResult(BaseModel):
    mode: str
    active_before: DirectorEvent | None = None
    active_after: DirectorEvent | None = None
    review_result: DirectorEventReviewResult | None = None
    selection_result: DirectorEventSelectionResult | None = None
    changed_event_id: str | None = None
    notes: list[str] = Field(default_factory=list)


def update_director_event_workflow(
    *,
    user_input: str,
    scene_text: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventReviewer,
    selector: DirectorEventSelector,
    trace_id: str | None = None,
) -> DirectorEventWorkflowResult:
    active = store.get_active()
    if active is not None:
        review = reviewer.review(
            active_event=active,
            user_input=user_input,
            scene_text=scene_text,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
            trace_id=trace_id,
        )
        changed_event_id = active.event_id
        if review.decision == "archive_active":
            store.archive_active(active.event_id, turn_index=turn_index)
        elif review.decision == "suspend_active":
            store.suspend(
                active.event_id,
                review.suspended_reason or review.reason,
                turn_index=turn_index,
            )
        else:
            store.touch(active.event_id, turn_index=turn_index)

        return DirectorEventWorkflowResult(
            mode="review_active",
            active_before=active,
            active_after=store.get_active(),
            review_result=review,
            changed_event_id=changed_event_id,
            notes=["reviewed_existing_active_event"],
        )

    suspended = store.list_suspended()
    archived = store.list_archived()
    selection = selector.select_or_create(
        suspended_events=suspended,
        archived_events=archived,
        scene_text=scene_text,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
    )

    changed_event_id = None
    if selection.action == "activate_suspended":
        changed_event_id = selection.activate_event_id
        store.activate(selection.activate_event_id, turn_index=turn_index)
    else:
        event = selection.new_event.to_event(turn_index=turn_index)
        changed_event_id = store.save(event)

    return DirectorEventWorkflowResult(
        mode="select_or_create",
        active_before=None,
        active_after=store.get_active(),
        selection_result=selection,
        changed_event_id=changed_event_id,
        notes=["selected_or_created_active_event"],
    )
