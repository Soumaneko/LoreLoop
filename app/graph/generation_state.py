from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action.schemas import ActionExtractionResult, ActionParseResult, Anchor
from app.domain.action.self_check import SelfCheckResult
from app.domain.context_facts.schemas import (
    ContextFactSet,
    ContextHistoryEvidenceSet,
    ContextSearchPlan,
)
from app.domain.core_memory.schemas import CoreMemory
from app.domain.continuity import ContinuitySelection
from app.domain.current_state.schemas import CurrentState
from app.domain.director.schemas import DirectorDecisionBatch, DirectorStageResult
from app.domain.npc_profile.schemas import NPCProfile, NPCProfileDirectorView, NPCProfilePublicView
from app.domain.narrative_composer.schemas import (
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.narrative_canon_query.schemas import (
    CanonEntityReference,
    NarrativeCanonQuerySet,
)
from app.domain.canon_evidence.schemas import CanonEvidenceSet
from app.domain.narrative_consistency.schemas import (
    NarrativeConsistencyReview,
    NarrativeConsistencyWarning,
)
from app.domain.narrative_rewrite.schemas import RewriteStrategyDecision
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.roll.schemas import Difficulty, RollConfig, RollResultBatch
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory, ShortTermScene
from app.domain.turn_resolution.schemas import PlayerTurnResolution, TurnResolutionResult
from app.runtime.services import RuntimeServices
from app.runtime.session import CampaignRuntime


class GenerationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class GenerationState(TypedDict, total=False):
    runtime_services: RuntimeServices
    runtime: CampaignRuntime
    events: Annotated[list[GenerationEvent], add]
    user_input: str
    difficulty: Difficulty
    max_chars: int
    trace_id: str
    turn_id: str
    group_id: str
    version_index: int
    status: str

    core_memory: CoreMemory
    current_state: CurrentState
    short_term_memory: ShortTermMemory
    turn_index: int
    next_turn_index: int

    scene_summaries: list[SceneSummary]
    recent_summaries: list[SceneSummary]
    matched_anchors: list[Anchor]
    action_npc_profiles: list[NPCProfile]
    action_npc_director_views: list[NPCProfileDirectorView]
    context_fact_npc_profiles: list[NPCProfile]

    context_facts: ContextFactSet
    context_search_plan: ContextSearchPlan | None
    context_history_evidence: ContextHistoryEvidenceSet | None
    continuity_selection: ContinuitySelection | None
    context_extract_error: str | None

    action_extraction_result: ActionExtractionResult
    raw_action_parse_result: ActionParseResult
    action_self_check_result: SelfCheckResult | None
    action_parse_result: ActionParseResult
    touched_event_candidates: list[str]

    roll_config: RollConfig
    roll_actions: Any
    modifier_batch: RollModifierBatch
    roll_results: RollResultBatch
    turn_resolution: TurnResolutionResult
    player_resolution: PlayerTurnResolution

    director_decision: DirectorDecisionBatch
    director_stage: DirectorStageResult | None
    context: NarrativeComposerContext
    narrative_composition: NarrativeComposerResult
    canon_entity_catalog: list[CanonEntityReference]
    canon_query_set: NarrativeCanonQuerySet
    canon_evidence_set: CanonEvidenceSet
    narrative_consistency_review: NarrativeConsistencyReview
    narrative_consistency_warnings: list[NarrativeConsistencyWarning]
    narrative_revision_instruction: str
    rewrite_feedback: str
    rewrite_source_scene_text: str
    rewrite_strategy_decision: RewriteStrategyDecision
    outline: PlotOutlineResult

    beat_entity_anchors: list[Anchor]
    composer_npc_public_views: list[NPCProfilePublicView]
    recent_scene: ShortTermScene | None

    scene_result: SceneWriterResult
    pending_turn_status: str
