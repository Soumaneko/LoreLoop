from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventReviewResult,
    DirectorEventSelectionResult,
)
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS = 3


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
    standby_director_npcs: list[DirectorNPCDraft] | None = None,
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
        if review.player_involved and not active.player_involved:
            store.mark_player_involved(active.event_id, turn_index=turn_index)
        if review.decision == "archive_active":
            store.archive_active(
                active.event_id,
                turn_index=turn_index,
                resolved_reason=review.reason,
            )
        elif review.decision == "suspend_active":
            store.suspend(
                active.event_id,
                review.suspended_reason or review.reason,
                turn_index=turn_index,
            )
        else:
            store.touch(active.event_id, turn_index=turn_index)

        active_after = store.get_active()
        notes = ["reviewed_existing_active_event"]
        if review.decision == "archive_active":
            notes.append("archived_active_event")
        elif review.decision == "suspend_active":
            notes.append("suspended_active_event")
        else:
            notes.append("kept_active_event")
        return DirectorEventWorkflowResult(
            mode="review_active",
            active_before=active,
            active_after=active_after,
            review_result=review,
            changed_event_id=changed_event_id,
            notes=notes,
        )

    latest_archived = store.get_latest_archived()
    if latest_archived is not None:
        archived_turn = (
            latest_archived.resolved_turn
            or latest_archived.updated_turn
            or latest_archived.created_turn
        )
        if turn_index <= archived_turn + DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS:
            return DirectorEventWorkflowResult(
                mode="cooldown",
                active_before=None,
                active_after=None,
                changed_event_id=latest_archived.event_id,
                notes=["director_event_archived_cooldown"],
            )

    suspended = store.list_suspended()
    archived = store.list_archived()
    standby_director_npcs = standby_director_npcs or []
    if not suspended and not standby_director_npcs:
        return DirectorEventWorkflowResult(
            mode="no_standby_director_npc",
            active_before=None,
            active_after=None,
            notes=["director_event_creation_skipped_no_standby_director_npc"],
        )

    selection = selector.select_or_create(
        suspended_events=suspended,
        archived_events=archived,
        standby_director_npcs=standby_director_npcs,
        scene_text=scene_text,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
    )

    changed_event_id = None
    if selection.action == "activate_suspended":
        suspended_ids = {event.event_id for event in suspended}
        if selection.activate_event_id not in suspended_ids:
            raise ValueError(
                "DirectorEventSelector selected an event that is not suspended: "
                f"{selection.activate_event_id}"
            )
        if not store.activate(selection.activate_event_id, turn_index=turn_index):
            raise RuntimeError(
                f"Failed to activate suspended director event: {selection.activate_event_id}"
            )
        changed_event_id = selection.activate_event_id
    else:
        standby_names = {npc.name for npc in standby_director_npcs}
        related_names = set(selection.new_event.related_npcs)
        if not (standby_names & related_names):
            raise ValueError(
                "DirectorEventSelector created a new event without a standby director NPC "
                "in related_npcs"
            )
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
