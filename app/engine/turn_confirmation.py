import concurrent.futures
import uuid

from app.core.llm.config import create_gateway
from app.domain.current_state import StateUpdater
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.entity.extractor import EntityExtractor
from app.domain.narrative_pacer import NARRATIVE_TYPE_LABELS
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.workflows.extract_memory import fill_pending_memory_scene_metadata
from app.workflows.generate_action_preview import generate_action_preview_workflow
from app.workflows.update_entity_profiles import update_entity_profiles_from_extraction
from app.workflows.update_director_event import update_director_event_workflow

from .maintenance import run_memory_batch_maintenance, run_scene_summary_maintenance
from .serialization import model_data
from .session import BACKGROUND_EXECUTOR, PENDING_OPENINGS, PENDING_TURNS
from .stores import (
    core_memory_store,
    current_state_store,
    director_event_store,
    entity_store,
    memory_store,
    pending_memory_store,
    scene_summary_store,
    short_term_memory_store,
)


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
    pending = PENDING_TURNS.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before confirming.")

    trace_id = str(uuid.uuid4())
    effective_scene_text = scene_text if scene_text is not None else pending.scene_result.scene_text
    effective_narrative_type = narrative_type or pending.context.narrative_pacing.selected_type
    if effective_narrative_type not in NARRATIVE_TYPE_LABELS:
        raise ValueError(f"Invalid narrative_type: {effective_narrative_type}")

    yield {
        "step": "confirm_started",
        "data": {
            "trace_id": trace_id,
            "narrative_type": effective_narrative_type,
        },
    }

    gateway1 = create_gateway()
    gateway2 = create_gateway()
    gateway3 = create_gateway()
    existing_entity_profiles = []
    if write_db:
        with entity_store() as ent_store:
            existing_entity_profiles = ent_store.list_all()

    def _do_state_update():
        updater = StateUpdater(gateway1)
        return updater.update(
            pending.state_before,
            effective_scene_text,
            trace_id=trace_id,
        )

    def _do_memory_draft_extract():
        extractor = MemoryDraftExtractor(gateway2)
        return extractor.extract(
            effective_scene_text,
            pending.state_before,
            turn_index=pending.turn_index,
            scene_id=pending.state_before.scene.scene_id,
            trace_id=trace_id,
        )

    def _do_entity_extract():
        extractor = EntityExtractor(gateway3)
        return extractor.extract(
            scene_text=effective_scene_text,
            existing_profiles=existing_entity_profiles,
            trace_id=trace_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        state_fu = pool.submit(_do_state_update)
        extract_fu = pool.submit(_do_memory_draft_extract)
        entity_extract_fu = pool.submit(_do_entity_extract) if write_db else None

        updated_state = None
        pending_memory_result = None
        entity_extraction = None

        first_wave = [state_fu, extract_fu]
        if entity_extract_fu is not None:
            first_wave.append(entity_extract_fu)

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

    pending_memory_items = fill_pending_memory_scene_metadata(
        pending_memory_result.items,
        updated_state,
    )
    pending_memory_result = pending_memory_result.model_copy(
        update={"items": pending_memory_items}
    )
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
        return generate_action_preview_workflow(
            scene_text=effective_scene_text,
            current_state=updated_state,
            core_memory=core_memory_store().load_required(),
            recent_summaries=recent_summaries,
            gateway=create_gateway(),
            trace_id=trace_id,
        )

    updated_entity_ids: list[str] = []
    director_event_result = None
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        action_preview_fu = pool.submit(_do_action_preview)
        if write_db and pending.turn_index >= 1:
            with director_event_store() as director_store:
                director_event_result = update_director_event_workflow(
                    user_input=pending.user_input,
                    scene_text=effective_scene_text,
                    current_state=updated_state,
                    core_memory=core_memory_store().load_required(),
                    scene_summaries=scene_summaries,
                    turn_index=pending.turn_index,
                    store=director_store,
                    reviewer=DirectorEventReviewer(create_gateway()),
                    selector=DirectorEventSelector(create_gateway()),
                    trace_id=trace_id,
                )
        if write_db and entity_extraction is not None:
            with entity_store() as ent_store:
                entity_result = update_entity_profiles_from_extraction(
                    scene_text=effective_scene_text,
                    current_state=updated_state,
                    gateway=create_gateway(),
                    entity_store=ent_store,
                    existing_profiles=existing_entity_profiles,
                    extraction=entity_extraction,
                    trace_id=trace_id,
                )
                updated_entity_ids = entity_result.saved_ids
        yield {
            "step": "entity_profiles_updated",
            "data": {
                "updated_entity_ids": updated_entity_ids,
                "director_event_result": model_data(director_event_result),
            },
        }
        action_preview = action_preview_fu.result()
    yield {
        "step": "action_preview_done",
        "data": {
            "action_preview": model_data(action_preview),
        },
    }

    saved_pending_ids: list[str] = []
    store_count_before = 0
    store_count_after = 0
    store_total = 0
    pending_count_after = 0
    batch_queued = False

    if write_db:
        with current_state_store() as state_store:
            state_store.save(updated_state)

        with short_term_memory_store() as short_store:
            short_store.add_turn(
                scene_id=updated_state.scene.scene_id,
                turn_index=pending.turn_index,
                user_input=pending.user_input,
                scene_text=effective_scene_text,
                narrative_type=effective_narrative_type,
            )
            short_term_after = short_store.get_recent()

        with pending_memory_store() as pending_store:
            saved_pending_ids = pending_store.save_many(pending_memory_result.items)
            pending_count_after = pending_store.count()
            should_batch = pending_store.should_trigger_batch(turn_threshold=5)

        with memory_store() as mem_store:
            store_count_before = mem_store.count()
            store_count_after = store_count_before
            store_total = len(mem_store.list_all())

        if should_batch:
            batch_queued = True
            BACKGROUND_EXECUTOR.submit(
                run_memory_batch_maintenance,
                updated_state,
                trace_id,
            )
        BACKGROUND_EXECUTOR.submit(
            run_scene_summary_maintenance,
            effective_scene_text,
            updated_state,
            updated_state.scene.scene_id,
            pending.turn_index,
            trace_id,
        )
    else:
        with short_term_memory_store() as short_store:
            short_term_after = short_store.get_recent()

        with memory_store() as mem_store:
            store_total = mem_store.count()

    PENDING_TURNS.pop(turn_id, None)

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
        memory_data["reason"] = "DB writes are disabled; pending memory candidates were extracted but not saved."

    scene_result_data = model_data(pending.scene_result)
    if scene_text is not None:
        scene_result_data["scene_text"] = scene_text

    yield {
        "step": "finish",
        "data": {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "write_db": write_db,
            "narrative_type": effective_narrative_type,
            "state_before": model_data(pending.state_before),
            "state_after": model_data(updated_state),
            "scene_result": scene_result_data,
            "action_preview": model_data(action_preview),
            "short_term_memory_after": model_data(short_term_after),
            "memory_extraction": memory_data,
            "updated_entity_ids": updated_entity_ids,
            "director_event_result": model_data(director_event_result),
            "status": "committed" if write_db else "dry_run",
        },
    }


def confirm_opening_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
    narrative_type: str | None = None,
):
    pending = PENDING_OPENINGS.get(turn_id)
    if pending is None:
        raise ValueError("Pending opening not found. Initialize again before confirming.")

    trace_id = str(uuid.uuid4())
    effective_scene_text = scene_text if scene_text is not None else pending.scene_result.scene_text
    effective_narrative_type = narrative_type or "transition"
    if effective_narrative_type not in NARRATIVE_TYPE_LABELS:
        effective_narrative_type = "transition"

    yield {
        "step": "confirm_started",
        "data": {
            "trace_id": trace_id,
            "narrative_type": effective_narrative_type,
            "opening": True,
        },
    }

    gateway1 = create_gateway()
    gateway2 = create_gateway()
    gateway3 = create_gateway()
    existing_entity_profiles = []
    if write_db:
        with entity_store() as ent_store:
            existing_entity_profiles = ent_store.list_all()

    def _do_state_update():
        updater = StateUpdater(gateway1)
        return updater.update(
            pending.current_state,
            effective_scene_text,
            trace_id=trace_id,
        )

    def _do_memory_draft_extract():
        extractor = MemoryDraftExtractor(gateway2)
        return extractor.extract(
            effective_scene_text,
            pending.current_state,
            turn_index=0,
            scene_id=pending.current_state.scene.scene_id,
            trace_id=trace_id,
        )

    def _do_entity_extract():
        extractor = EntityExtractor(gateway3)
        return extractor.extract(
            scene_text=effective_scene_text,
            existing_profiles=existing_entity_profiles,
            trace_id=trace_id,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        state_fu = pool.submit(_do_state_update)
        extract_fu = pool.submit(_do_memory_draft_extract)
        entity_extract_fu = pool.submit(_do_entity_extract) if write_db else None

        updated_state = None
        pending_memory_result = None
        entity_extraction = None

        first_wave = [state_fu, extract_fu]
        if entity_extract_fu is not None:
            first_wave.append(entity_extract_fu)

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

    pending_memory_items = fill_pending_memory_scene_metadata(
        pending_memory_result.items,
        updated_state,
    )
    pending_memory_result = pending_memory_result.model_copy(
        update={"items": pending_memory_items}
    )
    yield {
        "step": "memory_draft_metadata_filled",
        "data": {
            "location": updated_state.scene.location,
            "time_label": updated_state.world_clock.label,
            "pending_items": model_data(pending_memory_result.items),
        },
    }

    with scene_summary_store() as summary_store:
        recent_summaries = summary_store.get_recent(5)

    def _do_action_preview():
        return generate_action_preview_workflow(
            scene_text=effective_scene_text,
            current_state=updated_state,
            core_memory=core_memory_store().load_required(),
            recent_summaries=recent_summaries,
            gateway=create_gateway(),
            trace_id=trace_id,
        )

    updated_entity_ids: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        action_preview_fu = pool.submit(_do_action_preview)
        if write_db and entity_extraction is not None:
            with entity_store() as ent_store:
                entity_result = update_entity_profiles_from_extraction(
                    scene_text=effective_scene_text,
                    current_state=updated_state,
                    gateway=create_gateway(),
                    entity_store=ent_store,
                    existing_profiles=existing_entity_profiles,
                    extraction=entity_extraction,
                    trace_id=trace_id,
                )
                updated_entity_ids = entity_result.saved_ids
        yield {
            "step": "entity_profiles_updated",
            "data": {
                "updated_entity_ids": updated_entity_ids,
            },
        }
        action_preview = action_preview_fu.result()
    yield {
        "step": "action_preview_done",
        "data": {
            "action_preview": model_data(action_preview),
        },
    }

    saved_pending_ids: list[str] = []
    pending_count_after = 0
    batch_queued = False
    store_count_before = 0
    store_count_after = 0
    store_total = 0

    if write_db:
        with current_state_store() as state_store:
            state_store.save(updated_state)

        with short_term_memory_store() as short_store:
            short_store.clear()
            short_store.add_turn(
                scene_id=updated_state.scene.scene_id,
                turn_index=0,
                user_input="",
                scene_text=effective_scene_text,
                narrative_type=effective_narrative_type,
            )
            short_term_after = short_store.get_recent()

        with pending_memory_store() as pending_store:
            saved_pending_ids = pending_store.save_many(pending_memory_result.items)
            pending_count_after = pending_store.count()
            should_batch = pending_store.should_trigger_batch(turn_threshold=5)

        with memory_store() as mem_store:
            store_count_before = mem_store.count()
            store_count_after = store_count_before
            store_total = len(mem_store.list_all())

        if should_batch:
            batch_queued = True
            BACKGROUND_EXECUTOR.submit(
                run_memory_batch_maintenance,
                updated_state,
                trace_id,
            )
        BACKGROUND_EXECUTOR.submit(
            run_scene_summary_maintenance,
            effective_scene_text,
            updated_state,
            updated_state.scene.scene_id,
            0,
            trace_id,
        )
    else:
        with short_term_memory_store() as short_store:
            short_term_after = short_store.get_recent()
        with memory_store() as mem_store:
            store_total = mem_store.count()

    PENDING_OPENINGS.pop(turn_id, None)

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
        memory_data["reason"] = "DB writes are disabled; opening scene was not saved."

    scene_result_data = model_data(pending.scene_result)
    if scene_text is not None:
        scene_result_data["scene_text"] = scene_text

    yield {
        "step": "finish",
        "data": {
            "turn_id": turn_id,
            "trace_id": trace_id,
            "write_db": write_db,
            "opening": True,
            "narrative_type": effective_narrative_type,
            "state_before": model_data(pending.current_state),
            "state_after": model_data(updated_state),
            "scene_result": scene_result_data,
            "action_preview": model_data(action_preview),
            "short_term_memory_after": model_data(short_term_after),
            "memory_extraction": memory_data,
            "updated_entity_ids": updated_entity_ids,
            "status": "committed" if write_db else "dry_run",
        },
    }
