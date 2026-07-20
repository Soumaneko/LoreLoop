from __future__ import annotations

import uuid
from typing import Any, Literal

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.runtime.services import RuntimeServices
from app.runtime.session import get_campaign_runtime

from .maintenance_state import (
    MemoryMaintenanceState,
    SceneSummaryMaintenanceState,
)


def maintenance_gateway(
    *,
    turn_index: int | None = None,
    services: RuntimeServices,
):
    return services.gateway(
        turn_index=turn_index,
        session_type="maintenance",
    )


def prepare_scene_summary_state(
    trace_id: str,
    turn_index: int,
    *,
    core_memory: CoreMemory,
    threshold: int = 10,
) -> SceneSummaryMaintenanceState:
    services = get_campaign_runtime().services
    return {
        "events": [],
        "runtime_services": services,
        "trace_id": trace_id,
        "turn_index": turn_index,
        "core_memory": core_memory,
        "threshold": threshold,
        "result": SceneSummaryMaintenanceResult(),
    }

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
    services = get_campaign_runtime().services
    return {
        "events": [],
        "runtime_services": services,
        "trace_id": trace_id,
        "turn_index": turn_index,
        "current_state": current_state,
        "core_memory": core_memory,
        "turn_threshold": turn_threshold,
        "force": force,
        "through_turn": turn_index if through_turn is None else through_turn,
    }


def prepare_turn_memory_maintenance_state(
    current_state: CurrentState,
    state_before_update: CurrentState,
    scene_facts_json: str,
    trace_id: str,
    turn_index: int,
    *,
    core_memory: CoreMemory,
    turn_threshold: int = 5,
) -> MemoryMaintenanceState:
    state = prepare_memory_batch_state(
        current_state,
        trace_id,
        turn_index,
        core_memory=core_memory,
        turn_threshold=turn_threshold,
    )
    state.update({
        "state_before_update": state_before_update,
        "scene_facts_json": scene_facts_json,
    })
    return state


def prepare_manual_batch_state() -> MemoryMaintenanceState:
    services = get_campaign_runtime().services
    stores = services.stores
    trace_id = str(uuid.uuid4())
    with stores.current_state() as state_store:
        current_state = state_store.load_or_default()
    core_memory = stores.core_memory().load_required()
    with stores.pending_memory() as pending_store:
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
    services = get_campaign_runtime().services
    stores = services.stores
    trace_id = str(uuid.uuid4())
    core_memory = stores.core_memory().load_required()
    with stores.memory() as store:
        active_count = len(store.list_active())
        total_count = store.count()
    return (
        {
            "events": [],
            "runtime_services": services,
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
