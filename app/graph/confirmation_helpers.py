from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.director_promotion import (
    merge_director_draft_into_npc_profile,
    npc_profile_from_director_draft,
)
from app.domain.npc_profile.schemas import NPC_DISCLOSURE_ORDER
from app.runtime.serialization import model_data
from app.runtime.session import (
    get_campaign_runtime,
)
from app.graph.maintenance_graph import (
    run_scene_summary_maintenance,
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
from .fold import fold_node_update


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
    "npc_extraction",
    "director_decision",
    "director_executions",
    "director_event_reviews",
    "updated_npc_profile_ids",
    "new_npc_profiles",
    "recorded_setting_ids",
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
) -> list[DirectorNPCConfirmation]:
    """Promote every staged Director NPC draft (introduce materials) to a
    profile, in material order, so multi-introduce turns are fully promoted."""
    stage = getattr(pending, "director_stage", None)
    materials = (
        stage.materials
        if stage is not None
        else []
    )
    confirmations: list[DirectorNPCConfirmation] = []
    for material in materials:
        if material.kind != "npc" or material.payload.get("source") != "director_npc":
            continue
        confirmation = ensure_director_npc_profile(
            material.resource_id,
            turn_index=turn_index,
            stores=stores,
            disclosure_level=material.payload.get("view_mode"),
        )
        if confirmation is not None:
            confirmations.append(confirmation)
    return confirmations


def apply_staged_disclosure_upgrades(
    pending,
    stores,
) -> None:
    """Persist deferred disclosure-level upgrades staged by the generation
    Director. Runs at confirmation commit, alongside draft promotion."""
    stage = getattr(pending, "director_stage", None)
    materials = (
        stage.materials
        if stage is not None
        else []
    )

    with stores.npc_profile() as npc_store:
        for material in materials:
            if (
                material.kind != "npc"
                or not material.payload.get("disclosure_upgrade")
            ):
                continue
            npc_id = material.resource_id
            new_level = material.payload.get("new_disclosure_level")
            profile = npc_store.get_by_id(npc_id)
            if (
                profile is None
                or new_level is None
                or NPC_DISCLOSURE_ORDER.get(new_level) is None
            ):
                continue
            if NPC_DISCLOSURE_ORDER[new_level] <= NPC_DISCLOSURE_ORDER[
                profile.disclosure_level
            ]:
                continue
            npc_store.upsert(
                profile.model_copy(
                    update={"disclosure_level": new_level}
                )
            )


def ensure_director_npc_profile(
    draft_id: str,
    *,
    turn_index: int,
    stores,
    disclosure_level=None,
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
        npc_profile = npc_profile_from_director_draft(
            draft,
            disclosure_level=disclosure_level,
        )
        existing_npc = npc_store.get_by_source_director_npc_id(draft.draft_id) or npc_store.get_by_name(draft.name)
        if existing_npc is not None:
            npc_profile = merge_director_draft_into_npc_profile(
                existing_npc,
                draft,
                disclosure_level=disclosure_level,
            )
        npc_id = npc_store.upsert(npc_profile)
        return DirectorNPCConfirmation(npc_id=npc_id, draft=draft)


def merge_unique_ids(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        merged.append(value)
    return merged


def project_scene_text_with_staged_director_npc(
    scene_text: str,
    *,
    pending,
    confirmed_director_npc_drafts: list[DirectorNPCDraft] | None,
) -> str:
    stage = getattr(pending, "director_stage", None)
    materials = [
        item
        for item in (stage.materials if stage is not None else [])
        if item.kind == "npc"
        and item.payload.get("source") == "director_npc"
    ]
    if not materials:
        return scene_text

    drafts = confirmed_director_npc_drafts or []
    entries: list[str] = []
    for index, material in enumerate(materials):
        draft = drafts[index] if index < len(drafts) else None
        name = (
            draft.name
            if draft is not None
            else str(material.payload.get("name") or "")
        )
        if not name:
            continue
        role = draft.role if draft is not None else None
        role_note = f"（{role}）" if role else ""
        selected_id = (
            draft.draft_id
            if draft is not None
            else material.resource_id
        )
        entries.append(
            f"- {name}{role_note}，系统 ID：{selected_id}。"
        )
    if not entries:
        return scene_text
    mapping = "；".join(entries)

    return (
        "【确认用系统注记】\n"
        f"本回合 Director Agent 引入了以下导演 NPC：{mapping}\n"
        "如果正文中出现未透露姓名、临时称呼、只以外貌/位置描述出现的新增角色，"
        "且其出场与本回合导演 NPC 调度相符，确认流程应在内部状态、NPC档案和摘要中"
        "将其视为对应姓名。\n"
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
        npc_profile_store=stores.npc_profile,
        core_memory_store=stores.core_memory,
        promote_staged_director_npc_profile=lambda pending, *, turn_index: (
            promote_staged_director_npc_profile(
                pending,
                turn_index=turn_index,
                stores=stores,
            )
        ),
        apply_staged_disclosure_upgrades=lambda pending: (
            apply_staged_disclosure_upgrades(
                pending,
                stores=stores,
            )
        ),
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
    node_errors = node_update.get("node_errors", [])
    cache = state["cache"]
    cache_keys = set(_ALWAYS_CACHED_NODE_RESULTS)
    if state.get("write_db", True):
        cache_keys.update(_WRITE_CACHED_NODE_RESULTS)
    for key in cache_keys & node_update.keys():
        cache[key] = node_update[key]

    events = fold_node_update(
        state,
        node_update,
        exclude=frozenset(("node_errors",)),
    )
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
            run_scene_summary_maintenance=run_scene_summary_maintenance,
        ),
    )
    return ConfirmationFinalizer(
        runtime,
        committer,
        scheduler,
        ConfirmationEventBuilder(),
    ).finalize(state)
