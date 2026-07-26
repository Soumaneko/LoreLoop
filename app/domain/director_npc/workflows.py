import random

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.creative_brief import (
    draw_director_npc_creative_brief,
)
from app.domain.director_npc.dispatcher import DirectorNPCDispatcher
from app.domain.director_npc.schemas import (
    DirectorNPCDraft,
    DirectorNPCWorkflowResult,
    DispatchedDirectorNPCCandidate,
    ExistingNPCCandidate,
    DirectorNPCDispatchDecision,
    DirectorNPCPlotInjection,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.preparer import SettingAnchorPreparer
from app.domain.world_setting.schemas import SettingAnchorPacket
from app.domain.world_setting.store import WorldSettingStore
from app.domain.world_setting.workflows import maintain_setting_seeds


DIRECTOR_NPC_FIRST_FORCED_TURN = 1
DIRECTOR_NPC_REPEAT_BLOCK_TURNS = 2
DIRECTOR_NPC_STANDBY_TARGET = 3


def maybe_director_npc_workflow(
    *,
    core_memory: CoreMemory,
    current_state: CurrentState,
    recent_summaries: list[SceneSummary],
    director_events: list[DirectorEvent],
    user_input: str,
    turn_index: int,
    director_npc_store: DirectorNPCStore,
    npc_profile_store: NPCProfileStore | None = None,
    dispatcher_gateway: LLMGateway,
    trace_id: str | None = None,
) -> DirectorNPCWorkflowResult:
    owned_profile_store = npc_profile_store or NPCProfileStore(":memory:")
    standby = director_npc_store.list_standby()

    trigger_reason = _director_npc_trigger_reason(
        turn_index=turn_index,
        last_dispatch_turn=director_npc_store.get_meta_int("last_dispatch_turn", 0),
    )
    if trigger_reason is None:
        return DirectorNPCWorkflowResult(
            skipped_reason="trigger conditions not met",
        )

    existing_candidates = _existing_recall_candidates(owned_profile_store.list_all())
    dispatched_director_npc_candidates = _dispatched_director_npc_candidates(
        director_npc_store,
        owned_profile_store,
    )
    dispatch = DirectorNPCDispatcher(dispatcher_gateway).dispatch(
        core_memory=core_memory,
        current_state=current_state,
        recent_summaries=recent_summaries,
        director_events=director_events,
        standby_npcs=standby,
        existing_candidates=existing_candidates,
        dispatched_director_npc_candidates=dispatched_director_npc_candidates,
        user_input=user_input,
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
            }
        )
    if dispatch.action != "none":
        pass  # last_dispatch_turn is persisted during confirmation, not generation
    return DirectorNPCWorkflowResult(
        dispatch=dispatch,
        plot_injection=_build_plot_injection(
            dispatch,
            standby,
            existing_candidates,
            dispatched_director_npc_candidates,
        ),
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
    setting_anchor: SettingAnchorPacket,
    world_setting_store: WorldSettingStore,
    npc_profile_store: NPCProfileStore | None = None,
    creator_gateway: LLMGateway,
    creative_brief_rng: random.Random | None = None,
    trace_id: str | None = None,
) -> DirectorNPCDraft:
    owned_profile_store = npc_profile_store or NPCProfileStore(":memory:")
    creative_brief = draw_director_npc_creative_brief(
        setting_anchor.kind,
        rng=creative_brief_rng,
    )
    created = DirectorNPCCreator(creator_gateway).create(
        core_memory=core_memory,
        existing_npcs=owned_profile_store.list_all(),
        reserved_npcs=[
            {
                "name": npc.name,
                "role": npc.role,
                "core_motivation": npc.core_motivation,
                "hidden_secret": npc.hidden_secret,
            }
            for npc in director_npc_store.list_standby()
        ],
        setting_anchor=setting_anchor,
        creative_brief=creative_brief,
        protagonist_name=protagonist_name,
        turn_index=turn_index,
        trace_id=trace_id,
    )
    director_npc_store.save(created)
    world_setting_store.save_preparation(
        setting_anchor.setting_id,
        setting_anchor,
    )
    world_setting_store.mark_consumed(
        setting_anchor.setting_id,
        director_npc_id=created.draft_id,
    )
    return created


def restock_director_npc_standby(
    *,
    core_memory: CoreMemory,
    protagonist_name: str,
    turn_index: int,
    director_npc_store: DirectorNPCStore,
    world_setting_store: WorldSettingStore,
    npc_profile_store: NPCProfileStore | None = None,
    creator_gateway: LLMGateway,
    setting_maintainer_gateway: LLMGateway,
    setting_preparer_gateway: LLMGateway,
    reservation_owner: str,
    setting_rng: random.Random | None = None,
    creative_brief_rng: random.Random | None = None,
    trace_id: str | None = None,
) -> list[DirectorNPCDraft]:
    created_drafts: list[DirectorNPCDraft] = []
    owned_profile_store = npc_profile_store or NPCProfileStore(":memory:")
    _reconcile_setting_consumption(
        director_npc_store=director_npc_store,
        world_setting_store=world_setting_store,
    )
    missing_count = director_npc_standby_missing_count(director_npc_store)
    if missing_count <= 0:
        return []

    world_setting_store.release_reservations(reservation_owner)
    available_usable = [
        setting
        for setting in world_setting_store.list_available()
        if setting.maturity == "usable"
        and setting.prepared_anchor is not None
    ]
    if len(available_usable) < missing_count:
        maintain_setting_seeds(
            core_memory=core_memory,
            turn_index=turn_index,
            world_setting_store=world_setting_store,
            gateway=setting_maintainer_gateway,
            trace_id=trace_id,
        )

    random_source = setting_rng or random.SystemRandom()
    available_usable = [
        setting
        for setting in world_setting_store.list_available()
        if setting.maturity == "usable"
        and setting.prepared_anchor is not None
    ]
    random_source.shuffle(available_usable)
    reserved_usable = world_setting_store.reserve(
        [
            setting.setting_id
            for setting in available_usable[:missing_count]
        ],
        owner=reservation_owner,
    )
    remaining_count = missing_count - len(reserved_usable)
    available_seeds = world_setting_store.list_maintenance_seeds()
    random_source.shuffle(available_seeds)
    reserved_seeds = world_setting_store.reserve(
        [
            setting.setting_id
            for setting in available_seeds[: remaining_count * 3]
        ],
        owner=reservation_owner,
    )
    if not reserved_usable and not reserved_seeds:
        return []

    try:
        prepared_anchors: dict[str, SettingAnchorPacket] = {}
        if reserved_seeds:
            preparation_batch = SettingAnchorPreparer(
                setting_preparer_gateway
            ).prepare(
                core_memory=core_memory,
                settings=reserved_seeds,
                trace_id=trace_id,
            )
            reserved_seed_by_id = {
                setting.setting_id: setting for setting in reserved_seeds
            }
            for preparation in preparation_batch.preparations:
                setting = reserved_seed_by_id.get(preparation.setting_id)
                if setting is None:
                    continue
                if preparation.decision == "reject":
                    world_setting_store.mark_rejected(
                        setting.setting_id,
                        reason=preparation.reason,
                    )
                    continue
                anchor = preparation.anchor
                if anchor is None:
                    continue
                if anchor.label != setting.label or anchor.kind != setting.kind:
                    continue
                world_setting_store.save_preparation(
                    setting.setting_id,
                    anchor,
                )
                prepared_anchors[setting.setting_id] = anchor

        candidate_anchors = [
            setting.prepared_anchor
            for setting in reserved_usable
            if setting.prepared_anchor is not None
        ]
        candidate_anchors.extend(
            prepared_anchors[setting.setting_id]
            for setting in reserved_seeds
            if setting.setting_id in prepared_anchors
        )
        for selected_anchor in candidate_anchors[:missing_count]:
            created = create_director_npc_standby(
                core_memory=core_memory,
                protagonist_name=protagonist_name,
                turn_index=turn_index,
                director_npc_store=director_npc_store,
                setting_anchor=selected_anchor,
                world_setting_store=world_setting_store,
                npc_profile_store=owned_profile_store,
                creator_gateway=creator_gateway,
                creative_brief_rng=creative_brief_rng,
                trace_id=trace_id,
            )
            created_drafts.append(created)
    except BaseException:
        world_setting_store.release_reservations(reservation_owner)
        raise
    world_setting_store.release_reservations(reservation_owner)
    return created_drafts


def _reconcile_setting_consumption(
    *,
    director_npc_store: DirectorNPCStore,
    world_setting_store: WorldSettingStore,
) -> None:
    for draft in director_npc_store.list_all():
        if not draft.source_setting_id:
            continue
        setting = world_setting_store.get_by_id(draft.source_setting_id)
        if (
            setting is not None
            and setting.consumed_by_director_npc_id != draft.draft_id
        ):
            world_setting_store.mark_consumed(
                setting.setting_id,
                director_npc_id=draft.draft_id,
            )


def _director_npc_trigger_reason(
    *,
    turn_index: int,
    last_dispatch_turn: int,
) -> str | None:
    if last_dispatch_turn and turn_index <= last_dispatch_turn + DIRECTOR_NPC_REPEAT_BLOCK_TURNS:
        return None
    if turn_index == DIRECTOR_NPC_FIRST_FORCED_TURN and not last_dispatch_turn:
        return f"forced director NPC dispatch on turn {DIRECTOR_NPC_FIRST_FORCED_TURN}"
    return "director NPC dispatch check after cooldown"


def _existing_recall_candidates(npcs: list[NPCProfile]) -> list[ExistingNPCCandidate]:
    return [
        ExistingNPCCandidate(
            name=npc.name,
            role=npc.role,
            current_intent=npc.current_intent,
            attitude_to_player=npc.attitude_to_player,
            absent_turns=npc.absent_turns,
        )
        for npc in npcs
        if npc.absent_turns >= 1
    ]


def _dispatched_director_npc_candidates(
    director_npc_store: DirectorNPCStore,
    npc_profile_store: NPCProfileStore,
) -> list[DispatchedDirectorNPCCandidate]:
    candidates = []
    for draft in director_npc_store.list_all():
        if draft.status != "dispatched":
            continue
        public_profile = npc_profile_store.get_by_name(draft.name)
        candidates.append(
            DispatchedDirectorNPCCandidate(
                selected_id=draft.draft_id,
                selected_name=draft.name,
                public_entity_profile=(
                    public_profile.public_view()
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
    existing_candidates: list[ExistingNPCCandidate],
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


def _build_plot_injection(
    dispatch: DirectorNPCDispatchDecision,
    standby: list[DirectorNPCDraft],
    existing_candidates: list[ExistingNPCCandidate],
    dispatched_director_npc_candidates: list[DispatchedDirectorNPCCandidate],
) -> DirectorNPCPlotInjection | None:
    if dispatch.action == "none" or not dispatch.selected_id or not dispatch.selected_name:
        return None
    if dispatch.action == "dispatch_standby":
        for npc in standby:
            if npc.draft_id == dispatch.selected_id and npc.name == dispatch.selected_name:
                return DirectorNPCPlotInjection(
                    action=dispatch.action,
                    selected_id=dispatch.selected_id,
                    selected_name=dispatch.selected_name,
                    selected_npc=npc.private_view().model_dump(mode="json"),
                )
    if dispatch.action == "recall_existing":
        for npc in existing_candidates:
            if npc.name == dispatch.selected_id == dispatch.selected_name:
                return DirectorNPCPlotInjection(
                    action=dispatch.action,
                    selected_id=dispatch.selected_id,
                    selected_name=dispatch.selected_name,
                    selected_npc=npc.model_dump(mode="json"),
                )
        for candidate in dispatched_director_npc_candidates:
            if candidate.selected_id == dispatch.selected_id and candidate.selected_name == dispatch.selected_name:
                return DirectorNPCPlotInjection(
                    action=dispatch.action,
                    selected_id=dispatch.selected_id,
                    selected_name=dispatch.selected_name,
                    selected_npc=candidate.model_dump(mode="json"),
                )
    return None
