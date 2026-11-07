from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.text import normalize_compact_key
from app.domain.director.scheduled import collect_scheduled_npcs
from app.domain.npc_profile.workflows import auto_backfill_spontaneous_npc_profiles
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.director_promotion import (
    npc_profile_from_director_draft,
)
from app.domain.npc_profile.schemas import NPC_DISCLOSURE_ORDER
from app.domain.scene_writer.schemas import SceneWriterResult
from app.runtime.serialization import model_data
from app.runtime.services import RuntimeServices
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
from .confirmation_cache import ConfirmationCache
from .confirmation_state import ConfirmationEvent, ConfirmationState
from .fold import fold_node_update


@dataclass
class DirectorNPCConfirmation:
    npc_id: str
    draft: DirectorNPCDraft


def promote_staged_director_npc_profile(
    pending,
    *,
    turn_index: int,
    stores,
    used_names: set[str],
) -> list[DirectorNPCConfirmation]:
    """Promote used prepared actors, including those supplied by events."""
    stage = getattr(pending, "director_stage", None)
    materials = (
        stage.materials
        if stage is not None
        else []
    )
    confirmations: list[DirectorNPCConfirmation] = []
    with stores.npc_profile as npc_store:
        scheduled = collect_scheduled_npcs(materials, npc_store, stores.director_npc)
        for entry in scheduled:
            if normalize_compact_key(entry.name) not in used_names or not entry.draft_id:
                continue
            material = next(
                (item for item in materials
                 if item.kind == "npc" and item.resource_id == entry.draft_id),
                None,
            )
            confirmation = ensure_director_npc_profile(
                entry.draft_id,
                turn_index=turn_index,
                stores=stores,
                disclosure_level=(
                    material.payload.get("view_mode") if material else "face_to_face"
                ),
            )
            if confirmation is not None:
                confirmations.append(confirmation)
    return confirmations


def prepare_used_npc_profiles(
    pending,
    *,
    scene_result: SceneWriterResult,
    scene_text: str,
    turn_index: int,
    scene_id: str,
    trace_id: str,
    stores,
    gateway_factory,
    core_memory,
) -> tuple[list[str], list[DirectorNPCDraft]]:
    # Edited prose and legacy results have no usable receipt for this confirmation.
    usages = scene_result.usages_for_text(scene_text)
    used_names = {normalize_compact_key(item.name) for item in usages}
    if not used_names:
        return [], []
    confirmations = promote_staged_director_npc_profile(
        pending, turn_index=turn_index, stores=stores, used_names=used_names,
    )
    materials = pending.director_stage.materials if pending.director_stage else []
    has_spontaneous = any(
        item.kind == "npc" and item.payload.get("source") == "spontaneous"
        and normalize_compact_key(item.payload.get("name", "")) in used_names
        for item in materials
    )
    backfilled_ids = []
    if has_spontaneous:
        with stores.npc_profile as npc_store:
            backfilled_ids = auto_backfill_spontaneous_npc_profiles(
                pending=pending, npc_store=npc_store, gateway=gateway_factory(),
                core_memory=core_memory, turn_index=turn_index, scene_id=scene_id,
                trace_id=trace_id, used_names=used_names,
            )
    return (
        [item.npc_id for item in confirmations] + backfilled_ids,
        [item.draft for item in confirmations],
    )


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

    with stores.npc_profile as npc_store:
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
    with stores.director_npc as director_store, stores.npc_profile as npc_store:
        draft = director_store.get_by_id(draft_id)
        if draft is None or draft.status == "retired":
            return None
        existing = (
            npc_store.get_by_source_director_npc_id(draft.draft_id)
            or npc_store.get_by_name(draft.name)
        )
        if existing is not None:
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
        npc_id = npc_store.upsert(npc_profile)
        return DirectorNPCConfirmation(npc_id=npc_id, draft=draft)


def project_scene_text_with_used_npcs(scene_text: str, *, used_npcs) -> str:
    entries = [
        f"- {npc['name']}（{npc['role']}），正文称呼：{'、'.join(npc['surfaces'])}；参与方式：{npc['presence']}。"
        for npc in used_npcs
    ]
    if not entries:
        return scene_text
    return (
        "【确认用人物对应关系】\n" + "\n".join(entries)
        + "\n以上是本轮正文人物与档案姓名的对应关系。摘要使用档案姓名记录其公开行为；称呼对应不表示人物已向玩家自报姓名。\n【正文】\n"
        + scene_text
    )


def prepare_confirmation_state(
    pipeline_input,
    *,
    write_db: bool = True,
    scene_text: str | None = None,
    journaled: bool = False,
    stores=None,
) -> ConfirmationState:
    runtime = get_campaign_runtime()
    runtime_services = runtime.services
    if stores is not None:
        runtime_services = RuntimeServices(
            stores=stores,
            gateway_factory=runtime.services.gateway_factory,
            jobs=runtime.services.jobs,
            diagnostics=runtime.services.diagnostics,
        )
    stores = runtime_services.stores
    services = ConfirmationPreparationServices(
        current_state_store=stores.current_state,
        npc_profile_store=stores.npc_profile,
        core_memory_store=stores.core_memory,
        prepare_used_npc_profiles=lambda pending, **kwargs: prepare_used_npc_profiles(
            pending, stores=stores,
            gateway_factory=lambda: runtime_services.gateway(
                session_type="confirmation", turn_index=kwargs["turn_index"],
            ),
            **kwargs,
        ),
        apply_staged_disclosure_upgrades=lambda pending: (
            apply_staged_disclosure_upgrades(
                pending,
                stores=stores,
            )
        ),
        project_scene_text_with_used_npcs=project_scene_text_with_used_npcs,
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
    ConfirmationCache(state["cache"]).admit(
        node_update,
        write_db=state.get("write_db", True),
    )

    events = fold_node_update(
        state,
        node_update,
        exclude=frozenset(("node_errors",)),
    )
    state["node_errors"] = [*state.get("node_errors", []), *node_errors]
    return events


def prepare_confirmation_second_wave(state: ConfirmationState) -> ConfirmationState:
    stores = state["runtime_services"].stores
    with stores.scene_summary as summary_store:
        turn_summary = state["turn_summary"]
        state["scene_summaries"] = summary_store.list_active()
        state["recent_summaries"] = summary_store.get_recent(5)
        state["selection_summaries"] = [
            *state["recent_summaries"],
            turn_summary,
        ]
    with stores.continuity as continuity_store:
        state["active_continuity_items"] = (
            []
            if state["pipeline_input"].clear_continuity_before_save
            else continuity_store.list_active()
        )
    return state


def finalize_confirmation_state_in_transaction(
    state: ConfirmationState,
) -> tuple[ConfirmationState, Any]:
    runtime = get_campaign_runtime()
    stores = state["runtime_services"].stores
    committer = ConfirmationCommitter(
        ConfirmationCommitServices(
            current_state_store=stores.current_state,
            short_term_memory_store=stores.short_term,
            confirmed_history_store=stores.confirmed_history,
            npc_profile_store=stores.npc_profile,
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
    ).finalize_in_transaction(state)


def finalize_confirmation_state_after_commit(
    state: ConfirmationState,
    commit_result: Any,
) -> ConfirmationState:
    runtime = get_campaign_runtime()
    stores = state["runtime_services"].stores
    committer = ConfirmationCommitter(
        ConfirmationCommitServices(
            current_state_store=stores.current_state,
            short_term_memory_store=stores.short_term,
            confirmed_history_store=stores.confirmed_history,
            npc_profile_store=stores.npc_profile,
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
    ).finalize_after_commit(state, commit_result)
