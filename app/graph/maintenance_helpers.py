from __future__ import annotations

import uuid
from typing import Any, Literal

from app.core.llm.config import create_gateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.runtime.stores import (
    core_memory_store,
    current_state_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
)

from .maintenance_state import (
    MemoryMaintenanceState,
    SceneSummaryMaintenanceState,
)


def maintenance_gateway(*, turn_index: int | None = None):
    gateway = create_gateway()
    context: dict[str, Any] = {"session_type": "maintenance"}
    if turn_index is not None:
        context["turn_index"] = turn_index
    gateway.set_trace_context(**context)
    return gateway


def prepare_scene_summary_state(
    scene_text: str,
    current_state: CurrentState,
    scene_id: str,
    turn_index: int,
    trace_id: str,
    *,
    core_memory: CoreMemory,
    threshold: int = 10,
) -> SceneSummaryMaintenanceState:
    return {
        "events": [],
        "trace_id": trace_id,
        "turn_index": turn_index,
        "scene_id": scene_id,
        "scene_text": scene_text,
        "current_state": current_state,
        "core_memory": core_memory,
        "threshold": threshold,
        "result": SceneSummaryMaintenanceResult(),
    }


def scene_summary_route(
    state: SceneSummaryMaintenanceState,
) -> Literal["scene_summary_condense", "done"]:
    with scene_summary_store() as store:
        if store.should_condense(1, threshold=state.get("threshold", 10)):
            return "scene_summary_condense"
    return "done"


def prepare_memory_batch_state(
    current_state: CurrentState,
    trace_id: str,
    turn_index: int,
    *,
    core_memory: CoreMemory,
    turn_threshold: int = 5,
    force: bool = False,
    through_turn: int | None = None,
) -> MemoryMaintenanceState:
    return {
        "events": [],
        "trace_id": trace_id,
        "turn_index": turn_index,
        "current_state": current_state,
        "core_memory": core_memory,
        "turn_threshold": turn_threshold,
        "force": force,
        "through_turn": turn_index if through_turn is None else through_turn,
    }


def prepare_manual_batch_state() -> MemoryMaintenanceState:
    trace_id = str(uuid.uuid4())
    with current_state_store() as state_store:
        current_state = state_store.load_or_default()
    core_memory = core_memory_store().load_required()
    with pending_memory_store() as pending_store:
        pending_items = pending_store.list_unclassified()
        turn_count = pending_store.unclassified_turn_count()
        pending_total = pending_store.count()
    turn_index = max((item.turn_index for item in pending_items), default=0)
    state = prepare_memory_batch_state(
        current_state,
        trace_id,
        turn_index,
        core_memory=core_memory,
        turn_threshold=1,
        force=True,
        through_turn=turn_index,
    )
    state.update(
        {
            "pending_items": pending_items,
            "unclassified_turn_count": turn_count,
            "pending_total": pending_total,
        }
    )
    return state


def prepare_manual_memory_maintenance_state() -> tuple[MemoryMaintenanceState, int, int]:
    trace_id = str(uuid.uuid4())
    core_memory = core_memory_store().load_required()
    with memory_store() as store:
        active_count = len(store.list_active())
        total_count = store.count()
    return (
        {
            "events": [],
            "trace_id": trace_id,
            "turn_index": 0,
            "core_memory": core_memory,
            "through_turn": None,
        },
        active_count,
        total_count,
    )


def memory_batch_route(
    state: MemoryMaintenanceState,
) -> Literal["memory_maintain", "done"]:
    result = state["batch_result"]
    return "memory_maintain" if result.triggered else "done"
