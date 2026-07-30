from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.core_memory.schemas import CoreMemory
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.domain.world_setting.workflows import (
    SettingSeedExtractionWorkflowResult,
)
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


class DirectorNPCMaintenanceState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[MaintenanceEvent], add]
    trace_id: str
    turn_index: int
    core_memory: CoreMemory
    protagonist_name: str
    scene_text: str
    scene_facts_json: str
    new_npc_profile_ids: list[str]
    setting_seed_result: SettingSeedExtractionWorkflowResult
    created_drafts: list[DirectorNPCDraft]
