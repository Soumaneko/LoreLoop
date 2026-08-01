from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.intervention_reviewer import (
    DirectorEventInterventionReviewer,
)
from app.domain.director_event.intervention_planner import (
    DirectorEventInterventionPlanner,
)
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventInterventionPlan,
    DirectorEventInterventionReviewResult,
    DirectorEventReviewResult,
)
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


class DirectorEventWorkflowResult(BaseModel):
    event_id: str
    active_before: DirectorEvent
    active_after: DirectorEvent | None = None
    review_result: DirectorEventReviewResult | None = None
    intervention_review_result: DirectorEventInterventionReviewResult | None = None
    intervention_plan_result: DirectorEventInterventionPlan | None = None
    notes: list[str] = Field(default_factory=list)


def review_director_event_workflow(
    *,
    active_event: DirectorEvent,
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    recent_summaries: list[SceneSummary] | None = None,
    entity_profiles: list[EntityProfileUpdateInput] | None = None,
    npc_profiles: list[NPCProfileDirectorView] | None = None,
    entity_mentions: list[str] | None = None,
    npc_mentions: list[str] | None = None,
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventReviewer,
    intervention_reviewer: DirectorEventInterventionReviewer | None = None,
    intervention_planner: DirectorEventInterventionPlanner | None = None,
    trace_id: str | None = None,
    guidance: str | None = None,
) -> DirectorEventWorkflowResult:
    active = store.get_by_id(active_event.event_id)
    if active is None or active.status != "active":
        raise ValueError("review workflow requires an active DirectorEvent")
    role = active.role
    if active is not None:
        review_kwargs = dict(
            active_event=active,
            user_input=user_input,
            scene_facts=scene_facts,
            current_state=current_state,
            core_memory=core_memory,
            scene_summaries=scene_summaries,
            trace_id=trace_id,
            guidance=guidance,
        )
        review: DirectorEventReviewResult | None = None
        intervention_review: DirectorEventInterventionReviewResult | None = None
        if active.route_mode == "intervention":
            if intervention_reviewer is None:
                raise RuntimeError(
                    "intervention route requires an intervention reviewer"
                )
            intervention_review = intervention_reviewer.review(
                active_event=active,
                user_input=user_input,
                scene_facts=scene_facts,
                current_state=current_state,
                core_memory=core_memory,
                trace_id=trace_id,
                guidance=guidance,
            )
            exposure = active.exposure
            new_player_contact = intervention_review.new_player_contact
        else:
            review = reviewer.review(**review_kwargs)
            exposure = review.exposure
            new_player_contact = review.new_player_contact
        intervention_plan_result = None
        auto_progress_result = None
        auto_failed_intervention = False

        if review is not None and review.action == "redirect":
            if intervention_planner is None:
                raise RuntimeError("redirect requires an intervention planner")
            intervention_plan_result = intervention_planner.plan(
                active_event=active,
                redirect_review=review,
                user_input=user_input,
                scene_facts=scene_facts,
                current_state=current_state,
                core_memory=core_memory,
                recent_summaries=recent_summaries or scene_summaries[-5:],
                entity_profiles=entity_profiles or [],
                npc_profiles=npc_profiles or [],
                entity_mentions=entity_mentions or [],
                npc_mentions=npc_mentions or [],
                trace_id=trace_id,
                guidance=guidance,
            )

        store.append_player_contact(
            active.event_id,
            exposure=exposure,
            new_player_contact=new_player_contact,
            turn_index=turn_index,
        )

        if review is not None and review.action == "redirect":
            store.redirect_active(
                active.event_id,
                turn_index=turn_index,
                current_clock=current_state.world_clock,
                redirect_reason=review.reason,
                evidence=review.evidence,
                plan=intervention_plan_result,
            )
        elif active.route_mode == "intervention":
            if intervention_review is None:
                raise RuntimeError("intervention route requires an intervention review")
            if intervention_review.status in {"success", "failure"}:
                store.resolve_intervention(
                    active.event_id,
                    outcome=intervention_review.status,
                    turn_index=turn_index,
                    resolved_reason=intervention_review.reason,
                )
            else:
                store.touch(active.event_id, turn_index=turn_index)
                auto_failed_intervention = store.auto_fail_intervention_due(
                    active.event_id,
                    turn_index=turn_index,
                    current_clock=current_state.world_clock,
                )
        elif review is not None and review.action == "resolve":
            store.resolve_active(
                active.event_id,
                turn_index=turn_index,
                resolved_reason=review.resolved_reason or review.reason,
                resolution_kind="alternate",
            )
        elif review is not None and review.action == "advance":
            if active.progress_index >= 3:
                store.resolve_active(
                    active.event_id,
                    turn_index=turn_index,
                    resolved_reason=(
                        review.resolved_reason or review.reason
                    ),
                    resolution_kind="default",
                    resolved_state=active.milestones[2],
                )
            else:
                store.advance_progress(
                    active.event_id,
                    turn_index=turn_index,
                    current_clock=current_state.world_clock,
                )
                advanced = store.get_by_id(active.event_id)
                if advanced is not None and advanced.progress_index >= 3:
                    store.resolve_active(
                        active.event_id,
                        turn_index=turn_index,
                        resolved_reason=(
                            review.resolved_reason or review.reason
                        ),
                        resolution_kind="default",
                        resolved_state=advanced.milestones[2],
                    )
        else:
            store.touch(active.event_id, turn_index=turn_index)
            auto_progress_result = store.auto_advance_due(
                active.event_id,
                turn_index=turn_index,
                current_clock=current_state.world_clock,
            )

        active_after = store.get_active(role)
        notes = ["reviewed_existing_active_event"]
        if intervention_review is not None:
            if intervention_review.status in {"success", "failure"}:
                notes.append("resolved_active_event")
            else:
                notes.append("kept_active_event")
        elif review is not None and review.action == "resolve":
            notes.append("resolved_active_event")
        elif review is not None and review.action == "advance":
            notes.append("advanced_active_event")
        elif review is not None and review.action == "redirect":
            notes.append("redirected_active_event")
        else:
            notes.append("kept_active_event")
        if auto_progress_result == "advanced":
            notes.append("auto_advanced_active_event")
        elif auto_progress_result == "resolved":
            notes.append("auto_resolved_active_event")
        if auto_failed_intervention:
            notes.append("auto_failed_intervention")
        return DirectorEventWorkflowResult(
            event_id=active.event_id,
            active_before=active,
            active_after=active_after,
            review_result=review,
            intervention_review_result=intervention_review,
            intervention_plan_result=intervention_plan_result,
            notes=notes,
        )
