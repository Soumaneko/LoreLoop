from __future__ import annotations

from dataclasses import dataclass, field
from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.continuity import ContinuityProjection
from app.domain.current_state.schemas import CurrentState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.director import DirectorDecisionBatch, DirectorSkillExecutionBatch
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCExtractionResult, NPCProfile
from app.domain.npc_profile.updater import NPCProfileUpdateWarning
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.session import PendingOpening, PendingTurn
from app.runtime.services import RuntimeServices

from .confirmation_cache import ConfirmationCacheData


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
    clear_short_term_before_save: bool = False
    clear_confirmed_history_before_save: bool = False
    clear_continuity_before_save: bool = False
    confirm_started_extra: dict[str, Any] = field(default_factory=dict)
    finish_extra: dict[str, Any] = field(default_factory=dict)
    complete_initialization: bool = False


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
    cache: ConfirmationCacheData
    director_npc_profile_ids: list[str]
    confirmed_director_npc_drafts: list[DirectorNPCDraft]
    state_before_update: CurrentState
    confirmation_scene_text: str
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
    npc_extraction: NPCExtractionResult | None
    action_preview: ActionPreviewResult
    continuity_projection: ContinuityProjection
    updated_npc_profile_ids: list[str]
    new_npc_profiles: list[NPCProfile]
    npc_update_warnings: list[NPCProfileUpdateWarning]
    director_decision: DirectorDecisionBatch
    director_executions: DirectorSkillExecutionBatch
    director_event_reviews: list[dict[str, Any]]
