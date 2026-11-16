from pydantic import BaseModel, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.director.schemas import DirectorStageMaterial
from app.domain.current_state.schemas import CurrentState, WorldClock
from app.domain.director_event.reviewer import (
    DirectorEventExpiryReviewer,
)
from app.domain.director_event.schemas import (
    DirectorEvent,
    DirectorEventExpiryResult,
)
from app.domain.director_event.store import DirectorEventStore
from app.domain.scene_summary.schemas import SceneSummary


class DirectorEventWorkflowResult(BaseModel):
    event_id: str
    active_before: DirectorEvent
    active_after: DirectorEvent | None = None
    review_result: DirectorEventExpiryResult | None = None
    notes: list[str] = Field(default_factory=list)


def consume_used_event_hooks(
    *, materials: list[DirectorStageMaterial], used_resource_ids: list[str],
    turn_index: int, store: DirectorEventStore,
) -> list[dict[str, str]]:
    """Map accepted Composer receipts back to their admitted Hooks."""
    used = set(used_resource_ids)
    hooks_by_resource: dict[str, set[tuple[str, str]]] = {}
    for material in materials:
        if material.kind != "event" or material.resource_id not in used:
            continue
        event_id = material.payload.get("event_id")
        hook_id = (material.payload.get("hook") or {}).get("hook_id")
        if event_id and hook_id:
            hooks_by_resource.setdefault(material.resource_id, set()).add((event_id, hook_id))

    consumed = []
    for hooks in hooks_by_resource.values():
        # Legacy event-level receipts cannot distinguish multiple admitted Hooks.
        if len(hooks) != 1:
            continue
        event_id, hook_id = next(iter(hooks))
        event = store.get_by_id(event_id)
        if event is None or not event.is_active():
            continue
        if not any(hook.hook_id == hook_id and not hook.consumed for hook in event.hook_pool):
            continue
        store.mark_hooks_consumed(event_id, [hook_id], turn_index=turn_index)
        consumed.append({"event_id": event_id, "hook_id": hook_id})
    return consumed


def resolve_expired_hidden_events(
    *, current_clock: WorldClock, turn_index: int, store: DirectorEventStore,
) -> list[str]:
    resolved = []
    for event in store.list_hidden():
        if event.created_turn >= turn_index or not current_clock.has_reached(event.deadline_clock):
            continue
        if store.resolve_event(
            event.event_id, turn_index=turn_index,
            resolved_reason="事件在潜伏期到期，按无人干预结果结算。",
            final_world_impact=event.world_impact,
        ):
            resolved.append(event.event_id)
    return resolved


def expire_engaged_events_workflow(
    *,
    due_events: list[DirectorEvent],
    user_input: str,
    scene_facts: str,
    current_state: CurrentState,
    core_memory: CoreMemory,
    scene_summaries: list[SceneSummary],
    turn_index: int,
    store: DirectorEventStore,
    reviewer: DirectorEventExpiryReviewer,
    trace_id: str | None = None,
) -> list[DirectorEventWorkflowResult]:
    batch = reviewer.review_batch(
        events=due_events,
        user_input=user_input,
        scene_facts=scene_facts,
        current_state=current_state,
        core_memory=core_memory,
        scene_summaries=scene_summaries,
        trace_id=trace_id,
    )
    by_id = {event.event_id: event for event in due_events}

    results: list[DirectorEventWorkflowResult] = []
    for review in batch.reviews:
        active = by_id[review.event_id]
        if review.player_still_involved:
            reason = "事件到期时玩家仍在实际介入，退出导演后台管理：" + review.reason
            notes = ["resolved_due_event_with_active_player"]
        else:
            reason = "事件到期时玩家已停止介入，结算最终世界影响：" + review.reason
            notes = ["resolved_due_event_after_disengagement"]
        store.resolve_event(
            active.event_id, turn_index=turn_index,
            resolved_reason=reason, final_world_impact=review.final_world_impact,
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
