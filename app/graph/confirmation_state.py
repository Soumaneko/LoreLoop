from __future__ import annotations

from concurrent.futures import Future
from dataclasses import dataclass, field
from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.workflows import DirectorEventWorkflowBatchResult
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.entity.schemas import EntityExtractionResult, EntityProfile
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile
from app.domain.recall_memory.schemas import MemoryDraftExtractResult
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
    dry_run_memory_reason: str = (
        "DB writes are disabled; pending memory candidates were extracted but not saved."
    )
    confirm_started_extra: dict[str, Any] = field(default_factory=dict)
    finish_extra: dict[str, Any] = field(default_factory=dict)


class ConfirmationCache(TypedDict, total=False):
    key: str
    scene_facts: SceneFactSet
    updated_state: CurrentState
    pending_memory_result: MemoryDraftExtractResult
    entity_extraction: EntityExtractionResult | None
    npc_extraction: NPCExtractionResult | None
    npc_director_events: list[DirectorEvent]
    action_preview: ActionPreviewResult
    director_event_result: DirectorEventWorkflowBatchResult | None
    updated_entity_ids: list[str]
    updated_npc_profile_ids: list[str]
    existing_entity_profiles: list[EntityProfile]
    existing_npc_profiles: list[NPCProfile]
    director_npc_profile_id: str | None
    confirmed_director_npc_draft: DirectorNPCDraft | None
    director_npc_restock_scheduled: bool
    director_npc_last_dispatch_turn_set: bool
    pending_memory_metadata_filled: bool
    state_saved: bool
    short_term_saved: bool
    pending_memories_saved: bool
    saved_pending_ids: list[str]
    scene_summary_future: Future[Any]
    scene_summary_scheduled: bool
    memory_batch_scheduled: bool


class ConfirmationState(TypedDict, total=False):
    runtime_services: RuntimeServices
    events: Annotated[list[ConfirmationEvent], add]
    node_errors: Annotated[list[ConfirmationNodeError], add]
    pipeline_input: ConfirmationPipelineInput
    write_db: bool
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
    scene_facts: SceneFactSet
    scene_facts_json: str
    updated_state: CurrentState
    pending_memory_result: MemoryDraftExtractResult
    entity_extraction: EntityExtractionResult | None
    npc_extraction: NPCExtractionResult | None
    npc_director_events: list[DirectorEvent]
    action_preview: ActionPreviewResult
    updated_entity_ids: list[str]
    updated_npc_profile_ids: list[str]
    director_event_result: DirectorEventWorkflowBatchResult | None
