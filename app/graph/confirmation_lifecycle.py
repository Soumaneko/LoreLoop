from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.domain.director_npc.workflows import DIRECTOR_NPC_STANDBY_TARGET
from app.runtime.serialization import model_data
from app.runtime.session import CampaignRuntime, PendingTurn
from app.runtime.services import RuntimeServices

from .confirmation_state import ConfirmationState


@dataclass(frozen=True)
class ConfirmationPreparationServices:
    current_state_store: Callable
    director_npc_store: Callable
    entity_store: Callable
    npc_profile_store: Callable
    core_memory_store: Callable
    promote_dispatched_director_npc_profile: Callable
    state_with_confirmed_director_npc: Callable
    project_scene_text_for_confirmation: Callable
    runtime_services: RuntimeServices | None = None


class ConfirmationPreparer:
    """Validate a pending confirmation and assemble its immutable inputs."""

    def __init__(self, services: ConfirmationPreparationServices) -> None:
        self.services = services

    def prepare(
        self,
        pipeline_input,
        *,
        write_db: bool = True,
        scene_text: str | None = None,
    ) -> ConfirmationState:
        services = self.services
        pending = pipeline_input.pending
        turn_id = pipeline_input.turn_id
        trace_id = str(uuid.uuid4())
        effective_scene_text = (
            scene_text
            if scene_text is not None
            else pipeline_input.scene_result.scene_text
        )
        cache = pending.confirmation_cache
        if cache.get("key") != effective_scene_text:
            cache.clear()
            cache["key"] = effective_scene_text

        if write_db and not cache.get("state_saved"):
            with services.current_state_store() as state_store:
                persisted_state = state_store.load_or_default()
            if persisted_state != pipeline_input.state_before:
                raise ValueError(pipeline_input.stale_message)

        director_npc_profile_id = cache.get("director_npc_profile_id")
        confirmed_director_npc_draft = cache.get("confirmed_director_npc_draft")
        director_npc_restock_scheduled = bool(
            cache.get("director_npc_restock_scheduled")
        )
        if (
            pipeline_input.handle_director_npc
            and write_db
            and director_npc_profile_id is None
            and "director_npc_profile_id" not in cache
        ):
            director_npc_confirmation = (
                services.promote_dispatched_director_npc_profile(
                    pending,
                    turn_index=pipeline_input.turn_index,
                )
            )
            if director_npc_confirmation is not None:
                director_npc_profile_id = director_npc_confirmation.npc_id
                confirmed_director_npc_draft = director_npc_confirmation.draft
            cache["director_npc_profile_id"] = director_npc_profile_id
            cache["confirmed_director_npc_draft"] = confirmed_director_npc_draft

        director_npc_result = getattr(pending, "director_npc_result", None)
        if (
            pipeline_input.handle_director_npc
            and write_db
            and director_npc_result is not None
            and director_npc_result.dispatch is not None
            and director_npc_result.dispatch.action != "none"
            and not cache.get("director_npc_last_dispatch_turn_set")
        ):
            with services.director_npc_store() as npc_store:
                npc_store.set_meta_int(
                    "last_dispatch_turn",
                    pipeline_input.turn_index,
                )
            cache["director_npc_last_dispatch_turn_set"] = True

        state_before_update = services.state_with_confirmed_director_npc(
            pipeline_input.state_before,
            confirmed_director_npc_draft,
        )
        confirmation_scene_text = services.project_scene_text_for_confirmation(
            effective_scene_text,
            pending=pending,
            confirmed_director_npc_draft=confirmed_director_npc_draft,
        )

        existing_entity_profiles = cache.get("existing_entity_profiles", [])
        existing_npc_profiles = cache.get("existing_npc_profiles", [])
        if write_db and "existing_entity_profiles" not in cache:
            with (
                services.entity_store() as ent_store,
                services.npc_profile_store() as npc_store,
            ):
                existing_entity_profiles = ent_store.list_all()
                existing_npc_profiles = npc_store.list_all()
            cache["existing_entity_profiles"] = existing_entity_profiles
            cache["existing_npc_profiles"] = existing_npc_profiles

        core_memory = services.core_memory_store().load_required()
        return {
            "runtime_services": services.runtime_services,
            "events": [{
                "step": "confirm_started",
                "data": {
                    "trace_id": trace_id,
                    **pipeline_input.confirm_started_extra,
                },
            }],
            "node_errors": [],
            "pipeline_input": pipeline_input,
            "write_db": write_db,
            "scene_text": scene_text,
            "pending": pending,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "effective_scene_text": effective_scene_text,
            "cache": cache,
            "director_npc_profile_id": director_npc_profile_id,
            "confirmed_director_npc_draft": confirmed_director_npc_draft,
            "director_npc_restock_scheduled": director_npc_restock_scheduled,
            "state_before_update": state_before_update,
            "confirmation_scene_text": confirmation_scene_text,
            "existing_entity_profiles": existing_entity_profiles,
            "existing_npc_profiles": existing_npc_profiles,
            "core_memory": core_memory,
        }


@dataclass(frozen=True)
class ConfirmationCommitServices:
    current_state_store: Callable
    short_term_memory_store: Callable
    pending_memory_store: Callable
    memory_store: Callable


@dataclass
class ConfirmationCommitResult:
    short_term_after: Any
    saved_pending_ids: list[str]
    pending_count_after: int
    should_batch: bool
    store_count_before: int
    store_count_after: int
    store_total: int


class ConfirmationCommitter:
    """Persist the synchronous confirmation records with retry-cache guards."""

    def __init__(self, services: ConfirmationCommitServices) -> None:
        self.services = services

    def commit(self, state: ConfirmationState) -> ConfirmationCommitResult:
        services = self.services
        write_db = state.get("write_db", True)
        pipeline_input = state["pipeline_input"]
        cache = state["cache"]
        updated_state = state["updated_state"]

        if not write_db:
            with services.short_term_memory_store() as short_store:
                short_term_after = short_store.get_recent()
            with services.memory_store() as mem_store:
                store_total = mem_store.count()
            return ConfirmationCommitResult(
                short_term_after=short_term_after,
                saved_pending_ids=[],
                pending_count_after=0,
                should_batch=False,
                store_count_before=0,
                store_count_after=0,
                store_total=store_total,
            )

        if not cache.get("state_saved"):
            with services.current_state_store() as state_store:
                state_store.save(updated_state)
            cache["state_saved"] = True

        with services.short_term_memory_store() as short_store:
            if not cache.get("short_term_saved"):
                if pipeline_input.clear_short_term_before_save:
                    short_store.clear()
                short_store.add_turn(
                    scene_id=updated_state.scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=state["effective_scene_text"],
                    closing_prompt=pipeline_input.scene_result.closing_prompt,
                )
                cache["short_term_saved"] = True
            short_term_after = short_store.get_recent()

        with services.pending_memory_store() as pending_store:
            if not cache.get("pending_memories_saved"):
                saved_pending_ids = pending_store.save_many(
                    state["pending_memory_result"].items
                )
                cache["saved_pending_ids"] = saved_pending_ids
                cache["pending_memories_saved"] = True
            else:
                saved_pending_ids = cache.get("saved_pending_ids", [])
            pending_count_after = pending_store.count()
            should_batch = pending_store.should_trigger_batch(turn_threshold=5)

        with services.memory_store() as mem_store:
            store_count_before = mem_store.count()
            store_count_after = store_count_before
            store_total = len(mem_store.list_all())

        return ConfirmationCommitResult(
            short_term_after=short_term_after,
            saved_pending_ids=saved_pending_ids,
            pending_count_after=pending_count_after,
            should_batch=should_batch,
            store_count_before=store_count_before,
            store_count_after=store_count_after,
            store_total=store_total,
        )


@dataclass(frozen=True)
class ConfirmationMaintenanceServices:
    director_npc_store: Callable
    run_director_npc_restock: Callable
    run_scene_summary_maintenance: Callable
    run_memory_batch_maintenance: Callable
    submit_memory_batch_once: Callable


@dataclass
class ConfirmationScheduleResult:
    director_npc_restock_scheduled: bool
    batch_queued: bool


class ConfirmationMaintenanceScheduler:
    """Schedule optional post-confirmation maintenance through CampaignRuntime."""

    def __init__(
        self,
        runtime: CampaignRuntime,
        services: ConfirmationMaintenanceServices,
    ) -> None:
        self.runtime = runtime
        self.services = services

    def schedule_restock(self, state: ConfirmationState) -> bool:
        if not state.get("write_db", True):
            return bool(state.get("director_npc_restock_scheduled"))

        pipeline_input = state["pipeline_input"]
        cache = state["cache"]
        scheduled = bool(state.get("director_npc_restock_scheduled"))
        if (
            not pipeline_input.handle_director_npc
            or cache.get("director_npc_restock_scheduled")
        ):
            return scheduled

        with self.services.director_npc_store() as npc_store:
            should_restock = (
                len(npc_store.list_standby()) < DIRECTOR_NPC_STANDBY_TARGET
            )
        if not should_restock:
            return scheduled

        args = (
            state["trace_id"],
            pipeline_input.turn_index,
        )
        kwargs = {
            "core_memory": state["core_memory"],
            "protagonist_name": state["updated_state"].player.name,
        }
        if pipeline_input.director_npc_restock_sync:
            self.services.run_director_npc_restock(*args, **kwargs)
        else:
            future = self.runtime.jobs.submit_job(
                "director_npc_restock",
                self.services.run_director_npc_restock,
                *args,
                trace_id=state["trace_id"],
                turn_index=pipeline_input.turn_index,
                metadata={"campaign_id": self.runtime.campaign_id},
                **kwargs,
            )
            cache["director_npc_restock_job_id"] = getattr(
                future,
                "job_id",
                None,
            )
        cache["director_npc_restock_scheduled"] = True
        state["director_npc_restock_scheduled"] = True
        return True

    def schedule_followups(
        self,
        state: ConfirmationState,
        commit_result: ConfirmationCommitResult,
        *,
        director_npc_restock_scheduled: bool,
    ) -> ConfirmationScheduleResult:
        cache = state["cache"]
        if not state.get("write_db", True):
            return ConfirmationScheduleResult(
                director_npc_restock_scheduled=director_npc_restock_scheduled,
                batch_queued=bool(cache.get("memory_batch_scheduled")),
            )

        pipeline_input = state["pipeline_input"]
        updated_state = state["updated_state"]
        summary_future = cache.get("scene_summary_future")
        if not cache.get("scene_summary_scheduled"):
            summary_future = self.runtime.jobs.submit_job(
                "scene_summary",
                self.services.run_scene_summary_maintenance,
                state["confirmation_scene_text"],
                updated_state,
                updated_state.scene.scene_id,
                pipeline_input.turn_index,
                state["trace_id"],
                trace_id=state["trace_id"],
                turn_index=pipeline_input.turn_index,
                metadata={
                    "campaign_id": self.runtime.campaign_id,
                    "scene_id": updated_state.scene.scene_id,
                },
                core_memory=state["core_memory"],
            )
            cache["scene_summary_future"] = summary_future
            cache["scene_summary_job_id"] = getattr(
                summary_future,
                "job_id",
                None,
            )
            cache["scene_summary_scheduled"] = True

        batch_queued = bool(cache.get("memory_batch_scheduled"))
        if commit_result.should_batch and not batch_queued:
            batch_queued = self.services.submit_memory_batch_once(
                self.services.run_memory_batch_maintenance,
                updated_state,
                state["trace_id"],
                pipeline_input.turn_index,
                core_memory=state["core_memory"],
                wait_for_summary=summary_future,
            )
            cache["memory_batch_scheduled"] = batch_queued

        return ConfirmationScheduleResult(
            director_npc_restock_scheduled=director_npc_restock_scheduled,
            batch_queued=batch_queued,
        )


class ConfirmationEventBuilder:
    """Build final user-facing confirmation events without performing I/O."""

    def build(
        self,
        state: ConfirmationState,
        commit_result: ConfirmationCommitResult,
        schedule_result: ConfirmationScheduleResult,
    ) -> list[dict]:
        pipeline_input = state["pipeline_input"]
        pending = state["pending"]
        write_db = state.get("write_db", True)
        action_preview = state.get("action_preview")
        director_event_result = state.get("director_event_result")
        director_npc_profile_id = state.get("director_npc_profile_id")
        updated_entity_ids = state.get("updated_entity_ids", [])
        updated_npc_profile_ids = state.get("updated_npc_profile_ids", [])
        npc_extraction = state.get("npc_extraction")

        entity_update_data = {
            "updated_entity_ids": updated_entity_ids,
            "updated_npc_profile_ids": updated_npc_profile_ids,
        }
        if pipeline_input.handle_director_npc or pipeline_input.update_director_event:
            entity_update_data.update({
                "director_npc_profile_id": director_npc_profile_id,
                "director_npc_restock_scheduled": (
                    schedule_result.director_npc_restock_scheduled
                ),
                "director_npc_result": model_data(
                    getattr(pending, "director_npc_result", None)
                ),
                "director_event_result": model_data(director_event_result),
            })

        memory_data: dict = {
            "pending_items": model_data(state["pending_memory_result"].items),
            "saved_pending_ids": commit_result.saved_pending_ids,
            "pending_count_after": commit_result.pending_count_after,
            "batch_queued": schedule_result.batch_queued,
            "final_memories": [],
            "saved_ids": [],
            "store_count_before": commit_result.store_count_before,
            "store_count_after": commit_result.store_count_after,
            "store_total": commit_result.store_total,
        }
        if not write_db:
            memory_data["dry_run"] = True
            memory_data["reason"] = pipeline_input.dry_run_memory_reason

        scene_result_data = model_data(pipeline_input.scene_result)
        if state.get("scene_text") is not None:
            scene_result_data["scene_text"] = state["scene_text"]

        finish_data = {
            "turn_id": state["turn_id"],
            "trace_id": state["trace_id"],
            "write_db": write_db,
            **pipeline_input.finish_extra,
            "state_before": model_data(pipeline_input.state_before),
            "state_after": model_data(state["updated_state"]),
            "state_changes": model_data(state["state_change_set"]),
            "scene_result": scene_result_data,
            "action_preview": model_data(action_preview),
            "short_term_memory_after": model_data(commit_result.short_term_after),
            "memory_extraction": memory_data,
            "updated_entity_ids": updated_entity_ids,
            "updated_npc_profile_ids": updated_npc_profile_ids,
            "npc_extraction": (
                model_data(npc_extraction)
                if npc_extraction is not None
                else None
            ),
            "status": "committed" if write_db else "dry_run",
        }
        if pipeline_input.handle_director_npc or pipeline_input.update_director_event:
            finish_data.update({
                "director_npc_profile_id": director_npc_profile_id,
                "director_npc_restock_scheduled": (
                    schedule_result.director_npc_restock_scheduled
                ),
                "director_npc_result": model_data(
                    getattr(pending, "director_npc_result", None)
                ),
                "director_event_result": model_data(director_event_result),
            })

        return [
            {"step": "entity_profiles_updated", "data": entity_update_data},
            {"step": "finish", "data": finish_data},
        ]


class ConfirmationFinalizer:
    """Coordinate commit, maintenance scheduling, cleanup, and presentation."""

    def __init__(
        self,
        runtime: CampaignRuntime,
        committer: ConfirmationCommitter,
        scheduler: ConfirmationMaintenanceScheduler,
        event_builder: ConfirmationEventBuilder,
    ) -> None:
        self.runtime = runtime
        self.committer = committer
        self.scheduler = scheduler
        self.event_builder = event_builder

    def finalize(self, state: ConfirmationState) -> ConfirmationState:
        restock_scheduled = self.scheduler.schedule_restock(state)
        commit_result = self.committer.commit(state)
        schedule_result = self.scheduler.schedule_followups(
            state,
            commit_result,
            director_npc_restock_scheduled=restock_scheduled,
        )

        if state.get("write_db", True):
            pipeline_input = state["pipeline_input"]
            self.runtime.history.record_state_audit(
                state["turn_id"],
                state_changes=model_data(state["state_change_set"]),
                state_before=model_data(pipeline_input.state_before),
                state_after=model_data(state["updated_state"]),
            )
            pending = state["pending"]
            if isinstance(pending, PendingTurn):
                self.runtime.pending_turns.discard_group(pending.group_id)
            else:
                self.runtime.pending_openings.remove(state["turn_id"])

        state["events"] = [
            *state.get("events", []),
            *self.event_builder.build(state, commit_result, schedule_result),
        ]
        return state
