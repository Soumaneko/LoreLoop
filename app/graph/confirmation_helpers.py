from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.current_state import ParticipantState
from app.domain.current_state.schemas import CurrentState
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.runtime.serialization import model_data
from app.runtime.session import (
    get_campaign_runtime,
)
from app.graph.maintenance_graph import (
    run_scene_summary_maintenance,
    run_setting_seed_maintenance,
)
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
    "state_change_set",
    "state_update_warnings",
    "turn_summary",
    "action_preview",
    "continuity_projection",
}
_WRITE_CACHED_NODE_RESULTS = {
    "entity_extraction",
    "npc_extraction",
    "director_decision",
    "director_executions",
    "updated_entity_ids",
    "updated_npc_profile_ids",
    "new_npc_profiles",
}
_ROLLBACK_SAFE_CACHE_KEYS = {
    "key",
    "scene_facts",
    "updated_state",
    "state_change_set",
    "state_update_warnings",
    "turn_summary",
    "action_preview",
    "continuity_projection",
}

@dataclass
class DirectorNPCConfirmation:
    npc_id: str
    draft: DirectorNPCDraft


def promote_staged_director_npc_profile(
    pending,
    *,
    turn_index: int,
    stores,
) -> DirectorNPCConfirmation | None:
    stage = getattr(pending, "director_stage", None)
    material = next(
        (
            item
            for item in (stage.materials if stage is not None else [])
            if item.kind == "npc"
            and item.payload.get("source") == "director_npc"
        ),
        None,
    )
    if material is None:
        return None
    return ensure_director_npc_profile(
        material.resource_id,
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
        if draft is None or draft.status == "retired":
            return None
        if draft.status == "prepared":
            draft = director_store.mark_introduced(
                draft_id,
                turn_index=turn_index,
            )
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
        introduced_turn=draft.introduced_turn,
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
    merged.introduced_turn = draft.introduced_turn
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


def state_with_staged_director_npc(
    state: CurrentState,
    draft: DirectorNPCDraft | None,
) -> CurrentState:
    if draft is None:
        return state

    participants = list(state.scene.participants)
    if draft.name.casefold() not in {
        participant.name.casefold() for participant in participants
    }:
        participants.append(
            ParticipantState(
                name=draft.name,
            )
        )

    return state.model_copy(
        update={
            "scene": state.scene.model_copy(update={"participants": participants}),
        }
    )


def project_scene_text_with_staged_director_npc(
    scene_text: str,
    *,
    pending,
    confirmed_director_npc_draft: DirectorNPCDraft | None,
) -> str:
    stage = getattr(pending, "director_stage", None)
    material = next(
        (
            item
            for item in (stage.materials if stage is not None else [])
            if item.kind == "npc"
            and item.payload.get("source") == "director_npc"
        ),
        None,
    )
    if material is None:
        return scene_text

    name = (
        confirmed_director_npc_draft.name
        if confirmed_director_npc_draft is not None
        else str(material.payload.get("name") or "")
    )
    if not name:
        return scene_text
    role = confirmed_director_npc_draft.role if confirmed_director_npc_draft is not None else None
    role_note = f"（{role}）" if role else ""
    selected_id = (
        confirmed_director_npc_draft.draft_id
        if confirmed_director_npc_draft is not None
        else material.resource_id
    )

    return (
        "【确认用系统注记】\n"
        f"本回合 Director Agent 引入了导演 NPC：{name}{role_note}。"
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
    journaled: bool = False,
) -> ConfirmationState:
    runtime = get_campaign_runtime()
    runtime_services = runtime.services
    stores = runtime_services.stores
    services = ConfirmationPreparationServices(
        current_state_store=stores.current_state,
        entity_store=stores.entity,
        npc_profile_store=stores.npc_profile,
        core_memory_store=stores.core_memory,
        promote_staged_director_npc_profile=lambda pending, *, turn_index: (
            promote_staged_director_npc_profile(
                pending,
                turn_index=turn_index,
                stores=stores,
            )
        ),
        state_with_staged_director_npc=state_with_staged_director_npc,
        project_scene_text_with_staged_director_npc=(
            project_scene_text_with_staged_director_npc
        ),
        runtime_services=runtime_services,
    )
    return ConfirmationPreparer(services).prepare(
        pipeline_input,
        write_db=write_db,
        scene_text=scene_text,
        journaled=journaled,
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


def reset_confirmation_cache_after_rollback(pending) -> None:
    """Keep pure LLM results but discard every cache entry tied to DB writes."""
    cache = pending.confirmation_cache
    retained = {
        key: cache[key]
        for key in _ROLLBACK_SAFE_CACHE_KEYS
        if key in cache
    }
    cache.clear()
    cache.update(retained)


def prepare_confirmation_second_wave(state: ConfirmationState) -> ConfirmationState:
    with state["runtime_services"].stores.scene_summary() as summary_store:
        turn_summary = state["turn_summary"]
        state["scene_summaries"] = summary_store.list_active()
        state["recent_summaries"] = summary_store.get_recent(5)
        state["selection_summaries"] = [
            *state["recent_summaries"],
            turn_summary,
        ]
    return state


def finalize_confirmation_state(state: ConfirmationState) -> ConfirmationState:
    runtime = get_campaign_runtime()
    stores = state["runtime_services"].stores
    committer = ConfirmationCommitter(
        ConfirmationCommitServices(
            current_state_store=stores.current_state,
            short_term_memory_store=stores.short_term,
            confirmed_history_store=stores.confirmed_history,
            continuity_store=stores.continuity,
            scene_summary_store=stores.scene_summary,
        )
    )
    scheduler = ConfirmationMaintenanceScheduler(
        runtime,
        ConfirmationMaintenanceServices(
            run_setting_seed_maintenance=run_setting_seed_maintenance,
            run_scene_summary_maintenance=run_scene_summary_maintenance,
        ),
    )
    return ConfirmationFinalizer(
        runtime,
        committer,
        scheduler,
        ConfirmationEventBuilder(),
    ).finalize(state)
