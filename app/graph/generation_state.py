from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action.schemas import ActionExtractionResult, ActionParseResult, Anchor
from app.domain.action.self_check import SelfCheckResult
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent, DirectorEventConsumption
from app.domain.director_npc.schemas import DirectorNPCWorkflowResult
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView, NPCProfilePublicView
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
from app.domain.retrieval.schemas import CandidateRetrievalResult
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
    active_director_events: list[DirectorEvent]
    director_event_consumptions: list[DirectorEventConsumption]

    matched_anchors: list[Anchor]
    action_entity_profile_views: list[EntityProfileUpdateInput]
    action_npc_director_views: list[NPCProfileDirectorView]

    context_facts: ContextFactSet
    context_fact_memories: CandidateRetrievalResult | None
    context_extract_error: str | None

    action_extraction_result: ActionExtractionResult
    raw_action_parse_result: ActionParseResult
    action_self_check_result: SelfCheckResult | None
    action_parse_result: ActionParseResult

    retrieved_memories: CandidateRetrievalResult
    roll_config: RollConfig
    roll_actions: Any
    modifier_batch: RollModifierBatch
    roll_results: RollResultBatch
    turn_resolution: TurnResolutionResult
    player_resolution: PlayerTurnResolution

    director_npc_result: DirectorNPCWorkflowResult | None
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
    composer_entity_profile_views: list[EntityProfileUpdateInput]
    composer_npc_public_views: list[NPCProfilePublicView]
    recent_scene: ShortTermScene | None

    scene_result: SceneWriterResult
    pending_turn_status: str
