from __future__ import annotations

from app.domain.core_memory.schemas import CoreMemory
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.runtime.services import RuntimeServices
from app.runtime.session import get_campaign_runtime

from .maintenance_state import SceneSummaryMaintenanceState


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
