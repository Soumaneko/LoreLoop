from app.core.llm import LLMGateway
from app.domain.action.schemas import ActionParseResult
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState, NPCState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.dispatcher import DirectorNPCDispatcher
from app.domain.director_npc.schemas import (
    DirectorNPCDraft,
    DirectorNPCWorkflowResult,
    DispatchedDirectorNPCCandidate,
)
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.entity.store import EntityStore
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.short_term_memory.schemas import ShortTermMemory
from app.domain.turn_resolution.schemas import TurnResolutionResult


DIRECTOR_NPC_HARD_MAX_TURNS = 8
DIRECTOR_NPC_REPEAT_BLOCK_TURNS = 2
DIRECTOR_NPC_STANDBY_TARGET = 3
PRESSURE_TYPES = {"escalate_pressure", "complication"}
BLOCKING_ACTION_TYPES = {"attack", "defend", "hide_or_stealth", "cast_or_use_power"}


def maybe_director_npc_workflow(
    *,
    core_memory: CoreMemory,
    current_state: CurrentState,
    action_parse_result: ActionParseResult,
    turn_resolution: TurnResolutionResult,
    short_term_memory: ShortTermMemory,
    recent_summaries: list[SceneSummary],
    director_event: DirectorEvent | None,
    turn_index: int,
    director_npc_store: DirectorNPCStore,
    entity_store: EntityStore,
    creator_gateway: LLMGateway,
    dispatcher_gateway: LLMGateway,
    trace_id: str | None = None,
) -> DirectorNPCWorkflowResult:
    created_drafts = restock_director_npc_standby(
        core_memory=core_memory,
        protagonist_name=current_state.player.name,
        turn_index=turn_index,
        director_npc_store=director_npc_store,
        entity_store=entity_store,
        creator_gateway=creator_gateway,
        trace_id=trace_id,
    )
    standby = director_npc_store.list_standby()

    trigger_reason = _director_npc_trigger_reason(
        action_parse_result=action_parse_result,
        turn_resolution=turn_resolution,
        short_term_memory=short_term_memory,
        turn_index=turn_index,
        last_dispatch_turn=director_npc_store.get_meta_int("last_dispatch_turn", 0),
    )
    if trigger_reason is None:
        return DirectorNPCWorkflowResult(
            created_drafts=created_drafts,
            skipped_reason="trigger conditions not met",
        )

    existing_candidates = _existing_recall_candidates(current_state.npcs)
    dispatched_director_npc_candidates = _dispatched_director_npc_candidates(
        director_npc_store,
        entity_store,
    )
    dispatch = DirectorNPCDispatcher(dispatcher_gateway).dispatch(
        core_memory=core_memory,
        current_state=current_state,
        recent_summaries=recent_summaries,
        director_event=director_event,
        standby_npcs=standby,
        existing_candidates=existing_candidates,
        dispatched_director_npc_candidates=dispatched_director_npc_candidates,
        trigger_reason=trigger_reason,
        trace_id=trace_id,
    )
    if not _dispatch_references_valid(
        dispatch,
        standby,
        existing_candidates,
        dispatched_director_npc_candidates,
    ):
        dispatch = dispatch.model_copy(
            update={
                "action": "none",
                "selected_id": None,
                "selected_name": None,
                "reason": "dispatcher selected an unknown NPC candidate",
                "contextual_presence": None,
                "immediate_intent": None,
                "first_pressure": None,
                "scene_function": None,
            }
        )
    if dispatch.action != "none":
        director_npc_store.set_meta_int("last_dispatch_turn", turn_index)
    return DirectorNPCWorkflowResult(
        created_drafts=created_drafts,
        dispatch=dispatch,
        trigger_reason=trigger_reason,
    )


def director_npc_standby_missing_count(
    director_npc_store: DirectorNPCStore,
) -> int:
    return max(0, DIRECTOR_NPC_STANDBY_TARGET - len(director_npc_store.list_standby()))


def create_director_npc_standby(
    *,
    core_memory: CoreMemory,
    protagonist_name: str,
    turn_index: int,
    director_npc_store: DirectorNPCStore,
    entity_store: EntityStore,
    creator_gateway: LLMGateway,
    trace_id: str | None = None,
) -> DirectorNPCDraft:
    created = DirectorNPCCreator(creator_gateway).create(
        core_memory=core_memory,
        existing_npcs=entity_store.list_all(entity_type="npc"),
        reserved_npcs=[
            {"name": npc.name, "role": npc.role}
            for npc in director_npc_store.list_standby()
        ],
        protagonist_name=protagonist_name,
        turn_index=turn_index,
        trace_id=trace_id,
    )
    director_npc_store.save(created)
    return created


def restock_director_npc_standby(
    *,
    core_memory: CoreMemory,
    protagonist_name: str,
    turn_index: int,
    director_npc_store: DirectorNPCStore,
    entity_store: EntityStore,
    creator_gateway: LLMGateway,
    trace_id: str | None = None,
) -> list[DirectorNPCDraft]:
    created_drafts: list[DirectorNPCDraft] = []
    for _ in range(director_npc_standby_missing_count(director_npc_store)):
        created_drafts.append(
            create_director_npc_standby(
                core_memory=core_memory,
                protagonist_name=protagonist_name,
                turn_index=turn_index,
                director_npc_store=director_npc_store,
                entity_store=entity_store,
                creator_gateway=creator_gateway,
                trace_id=trace_id,
            )
        )
    return created_drafts


def _director_npc_trigger_reason(
    *,
    action_parse_result: ActionParseResult,
    turn_resolution: TurnResolutionResult,
    short_term_memory: ShortTermMemory,
    turn_index: int,
    last_dispatch_turn: int,
) -> str | None:
    if last_dispatch_turn and turn_index <= last_dispatch_turn + DIRECTOR_NPC_REPEAT_BLOCK_TURNS:
        return None
    if _has_blocking_action(action_parse_result):
        return None
    if last_dispatch_turn and turn_index >= last_dispatch_turn + DIRECTOR_NPC_HARD_MAX_TURNS:
        return f"{DIRECTOR_NPC_HARD_MAX_TURNS} turns passed since last director NPC dispatch"
    if not _recent_pressure_exists(short_term_memory, turn_resolution):
        return "no pressure or complication in the last 5 turns including this turn"
    return None


def _has_blocking_action(action_parse_result: ActionParseResult) -> bool:
    for action in action_parse_result.atomic_actions:
        if action.risk_level == "high":
            return True
        if action.action_type in BLOCKING_ACTION_TYPES:
            return True
    return False


def _recent_pressure_exists(
    short_term_memory: ShortTermMemory,
    turn_resolution: TurnResolutionResult,
) -> bool:
    recent_types = [
        scene.narrative_type
        for scene in short_term_memory.recent_scenes[-4:]
        if scene.narrative_type
    ]
    recent_types.append(turn_resolution.selected_type)
    if len(recent_types) < 5:
        return True
    return any(narrative_type in PRESSURE_TYPES for narrative_type in recent_types)


def _existing_recall_candidates(npcs: list[NPCState]) -> list[NPCState]:
    return [
        npc
        for npc in npcs
        if npc.absent_turns >= 1
    ]


def _dispatched_director_npc_candidates(
    director_npc_store: DirectorNPCStore,
    entity_store: EntityStore,
) -> list[DispatchedDirectorNPCCandidate]:
    candidates = []
    for draft in director_npc_store.list_all():
        if draft.status != "dispatched":
            continue
        public_profile = entity_store.get_by_name(draft.name)
        candidates.append(
            DispatchedDirectorNPCCandidate(
                selected_id=draft.draft_id,
                selected_name=draft.name,
                public_entity_profile=(
                    public_profile.semantic_view()
                    if public_profile is not None
                    else None
                ),
                director_private_profile=draft.private_view(),
            )
        )
    return candidates


def _dispatch_references_valid(
    dispatch,
    standby: list,
    existing_candidates: list[NPCState],
    dispatched_director_npc_candidates: list[DispatchedDirectorNPCCandidate],
) -> bool:
    if dispatch.action == "none":
        return True
    if dispatch.action == "dispatch_standby":
        return any(
            npc.draft_id == dispatch.selected_id and npc.name == dispatch.selected_name
            for npc in standby
        )
    if dispatch.action == "recall_existing":
        return any(
            npc.name == dispatch.selected_id == dispatch.selected_name
            for npc in existing_candidates
        ) or any(
            npc.selected_id == dispatch.selected_id
            and npc.selected_name == dispatch.selected_name
            for npc in dispatched_director_npc_candidates
        )
    return False
