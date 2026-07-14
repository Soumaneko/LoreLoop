from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.current_state import NPCState
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.runtime.serialization import model_data
from app.runtime.session import (
    get_campaign_runtime,
)
from app.graph.maintenance_graph import (
    run_director_npc_restock,
    run_memory_batch_maintenance,
    run_scene_summary_maintenance,
)
from app.domain.director_npc.workflows import DIRECTOR_NPC_STANDBY_TARGET
from .confirmation_lifecycle import (
    ConfirmationCommitServices,
    ConfirmationCommitter,
    ConfirmationEventBuilder,
    ConfirmationFinalizer,
    ConfirmationMaintenanceScheduler,
    ConfirmationMaintenanceServices,
    ConfirmationPreparationServices,
    ConfirmationPreparer,
)
from .confirmation_state import ConfirmationEvent, ConfirmationState


_ALWAYS_CACHED_NODE_RESULTS = {
    "scene_facts",
    "updated_state",
    "pending_memory_result",
    "action_preview",
}
_WRITE_CACHED_NODE_RESULTS = {
    "entity_extraction",
    "npc_extraction",
    "director_event_result",
    "updated_entity_ids",
    "updated_npc_profile_ids",
}

@dataclass
class DirectorNPCConfirmation:
    npc_id: str
    draft: DirectorNPCDraft


def promote_dispatched_director_npc_profile(
    pending,
    *,
    turn_index: int,
    stores,
) -> DirectorNPCConfirmation | None:
    result = getattr(pending, "director_npc_result", None)
    if (
        result is None
        or result.dispatch is None
        or result.dispatch.action != "dispatch_standby"
    ):
        return None
    return ensure_director_npc_profile(
        result.dispatch.selected_id,
        turn_index=turn_index,
        stores=stores,
    )


def ensure_director_npc_profile(
    draft_id: str,
    *,
    turn_index: int,
    stores,
) -> DirectorNPCConfirmation | None:
    with stores.director_npc() as director_store, stores.npc_profile() as npc_store:
        draft = director_store.get_by_id(draft_id)
        if draft is None or draft.status == "discarded":
            return None
        if draft.status == "standby":
            draft = director_store.mark_dispatched(draft_id, turn_index=turn_index)
            if draft is None:
                return None
        npc_profile = npc_profile_from_director_draft(draft)
        existing_npc = npc_store.get_by_source_director_npc_id(draft.draft_id) or npc_store.get_by_name(draft.name)
        if existing_npc is not None:
            npc_profile = merge_director_draft_into_npc_profile(existing_npc, draft)
        npc_id = npc_store.upsert(npc_profile)
        return DirectorNPCConfirmation(npc_id=npc_id, draft=draft)


def npc_profile_from_director_draft(draft: DirectorNPCDraft) -> NPCProfile:
    return NPCProfile(
        name=draft.name,
        public_summary=draft.role,
        role=draft.role,
        appearance=draft.appearance,
        personality_traits=list(draft.personality_traits),
        known_abilities=list(draft.known_abilities),
        faction=draft.faction,
        core_motivation=draft.core_motivation,
        hidden_secret=draft.hidden_secret,
        conflict_style=draft.conflict_style,
        social_style=draft.social_style,
        encounter_potentials=list(draft.encounter_potentials),
        source_director_npc_id=draft.draft_id,
        created_turn=draft.created_turn,
        dispatched_turn=draft.dispatched_turn,
        confidence=0.7,
        name_hit_count=1,
    )


def merge_director_draft_into_npc_profile(profile: NPCProfile, draft: DirectorNPCDraft) -> NPCProfile:
    merged = profile.model_copy(deep=True)
    merged.public_summary = merged.public_summary or draft.role
    if merged.source_director_npc_id:
        merged.role = merged.role or draft.role
        merged.appearance = merged.appearance or draft.appearance
        if not merged.personality_traits:
            merged.personality_traits = list(draft.personality_traits)
        if not merged.known_abilities:
            merged.known_abilities = list(draft.known_abilities)
        merged.faction = merged.faction or draft.faction
    else:
        merged.role = draft.role
        merged.appearance = draft.appearance
        merged.personality_traits = list(draft.personality_traits)
        merged.known_abilities = list(draft.known_abilities)
        merged.faction = draft.faction
    merged.core_motivation = draft.core_motivation
    merged.hidden_secret = draft.hidden_secret
    merged.conflict_style = draft.conflict_style
    merged.social_style = draft.social_style
    merged.encounter_potentials = list(draft.encounter_potentials)
    merged.source_director_npc_id = merged.source_director_npc_id or draft.draft_id
    merged.created_turn = merged.created_turn or draft.created_turn
    merged.dispatched_turn = draft.dispatched_turn
    merged.confidence = max(merged.confidence, 0.7)
    merged.name_hit_count = max(merged.name_hit_count, 1)
    return merged


def merge_unique_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def state_with_confirmed_director_npc(
    state: CurrentState,
    draft: DirectorNPCDraft | None,
) -> CurrentState:
    if draft is None:
        return state

    participants = list(state.scene.participants)
    if draft.name not in participants:
        participants.append(draft.name)

    npcs = []
    matched = False
    for npc in state.npcs:
        if npc.name == draft.name:
            matched = True
            npcs.append(
                npc.model_copy(
                    update={
                        "role": npc.role or draft.role,
                        "location": npc.location or state.scene.location,
                        "absent_turns": 0,
                    }
                )
            )
        else:
            npcs.append(npc)

    if not matched:
        npcs.append(
            NPCState(
                name=draft.name,
                role=draft.role,
                location=state.scene.location,
                absent_turns=0,
            )
        )

    return state.model_copy(
        update={
            "scene": state.scene.model_copy(update={"participants": participants}),
            "npcs": npcs,
        }
    )


def project_scene_text_for_confirmation(
    scene_text: str,
    *,
    pending,
    confirmed_director_npc_draft: DirectorNPCDraft | None,
) -> str:
    result = getattr(pending, "director_npc_result", None)
    dispatch = getattr(result, "dispatch", None)
    if dispatch is None or dispatch.action != "dispatch_standby" or not dispatch.selected_name:
        return scene_text

    name = confirmed_director_npc_draft.name if confirmed_director_npc_draft is not None else dispatch.selected_name
    role = confirmed_director_npc_draft.role if confirmed_director_npc_draft is not None else None
    role_note = f"（{role}）" if role else ""
    selected_id = (
        confirmed_director_npc_draft.draft_id
        if confirmed_director_npc_draft is not None
        else dispatch.selected_id
    )

    return (
        "【确认用系统注记】\n"
        f"本回合 DirectorNPCDispatcher 引入了导演 NPC：{name}{role_note}。"
        f"系统 ID：{selected_id}。\n"
        "如果正文中出现未透露姓名、临时称呼、只以外貌/位置描述出现的新增角色，"
        f"且其出场与本回合导演 NPC 调度相符，确认流程应在内部状态、NPC档案和摘要中将其视为{name}。\n"
        "该注记只用于确认流程；不代表玩家知道TA的姓名；不得写成角色已自报姓名；"
        "不得泄露 hidden_secret、core_motivation 或其他幕后信息。\n"
        "【正文】\n"
        f"{scene_text}"
    )


def prepare_confirmation_state(
    pipeline_input,
    *,
    write_db: bool = True,
    scene_text: str | None = None,
) -> ConfirmationState:
    runtime = get_campaign_runtime()
    runtime_services = runtime.services
    stores = runtime_services.stores
    services = ConfirmationPreparationServices(
        current_state_store=stores.current_state,
        director_npc_store=stores.director_npc,
        entity_store=stores.entity,
        npc_profile_store=stores.npc_profile,
        core_memory_store=stores.core_memory,
        promote_dispatched_director_npc_profile=lambda pending, *, turn_index: (
            promote_dispatched_director_npc_profile(
                pending,
                turn_index=turn_index,
                stores=stores,
            )
        ),
        state_with_confirmed_director_npc=state_with_confirmed_director_npc,
        project_scene_text_for_confirmation=project_scene_text_for_confirmation,
        runtime_services=runtime_services,
    )
    return ConfirmationPreparer(services).prepare(
        pipeline_input,
        write_db=write_db,
        scene_text=scene_text,
    )


def merge_confirmation_node_update(
    state: ConfirmationState,
    node_update: ConfirmationState,
) -> list[ConfirmationEvent]:
    events = node_update.get("events", [])
    node_errors = node_update.get("node_errors", [])
    cache = state["cache"]
    cache_keys = set(_ALWAYS_CACHED_NODE_RESULTS)
    if state.get("write_db", True):
        cache_keys.update(_WRITE_CACHED_NODE_RESULTS)
    for key in cache_keys & node_update.keys():
        cache[key] = node_update[key]

    state.update({
        key: value
        for key, value in node_update.items()
        if key not in {"events", "node_errors"}
    })
    state["events"] = [*state.get("events", []), *events]
    state["node_errors"] = [*state.get("node_errors", []), *node_errors]
    return events


def prepare_confirmation_second_wave(state: ConfirmationState) -> ConfirmationState:
    from app.domain.recall_memory.maintenance import fill_pending_memory_scene_metadata

    cache = state["cache"]
    updated_state = state["updated_state"]
    pending_memory_result = state["pending_memory_result"]

    if not cache.get("pending_memory_metadata_filled"):
        pending_memory_items = fill_pending_memory_scene_metadata(
            pending_memory_result.items,
            updated_state,
        )
        pending_memory_result = pending_memory_result.model_copy(
            update={"items": pending_memory_items}
        )
        cache["pending_memory_result"] = pending_memory_result
        cache["pending_memory_metadata_filled"] = True
        state["pending_memory_result"] = pending_memory_result

    with state["runtime_services"].stores.scene_summary() as summary_store:
        state["scene_summaries"] = summary_store.list_active()
        state["recent_summaries"] = summary_store.get_recent(5)

    state["events"] = [
        *state.get("events", []),
        {
            "step": "memory_draft_metadata_filled",
            "data": {
                "location": updated_state.scene.location,
                "time_label": updated_state.world_clock.label,
                "pending_items": model_data(pending_memory_result.items),
            },
        },
    ]
    return state


def finalize_confirmation_state(state: ConfirmationState) -> ConfirmationState:
    runtime = get_campaign_runtime()
    stores = state["runtime_services"].stores
    committer = ConfirmationCommitter(
        ConfirmationCommitServices(
            current_state_store=stores.current_state,
            short_term_memory_store=stores.short_term,
            pending_memory_store=stores.pending_memory,
            memory_store=stores.memory,
        )
    )
    scheduler = ConfirmationMaintenanceScheduler(
        runtime,
        ConfirmationMaintenanceServices(
            director_npc_store=stores.director_npc,
            run_director_npc_restock=run_director_npc_restock,
            run_scene_summary_maintenance=run_scene_summary_maintenance,
            run_memory_batch_maintenance=run_memory_batch_maintenance,
            submit_memory_batch_once=runtime.jobs.submit_memory_batch_once,
        ),
    )
    return ConfirmationFinalizer(
        runtime,
        committer,
        scheduler,
        ConfirmationEventBuilder(),
    ).finalize(state)
