import concurrent.futures
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.core.llm.config import create_gateway
from app.domain.current_state import StateUpdater
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.entity.extractor import EntityExtractor
from app.domain.entity.schemas import EntityProfile
from app.domain.turn_resolution import NARRATIVE_TYPE_LABELS
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.workflows.extract_memory import fill_pending_memory_scene_metadata
from app.workflows.generate_action_preview import generate_action_preview_workflow
from app.workflows.update_entity_profiles import update_entity_profiles_from_extraction
from app.workflows.update_director_event import update_director_event_workflow
from app.workflows.director_npc import DIRECTOR_NPC_STANDBY_TARGET

from .maintenance import (
    run_director_npc_restock,
    run_memory_batch_maintenance,
    run_scene_summary_maintenance,
)
from .serialization import model_data
from .session import (
    BACKGROUND_EXECUTOR,
    PENDING_OPENINGS,
    PENDING_TURNS,
    confirmation_lock,
    release_confirmation_lock,
    submit_memory_batch_once,
)
from .stores import (
    core_memory_store,
    current_state_store,
    director_event_store,
    director_npc_store,
    entity_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
    short_term_memory_store,
)


@dataclass
class _ConfirmationPipelineInput:
    turn_id: str
    pending: Any
    state_before: Any
    scene_result: Any
    user_input: str
    turn_index: int
    default_narrative_type: str
    stale_message: str
    pending_registry: dict[str, Any]
    invalid_narrative_fallback: str | None = None
    update_director_event: bool = True
    handle_director_npc: bool = True
    clear_short_term_before_save: bool = False
    dry_run_memory_reason: str = (
        "DB writes are disabled; pending memory candidates were extracted but not saved."
    )
    confirm_started_extra: dict[str, Any] = field(default_factory=dict)
    finish_extra: dict[str, Any] = field(default_factory=dict)


def confirm_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    if turn_id in PENDING_OPENINGS:
        yield from confirm_opening_stream(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
            narrative_type=narrative_type,
        )
        return
    yield from confirm_turn_stream(
        turn_id,
        write_db=write_db,
        scene_text=scene_text,
        narrative_type=narrative_type,
    )


def confirm_turn_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    lock = confirmation_lock(turn_id)
    with lock:
        yield from _confirm_turn_stream_locked(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
            narrative_type=narrative_type,
        )


def _promote_dispatched_director_npc_shell(
    pending,
    *,
    turn_index: int,
) -> str | None:
    result = pending.director_npc_result
    if (
        result is None
        or result.dispatch is None
        or result.dispatch.action != "dispatch_standby"
    ):
        return None

    dispatch = result.dispatch
    with director_npc_store() as npc_store, entity_store() as ent_store:
        draft = npc_store.mark_dispatched(dispatch.selected_id, turn_index=turn_index)
        if draft is None:
            return None
        existing = ent_store.get_by_name(draft.name)
        if existing is not None and existing.entity_type == "npc":
            return existing.entity_id
        return ent_store.upsert(
            EntityProfile(
                entity_type="npc",
                name=draft.name,
                role=draft.role,
                confidence=0.2,
                name_hit_count=1,
            )
        )


def _confirm_turn_stream_locked(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    pending = PENDING_TURNS.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before confirming.")

    yield from _confirm_pipeline_locked(
        _ConfirmationPipelineInput(
            turn_id=turn_id,
            pending=pending,
            state_before=pending.state_before,
            scene_result=pending.scene_result,
            user_input=pending.user_input,
            turn_index=pending.turn_index,
            default_narrative_type=pending.context.turn_resolution.selected_type,
            stale_message=(
                "Pending turn is stale because CurrentState changed after generation. "
                "Generate the turn again before confirming."
            ),
            pending_registry=PENDING_TURNS,
            update_director_event=True,
            handle_director_npc=True,
        ),
        write_db=write_db,
        scene_text=scene_text,
        narrative_type=narrative_type,
    )


def _confirm_pipeline_locked(
    pipeline_input: _ConfirmationPipelineInput,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    pending = pipeline_input.pending
    turn_id = pipeline_input.turn_id
    trace_id = str(uuid.uuid4())
    effective_scene_text = scene_text if scene_text is not None else pipeline_input.scene_result.scene_text
    effective_narrative_type = narrative_type or pipeline_input.default_narrative_type
    if effective_narrative_type not in NARRATIVE_TYPE_LABELS:
        if pipeline_input.invalid_narrative_fallback is None:
            raise ValueError(f"Invalid narrative_type: {effective_narrative_type}")
        effective_narrative_type = pipeline_input.invalid_narrative_fallback
    cache_key = (effective_scene_text, effective_narrative_type)
    cache = pending.confirmation_cache
    if cache.get("key") != cache_key:
        cache.clear()
        cache["key"] = cache_key
    if write_db and not cache.get("state_saved"):
        with current_state_store() as state_store:
            persisted_state = state_store.load_or_default()
        if persisted_state != pipeline_input.state_before:
            raise ValueError(pipeline_input.stale_message)

    yield {
        "step": "confirm_started",
        "data": {
            "trace_id": trace_id,
            "narrative_type": effective_narrative_type,
            **pipeline_input.confirm_started_extra,
        },
    }

    director_npc_entity_id = cache.get("director_npc_entity_id")
    director_npc_restock_scheduled = bool(cache.get("director_npc_restock_scheduled"))
    if (
        pipeline_input.handle_director_npc
        and write_db
        and director_npc_entity_id is None
        and "director_npc_entity_id" not in cache
    ):
        director_npc_entity_id = _promote_dispatched_director_npc_shell(
            pending,
            turn_index=pipeline_input.turn_index,
        )
        cache["director_npc_entity_id"] = director_npc_entity_id

    existing_entity_profiles = cache.get("existing_entity_profiles", [])
    if write_db and "existing_entity_profiles" not in cache:
        with entity_store() as ent_store:
            existing_entity_profiles = ent_store.list_all()
        cache["existing_entity_profiles"] = existing_entity_profiles

    core_memory = core_memory_store().load_required()

    def _do_state_update():
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        updater = StateUpdater(gateway)
        npc_aliases = {
            profile.name: list(profile.aliases)
            for profile in existing_entity_profiles
            if getattr(profile, "entity_type", None) == "npc"
            and profile.name
            and profile.aliases
        }
        result = updater.update(
            pipeline_input.state_before,
            effective_scene_text,
            core_memory=core_memory,
            npc_aliases=npc_aliases,
            trace_id=trace_id,
        )
        cache["updated_state"] = result
        return result

    def _do_memory_draft_extract():
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        extractor = MemoryDraftExtractor(gateway)
        result = extractor.extract(
            effective_scene_text,
            pipeline_input.state_before,
            core_memory=core_memory,
            turn_index=pipeline_input.turn_index,
            scene_id=pipeline_input.state_before.scene.scene_id,
            trace_id=trace_id,
        )
        cache["pending_memory_result"] = result
        return result

    def _do_entity_extract():
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        extractor = EntityExtractor(gateway)
        result = extractor.extract(
            core_memory=core_memory,
            scene_text=effective_scene_text,
            existing_profiles=existing_entity_profiles,
            trace_id=trace_id,
        )
        cache["entity_extraction"] = result
        return result

    updated_state = cache.get("updated_state")
    pending_memory_result = cache.get("pending_memory_result")
    entity_extraction = cache.get("entity_extraction")
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        state_fu = pool.submit(_do_state_update) if updated_state is None else None
        extract_fu = (
            pool.submit(_do_memory_draft_extract)
            if pending_memory_result is None
            else None
        )
        entity_extract_fu = (
            pool.submit(_do_entity_extract)
            if write_db and entity_extraction is None
            else None
        )

        first_wave = [
            future
            for future in (state_fu, extract_fu, entity_extract_fu)
            if future is not None
        ]

        for future in concurrent.futures.as_completed(first_wave):
            if future == state_fu:
                updated_state = future.result()
                yield {"step": "state_update_done", "data": {"state_after": model_data(updated_state)}}
            elif future == extract_fu:
                pending_memory_result = future.result()
                yield {
                    "step": "memory_draft_extract_done",
                    "data": {
                        "pending_items_count": len(pending_memory_result.items),
                        "pending_items": model_data(pending_memory_result.items),
                    },
                }
            else:
                entity_extraction = future.result()
                yield {
                    "step": "entity_extract_done",
                    "data": {
                        "entity_extraction": model_data(entity_extraction),
                    },
                }

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
    yield {
        "step": "memory_draft_metadata_filled",
        "data": {
            "location": updated_state.scene.location,
            "time_label": updated_state.world_clock.label,
            "pending_items": model_data(pending_memory_result.items),
        },
    }

    with scene_summary_store() as summary_store:
        scene_summaries = summary_store.list_active()
        recent_summaries = summary_store.get_recent(5)

    def _do_action_preview():
        gateway = create_gateway()
        gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
        return generate_action_preview_workflow(
            scene_text=effective_scene_text,
            current_state=updated_state,
            core_memory=core_memory_store().load_required(),
            recent_summaries=recent_summaries,
            next_action_directions=pipeline_input.scene_result.next_action_directions,
            gateway=gateway,
            trace_id=trace_id,
        )

    action_preview = cache.get("action_preview")
    if action_preview is None:
        action_preview = _do_action_preview()
        cache["action_preview"] = action_preview
    yield {
        "step": "action_preview_done",
        "data": {
            "action_preview": model_data(action_preview),
        },
    }

    updated_entity_ids: list[str] = cache.get("updated_entity_ids", [])
    director_event_result = cache.get("director_event_result")
    if write_db:
        if (
            pipeline_input.update_director_event
            and pipeline_input.turn_index >= 1
            and "director_event_result" not in cache
        ):
            with director_event_store() as director_store:
                reviewer_gateway = create_gateway()
                reviewer_gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
                selector_gateway = create_gateway()
                selector_gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
                director_event_result = update_director_event_workflow(
                    user_input=pipeline_input.user_input,
                    scene_text=effective_scene_text,
                    current_state=updated_state,
                    core_memory=core_memory_store().load_required(),
                    scene_summaries=scene_summaries,
                    turn_index=pipeline_input.turn_index,
                    store=director_store,
                    reviewer=DirectorEventReviewer(reviewer_gateway),
                    selector=DirectorEventSelector(selector_gateway),
                    trace_id=trace_id,
                )
            cache["director_event_result"] = director_event_result
        if entity_extraction is not None and "updated_entity_ids" not in cache:
            with entity_store() as ent_store:
                gateway = create_gateway()
                gateway.set_trace_context(turn_index=pipeline_input.turn_index, session_type="confirmation")
                entity_result = update_entity_profiles_from_extraction(
                    scene_text=effective_scene_text,
                    current_state=updated_state,
                    core_memory=core_memory,
                    gateway=gateway,
                    entity_store=ent_store,
                    existing_profiles=existing_entity_profiles,
                    extraction=entity_extraction,
                    recent_summaries=recent_summaries,
                    trace_id=trace_id,
                )
                updated_entity_ids = entity_result.saved_ids
            cache["updated_entity_ids"] = updated_entity_ids
        if (
            pipeline_input.handle_director_npc
            and director_npc_entity_id
            and not cache.get("director_npc_restock_scheduled")
        ):
            with director_npc_store() as npc_store:
                should_restock_director_npc = (
                    len(npc_store.list_standby()) < DIRECTOR_NPC_STANDBY_TARGET
                )
            if should_restock_director_npc:
                BACKGROUND_EXECUTOR.submit(
                    run_director_npc_restock,
                    trace_id,
                    pipeline_input.turn_index,
                    core_memory=core_memory,
                    protagonist_name=updated_state.player.name,
                )
                director_npc_restock_scheduled = True
                cache["director_npc_restock_scheduled"] = True
    entity_update_data = {
        "updated_entity_ids": updated_entity_ids,
    }
    if pipeline_input.handle_director_npc or pipeline_input.update_director_event:
        entity_update_data.update(
            {
                "director_npc_entity_id": director_npc_entity_id,
                "director_npc_restock_scheduled": director_npc_restock_scheduled,
                "director_npc_result": model_data(
                    getattr(pending, "director_npc_result", None)
                ),
                "director_event_result": model_data(director_event_result),
            }
        )
    yield {
        "step": "entity_profiles_updated",
        "data": entity_update_data,
    }

    saved_pending_ids: list[str] = []
    store_count_before = 0
    store_count_after = 0
    store_total = 0
    pending_count_after = 0
    batch_queued = bool(cache.get("memory_batch_scheduled"))

    if write_db:
        if not cache.get("state_saved"):
            with current_state_store() as state_store:
                state_store.save(updated_state)
            cache["state_saved"] = True

        with short_term_memory_store() as short_store:
            if not cache.get("short_term_saved"):
                if pipeline_input.clear_short_term_before_save:
                    short_store.clear()
                short_store.add_turn(
                    scene_id=updated_state.scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=effective_scene_text,
                    narrative_type=effective_narrative_type,
                )
                cache["short_term_saved"] = True
            short_term_after = short_store.get_recent()

        with pending_memory_store() as pending_store:
            if not cache.get("pending_memories_saved"):
                saved_pending_ids = pending_store.save_many(pending_memory_result.items)
                cache["saved_pending_ids"] = saved_pending_ids
                cache["pending_memories_saved"] = True
            else:
                saved_pending_ids = cache.get("saved_pending_ids", [])
            pending_count_after = pending_store.count()
            should_batch = pending_store.should_trigger_batch(turn_threshold=5)

        with memory_store() as mem_store:
            store_count_before = mem_store.count()
            store_count_after = store_count_before
            store_total = len(mem_store.list_all())

        summary_future = None
        if not cache.get("scene_summary_scheduled"):
            summary_future = BACKGROUND_EXECUTOR.submit(
                run_scene_summary_maintenance,
                effective_scene_text,
                updated_state,
                updated_state.scene.scene_id,
                pipeline_input.turn_index,
                trace_id,
                core_memory=core_memory,
            )
            cache["scene_summary_scheduled"] = True
        if should_batch and not cache.get("memory_batch_scheduled"):
            batch_queued = submit_memory_batch_once(
                run_memory_batch_maintenance,
                updated_state,
                trace_id,
                pipeline_input.turn_index,
                core_memory=core_memory,
                wait_for_summary=summary_future,
            )
            cache["memory_batch_scheduled"] = batch_queued
    else:
        with short_term_memory_store() as short_store:
            short_term_after = short_store.get_recent()

        with memory_store() as mem_store:
            store_total = mem_store.count()

    if write_db:
        pipeline_input.pending_registry.pop(turn_id, None)
        release_confirmation_lock(turn_id)

    memory_data: dict = {
        "pending_items": model_data(pending_memory_result.items),
        "saved_pending_ids": saved_pending_ids,
        "pending_count_after": pending_count_after,
        "batch_queued": batch_queued,
        "final_memories": [],
        "saved_ids": [],
        "store_count_before": store_count_before,
        "store_count_after": store_count_after,
        "store_total": store_total,
    }
    if not write_db:
        memory_data["dry_run"] = True
        memory_data["reason"] = pipeline_input.dry_run_memory_reason

    scene_result_data = model_data(pipeline_input.scene_result)
    if scene_text is not None:
        scene_result_data["scene_text"] = scene_text

    finish_data = {
        "turn_id": turn_id,
        "trace_id": trace_id,
        "write_db": write_db,
        **pipeline_input.finish_extra,
        "narrative_type": effective_narrative_type,
        "state_before": model_data(pipeline_input.state_before),
        "state_after": model_data(updated_state),
        "scene_result": scene_result_data,
        "action_preview": model_data(action_preview),
        "short_term_memory_after": model_data(short_term_after),
        "memory_extraction": memory_data,
        "updated_entity_ids": updated_entity_ids,
        "status": "committed" if write_db else "dry_run",
    }
    if pipeline_input.handle_director_npc or pipeline_input.update_director_event:
        finish_data.update(
            {
                "director_npc_entity_id": director_npc_entity_id,
                "director_npc_restock_scheduled": director_npc_restock_scheduled,
                "director_npc_result": model_data(
                    getattr(pending, "director_npc_result", None)
                ),
                "director_event_result": model_data(director_event_result),
            }
        )

    yield {
        "step": "finish",
        "data": finish_data,
    }


def confirm_opening_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    lock = confirmation_lock(turn_id)
    with lock:
        yield from _confirm_opening_stream_locked(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
            narrative_type=narrative_type,
        )


def _confirm_opening_stream_locked(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    pending = PENDING_OPENINGS.get(turn_id)
    if pending is None:
        raise ValueError("Pending opening not found. Initialize again before confirming.")

    yield from _confirm_pipeline_locked(
        _ConfirmationPipelineInput(
            turn_id=turn_id,
            pending=pending,
            state_before=pending.current_state,
            scene_result=pending.scene_result,
            user_input="",
            turn_index=0,
            default_narrative_type="transition",
            invalid_narrative_fallback="transition",
            stale_message=(
                "Pending opening is stale because initialization state changed. "
                "Initialize again before confirming."
            ),
            pending_registry=PENDING_OPENINGS,
            update_director_event=False,
            handle_director_npc=False,
            clear_short_term_before_save=True,
            dry_run_memory_reason="DB writes are disabled; opening scene was not saved.",
            confirm_started_extra={"opening": True},
            finish_extra={"opening": True},
        ),
        write_db=write_db,
        scene_text=scene_text,
        narrative_type=narrative_type,
    )
