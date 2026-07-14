from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.recall_memory.maintenance import (
    BatchClassifyPendingMemoryWorkflowResult,
    MaintainMemoriesWorkflowResult,
)
from app.domain.recall_memory.schemas import PendingMemoryItem
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
    scene_id: str
    scene_text: str
    current_state: CurrentState
    core_memory: CoreMemory
    threshold: int
    result: SceneSummaryMaintenanceResult


class MemoryMaintenanceState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[MaintenanceEvent], add]
    trace_id: str
    turn_index: int
    current_state: CurrentState
    core_memory: CoreMemory
    turn_threshold: int
    force: bool
    through_turn: int | None
    pending_items: list[PendingMemoryItem]
    pending_total: int
    unclassified_turn_count: int
    batch_result: BatchClassifyPendingMemoryWorkflowResult
    maintenance_result: MaintainMemoriesWorkflowResult


class DirectorNPCRestockState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[MaintenanceEvent], add]
    trace_id: str
    turn_index: int
    core_memory: CoreMemory
    protagonist_name: str
    created_drafts: list[DirectorNPCDraft]
