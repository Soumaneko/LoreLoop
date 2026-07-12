from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
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
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    recent_summaries: list[SceneSummary] | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
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
            scene_facts=scene_facts,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
            trace_id=trace_id,
        )
        changed_event_id = active.event_id
        store.replace_player_contact(
            active.event_id,
            exposure=review.exposure,
            player_contact_state=review.player_contact_state,
            turn_index=turn_index,
        )
        manual_progress = False
        if review.action == "resolve":
            store.resolve_active(
                active.event_id,
                turn_index=turn_index,
                resolved_reason=review.resolved_reason or review.reason,
            )
        elif review.action == "advance":
            manual_progress = True
            if active.progress_index >= 2:
                store.resolve_active(
                    active.event_id,
                    turn_index=turn_index,
                    resolved_reason=review.resolved_reason or review.reason,
                )
            else:
                store.advance_progress(
                    active.event_id,
                    turn_index=turn_index,
                    current_clock=current_state.world_clock,
                )
                advanced = store.get_by_id(active.event_id)
                if advanced is not None and advanced.progress_index >= 2:
                    store.resolve_active(
                        active.event_id,
                        turn_index=turn_index,
                        resolved_reason=review.resolved_reason or review.reason,
                    )
        else:
            store.touch(active.event_id, turn_index=turn_index)

        auto_progress_result = None
        if review.action == "hold" and not manual_progress:
            auto_progress_result = store.auto_advance_due(
                active.event_id,
                turn_index=turn_index,
                current_clock=current_state.world_clock,
            )

        active_after = store.get_active()
        notes = ["reviewed_existing_active_event"]
        if review.action == "resolve":
            notes.append("resolved_active_event")
        elif review.action == "advance":
            notes.append("advanced_active_event")
        else:
            notes.append("kept_active_event")
        if auto_progress_result == "advanced":
            notes.append("auto_advanced_active_event")
        elif auto_progress_result == "resolved":
            notes.append("auto_resolved_active_event")
        return DirectorEventWorkflowResult(
            mode="review_active",
            active_before=active,
            active_after=active_after,
            review_result=review,
            changed_event_id=changed_event_id,
            notes=notes,
        )

    latest_resolved = store.get_latest_resolved()
    if latest_resolved is not None:
        resolved_turn = (
            latest_resolved.resolved_turn
            or latest_resolved.updated_turn
            or latest_resolved.created_turn
        )
        if turn_index <= resolved_turn + DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS:
            return DirectorEventWorkflowResult(
                mode="cooldown",
                active_before=None,
                active_after=None,
                changed_event_id=latest_resolved.event_id,
                notes=["director_event_resolved_cooldown"],
            )

    resolved = store.list_resolved()
    standby_director_npcs = standby_director_npcs or []

    selection = selector.select_or_create(
        resolved_events=resolved,
        standby_director_npcs=standby_director_npcs,
        current_state=current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries or scene_summaries[-5:],
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
        trace_id=trace_id,
    )

    changed_event_id = None
    if selection.action == "none":
        return DirectorEventWorkflowResult(
            mode="select_or_create",
            active_before=None,
            active_after=None,
            selection_result=selection,
            changed_event_id=None,
            notes=["director_event_creation_skipped_by_selector"],
        )

    if selection.new_event is None:
        raise ValueError("DirectorEventSelector action=create_new requires new_event")
    if not selection.new_event.bridge_anchors:
        raise ValueError("DirectorEventSelector created a new event without bridge_anchors")
    if len(selection.new_event.milestones) != 3:
        raise ValueError("DirectorEventSelector created a new event without exactly three milestones")
    event = selection.new_event.to_event(
        turn_index=turn_index,
        current_clock=current_state.world_clock,
    )
    changed_event_id = store.save(event)

    return DirectorEventWorkflowResult(
        mode="select_or_create",
        active_before=None,
        active_after=store.get_active(),
        selection_result=selection,
        changed_event_id=changed_event_id,
        notes=["selected_or_created_active_event"],
    )
