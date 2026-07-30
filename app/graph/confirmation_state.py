from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.director_event.workflows import DirectorEventWorkflowBatchResult
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.entity.schemas import EntityExtractionResult, EntityProfile
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.session import PendingOpening, PendingTurn
from app.runtime.services import RuntimeServices


class ConfirmationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class ConfirmationNodeError(TypedDict):
    node: str
    exception: Exception


@dataclass
class ConfirmationPipelineInput:
    turn_id: str
    pending: PendingTurn | PendingOpening
    state_before: CurrentState
    scene_result: SceneWriterResult
    user_input: str
    turn_index: int
    stale_message: str
    update_director_event: bool = True
    handle_director_npc: bool = True
    director_npc_restock_sync: bool = False
    clear_short_term_before_save: bool = False
    clear_confirmed_history_before_save: bool = False
    dry_run_memory_reason: str = (
        "DB writes are disabled; pending memory candidates were extracted but not saved."
    )
    confirm_started_extra: dict[str, Any] = field(default_factory=dict)
    finish_extra: dict[str, Any] = field(default_factory=dict)
    complete_initialization: bool = False


class ConfirmationCache(TypedDict, total=False):
    key: str
    scene_facts: SceneFactSet
    updated_state: CurrentState
    state_change_set: StateChangeSet
    state_update_warnings: list[StateChangeWarning]
    turn_summary: SceneSummary
    entity_extraction: EntityExtractionResult | None
    npc_extraction: NPCExtractionResult | None
    npc_director_events: list[DirectorEvent]
    action_preview: ActionPreviewResult
    director_event_result: DirectorEventWorkflowBatchResult | None
    director_event_promoted_npc_profile_ids: list[str]
    updated_entity_ids: list[str]
    updated_npc_profile_ids: list[str]
    existing_entity_profiles: list[EntityProfile]
    existing_npc_profiles: list[NPCProfile]
    director_npc_profile_id: str | None
    confirmed_director_npc_draft: DirectorNPCDraft | None
    director_npc_restock_scheduled: bool
    director_npc_restock_job_id: str | None
    director_npc_restock_operation_id: str | None
    setting_seed_maintenance_scheduled: bool
    setting_seed_maintenance_job_id: str | None
    setting_seed_maintenance_operation_id: str | None
    director_npc_last_dispatch_turn_set: bool
    turn_summary_saved: bool
    turn_summary_id: str
    state_saved: bool
    short_term_saved: bool
    confirmed_history_saved: bool
    scene_summary_future: Future[Any]
    scene_summary_job_id: str | None
    scene_summary_operation_id: str | None
    scene_summary_scheduled: bool
    memory_batch_scheduled: bool
    memory_batch_job_id: str | None
    memory_batch_operation_id: str | None


class ConfirmationState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[ConfirmationEvent], add]
    node_errors: Annotated[list[ConfirmationNodeError], add]
    pipeline_input: ConfirmationPipelineInput
    write_db: bool
    journaled: bool
    journal_completed: bool
    scene_text: str | None
    pending: PendingTurn | PendingOpening
    turn_id: str
    trace_id: str
    effective_scene_text: str
    cache: ConfirmationCache
    director_npc_profile_id: str | None
    confirmed_director_npc_draft: DirectorNPCDraft | None
    director_npc_restock_scheduled: bool
    state_before_update: CurrentState
    confirmation_scene_text: str
    existing_entity_profiles: list[EntityProfile]
    existing_npc_profiles: list[NPCProfile]
    core_memory: CoreMemory
    scene_summaries: list[SceneSummary]
    recent_summaries: list[SceneSummary]
    selection_summaries: list[SceneSummary]
    turn_summary: SceneSummary
    scene_facts: SceneFactSet
    scene_facts_json: str
    updated_state: CurrentState
    state_change_set: StateChangeSet
    state_update_warnings: list[StateChangeWarning]
    entity_extraction: EntityExtractionResult | None
    npc_extraction: NPCExtractionResult | None
    npc_director_events: list[DirectorEvent]
    action_preview: ActionPreviewResult
    updated_entity_ids: list[str]
    updated_npc_profile_ids: list[str]
    director_event_result: DirectorEventWorkflowBatchResult | None
    director_event_promoted_npc_profile_ids: list[str]
