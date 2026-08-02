from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.reviewer import (
    DirectorEventEngagedReviewer,
    DirectorEventHiddenReviewer,
)
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventEngagedReviewResult,
    DirectorEventHiddenReviewResult,
)
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


class DirectorEventWorkflowResult(BaseModel):
    event_id: str
    active_before: DirectorEvent
    active_after: DirectorEvent | None = None
    review_result: DirectorEventHiddenReviewResult | DirectorEventEngagedReviewResult | None = None
    notes: list[str] = Field(default_factory=list)


def _require_valid_consume_hooks(
    active: DirectorEvent,
    consume_hook_ids: list[str],
) -> None:
    known = {hook.hook_id for hook in active.hook_pool}
    invalid = [
        hook_id for hook_id in consume_hook_ids if hook_id not in known
    ]
    if invalid:
        raise ValueError(
            "consume_hook_ids not part of this event: "
            + ", ".join(sorted(invalid))
        )
    consumed = [
        hook.hook_id
        for hook in active.hook_pool
        if hook.hook_id in consume_hook_ids and hook.consumed
    ]
    if consumed:
        raise ValueError(
            "consume_hook_ids already consumed: " + ", ".join(sorted(consumed))
        )


def review_hidden_events_workflow(
    *,
    active_events: list[DirectorEvent],
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventHiddenReviewer,
    trace_id: str | None = None,
    guidance: str | None = None,
) -> list[DirectorEventWorkflowResult]:
    batch = reviewer.review_batch(
        events=active_events,
        user_input=user_input,
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
        guidance=guidance,
    )
    by_id = {event.event_id: event for event in active_events}

    results: list[DirectorEventWorkflowResult] = []
    for review in batch.reviews:
        active = by_id[review.event_id]
        if review.consume_hook_ids:
            _require_valid_consume_hooks(active, review.consume_hook_ids)
            store.mark_hooks_consumed(
                active.event_id,
                list(review.consume_hook_ids),
                turn_index=turn_index,
            )

        notes: list[str] = []
        if review.action == "engage":
            store.set_stage(active.event_id, "engaged", turn_index=turn_index)
            notes.append("engaged_hidden_event")
        else:
            notes.append("kept_hidden_event")

        store.append_player_contact(
            active.event_id,
            new_player_contact=review.new_player_contact,
            turn_index=turn_index,
        )

        active_after = store.get_by_id(active.event_id)
        results.append(
            DirectorEventWorkflowResult(
                event_id=active.event_id,
                active_before=active,
                active_after=active_after,
                review_result=review,
                notes=notes,
            )
        )
    return results


def review_engaged_events_workflow(
    *,
    active_events: list[DirectorEvent],
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventEngagedReviewer,
    trace_id: str | None = None,
    guidance: str | None = None,
) -> list[DirectorEventWorkflowResult]:
    batch = reviewer.review_batch(
        events=active_events,
        user_input=user_input,
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
        guidance=guidance,
    )
    by_id = {event.event_id: event for event in active_events}

    results: list[DirectorEventWorkflowResult] = []
    for review in batch.reviews:
        active = by_id[review.event_id]
        if review.action == "resolve":
            store.resolve_event(
                active.event_id,
                turn_index=turn_index,
                resolved_reason=review.resolved_reason or review.reason,
            )
            notes = ["resolved_engaged_event"]
        else:
            notes = ["kept_engaged_event"]

        store.append_player_contact(
            active.event_id,
            new_player_contact=review.new_player_contact,
            turn_index=turn_index,
        )

        active_after = store.get_by_id(active.event_id)
        results.append(
            DirectorEventWorkflowResult(
                event_id=active.event_id,
                active_before=active,
                active_after=active_after,
                review_result=review,
                notes=notes,
            )
        )
    return results
