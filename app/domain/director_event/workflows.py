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
    DirectorEventRole,
    DirectorEventSelectionResult,
)
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS = 3


class DirectorEventWorkflowResult(BaseModel):
    role: DirectorEventRole
    mode: str
    active_before: DirectorEvent | None = None
    active_after: DirectorEvent | None = None
    review_result: DirectorEventReviewResult | None = None
    selection_result: DirectorEventSelectionResult | None = None
    changed_event_id: str | None = None
    notes: list[str] = Field(default_factory=list)


class DirectorEventWorkflowBatchResult(BaseModel):
    arc: DirectorEventWorkflowResult
    ambient: DirectorEventWorkflowResult


def update_director_event_workflow(
    *,
    role: DirectorEventRole,
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    recent_summaries: list[SceneSummary] | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    standby_director_npcs: list[DirectorNPCDraft] | None = None,
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventReviewer,
    selector: DirectorEventSelector,
    trace_id: str | None = None,
) -> DirectorEventWorkflowResult:
    active = store.get_active(role)
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

        active_after = store.get_active(role)
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
            role=role,
            mode="review_active",
            active_before=active,
            active_after=active_after,
            review_result=review,
            changed_event_id=changed_event_id,
            notes=notes,
        )

    latest_resolved = store.get_latest_resolved(role)
    if latest_resolved is not None:
        resolved_turn = (
            latest_resolved.resolved_turn
            or latest_resolved.updated_turn
            or latest_resolved.created_turn
        )
        if turn_index <= resolved_turn + DIRECTOR_EVENT_IDLE_COOLDOWN_TURNS:
            return DirectorEventWorkflowResult(
                role=role,
                mode="cooldown",
                active_before=None,
                active_after=None,
                changed_event_id=latest_resolved.event_id,
                notes=["director_event_resolved_cooldown"],
            )

    events = store.list_all()
    standby_director_npcs = standby_director_npcs or []
    selection = selector.select_or_create(
        role=role,
        events=events,
        standby_director_npcs=standby_director_npcs,
        current_state=current_state,
        core_memory=core_memory,
        recent_summaries=recent_summaries or scene_summaries[-5:],
        entity_profiles=entity_profiles or [],
        npc_profiles=npc_profiles or [],
        trace_id=trace_id,
    )

    if selection.director_npc_id is not None:
        if role != "ambient":
            raise ValueError("Arc DirectorEvent cannot use a standby DirectorNPC")
        selected_director_npc = next(
            (
                npc
                for npc in standby_director_npcs
                if npc.draft_id == selection.director_npc_id
            ),
            None,
        )
        if selected_director_npc is None:
            raise ValueError("DirectorEventSelector selected an unknown DirectorNPC")
        if selected_director_npc.name not in selection.actors:
            raise ValueError(
                "DirectorEventSelector must include the selected DirectorNPC in actors"
            )

    if not selection.bridge_anchors:
        raise ValueError("DirectorEventSelector created a new event without bridge_anchors")
    if len(selection.milestones) != 3:
        raise ValueError("DirectorEventSelector created a new event without exactly three milestones")
    event = selection.to_event(
        role=role,
        turn_index=turn_index,
        current_clock=current_state.world_clock,
    )
    changed_event_id = store.save(event)

    return DirectorEventWorkflowResult(
        role=role,
        mode="select_or_create",
        active_before=None,
        active_after=store.get_active(role),
        selection_result=selection,
        changed_event_id=changed_event_id,
        notes=["selected_or_created_active_event"],
    )
