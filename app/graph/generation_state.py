from __future__ import annotations

from operator import add
from typing import Annotated, Any, TypedDict

from app.domain.action.schemas import ActionExtractionResult, ActionParseResult, Anchor
from app.domain.action.self_check import SelfCheckResult
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCWorkflowResult
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfileDirectorView, NPCProfilePublicView
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.recall_memory.schemas import MemoryRecord
from app.domain.retrieval.schemas import CandidateRetrievalResult
from app.domain.roll.schemas import Difficulty, RollConfig, RollResultBatch
from app.domain.roll_modifier.schemas import RollModifierBatch
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermMemory, ShortTermScene
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.workflows.context_assembler import PlotOutlineContext


class GenerationEvent(TypedDict, total=False):
    step: str
    data: dict[str, Any]


class TurnInput(TypedDict, total=False):
    user_input: str
    difficulty: Difficulty
    max_chars: int


class TurnOutput(TypedDict, total=False):
    turn_id: str
    trace_id: str
    turn_index: int
    scene_result: SceneWriterResult
    context: PlotOutlineContext
    status: str


class GenerationState(TurnInput, TurnOutput, total=False):
    events: Annotated[list[GenerationEvent], add]
    trace_id: str

    core_memory: CoreMemory
    current_state: CurrentState
    short_term_memory: ShortTermMemory
    turn_index: int
    next_turn_index: int

    scene_summaries: list[SceneSummary]
    recent_summaries: list[SceneSummary]
    active_director_event: DirectorEvent | None
    recent_resolved_director_event: DirectorEvent | None

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

    director_npc_result: DirectorNPCWorkflowResult | None
    context: PlotOutlineContext
    outline: PlotOutlineResult

    beat_entity_anchors: list[Anchor]
    writer_entity_profile_views: list[EntityProfileUpdateInput]
    writer_npc_public_views: list[NPCProfilePublicView]
    recent_scene: ShortTermScene | None

    scene_result: SceneWriterResult
    pending_turn_status: str


TurnState = GenerationState


class GenerationGraphResult(TypedDict, total=False):
    state: GenerationState
    events: list[GenerationEvent]
    retrieved_memories: list[MemoryRecord]
