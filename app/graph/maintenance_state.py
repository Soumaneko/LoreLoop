from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.core_memory.schemas import CoreMemory
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.runtime.services import RuntimeServices


class MaintenanceEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class SceneSummaryMaintenanceState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[MaintenanceEvent], add]
    trace_id: str
    turn_index: int
    core_memory: CoreMemory
    threshold: int
    result: SceneSummaryMaintenanceResult
