from __future__ import annotations

import traceback
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from app.core.llm.token_usage import get_trace_token_usage
from app.domain.continuity import ContinuityApplyResult, ContinuityService
from app.runtime.serialization import model_data
from app.runtime.session import CampaignRuntime, PendingTurn
from app.runtime.services import RuntimeServices

from .confirmation_state import ConfirmationState


def _diagnostic_turn_index(state: ConfirmationState) -> int | None:
    pipeline_input = state.get("pipeline_input")
    if isinstance(pipeline_input, dict):
        value = pipeline_input.get("turn_index")
    else:
        value = getattr(pipeline_input, "turn_index", None)
    return value if isinstance(value, int) else None


@dataclass(frozen=True)
class ConfirmationPreparationServices:
    current_state_store: Callable
    entity_store: Callable
    npc_profile_store: Callable
    core_memory_store: Callable
    promote_staged_director_npc_profile: Callable
    state_with_staged_director_npc: Callable
    project_scene_text_with_staged_director_npc: Callable
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
        journaled: bool = False,
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
        if (
            pipeline_input.run_director_agent
            and write_db
            and director_npc_profile_id is None
            and "director_npc_profile_id" not in cache
        ):
            director_npc_confirmation = (
                services.promote_staged_director_npc_profile(
                    pending,
                    turn_index=pipeline_input.turn_index,
                )
            )
            if director_npc_confirmation is not None:
                director_npc_profile_id = director_npc_confirmation.npc_id
                confirmed_director_npc_draft = director_npc_confirmation.draft
            cache["director_npc_profile_id"] = director_npc_profile_id
            cache["confirmed_director_npc_draft"] = confirmed_director_npc_draft

        state_before_update = services.state_with_staged_director_npc(
            pipeline_input.state_before,
            confirmed_director_npc_draft,
        )
        confirmation_scene_text = (
            services.project_scene_text_with_staged_director_npc(
            effective_scene_text,
            pending=pending,
            confirmed_director_npc_draft=confirmed_director_npc_draft,
            )
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
            "journaled": journaled,
            "scene_text": scene_text,
            "pending": pending,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "effective_scene_text": effective_scene_text,
            "cache": cache,
            "director_npc_profile_id": director_npc_profile_id,
            "confirmed_director_npc_draft": confirmed_director_npc_draft,
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
    confirmed_history_store: Callable
    continuity_store: Callable
    scene_summary_store: Callable


@dataclass
class ConfirmationCommitResult:
    short_term_after: Any
    continuity_result: ContinuityApplyResult
    continuity_total: int


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
            with services.continuity_store() as continuity_store:
                continuity_total = continuity_store.count()
            return ConfirmationCommitResult(
                short_term_after=short_term_after,
                continuity_result=ContinuityApplyResult(),
                continuity_total=continuity_total,
            )

        if not cache.get("state_saved"):
            with services.current_state_store() as state_store:
                state_store.save(updated_state)
            cache["state_saved"] = True

        if not cache.get("turn_summary_saved"):
            with services.scene_summary_store() as summary_store:
                cache["turn_summary_id"] = summary_store.save_one(
                    state["turn_summary"]
                )
            cache["turn_summary_saved"] = True

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
                    action_preview=state.get("action_preview"),
                    player_resolution=getattr(
                        pipeline_input.pending,
                        "player_resolution",
                        None,
                    ),
                    token_usage={
                        "generation": (
                            getattr(pipeline_input.pending, "token_usage", None)
                            or {}
                        ).get("generation"),
                        "confirmation": get_trace_token_usage(state["trace_id"]),
                    },
                )
                cache["short_term_saved"] = True
            short_term_after = short_store.get_recent()

        if not cache.get("confirmed_history_saved"):
            with services.confirmed_history_store() as history_store:
                if pipeline_input.clear_confirmed_history_before_save:
                    history_store.clear()
                history_store.upsert_turn(
                    scene_id=updated_state.scene.scene_id,
                    turn_index=pipeline_input.turn_index,
                    user_input=pipeline_input.user_input,
                    scene_text=state["effective_scene_text"],
                )
            cache["confirmed_history_saved"] = True

        with (
            services.confirmed_history_store() as history_store,
            services.continuity_store() as continuity_store,
        ):
            if pipeline_input.clear_continuity_before_save:
                continuity_store.clear()
            documents = history_store.documents_for_turn(
                pipeline_input.turn_index
            )
            continuity_result = ContinuityService(
                None,
                continuity_store,
            ).apply(
                state["continuity_projection"],
                turn_index=pipeline_input.turn_index,
                available_evidence_ids={
                    document.document_id
                    for document in documents
                    if document.source_type == "scene_paragraph"
                },
            )
            continuity_total = continuity_store.count()

        return ConfirmationCommitResult(
            short_term_after=short_term_after,
            continuity_result=continuity_result,
            continuity_total=continuity_total,
        )


@dataclass(frozen=True)
class ConfirmationMaintenanceServices:
    run_setting_seed_maintenance: Callable
    run_scene_summary_maintenance: Callable


class ConfirmationMaintenanceScheduler:
    """Schedule optional post-confirmation maintenance through CampaignRuntime."""

    def __init__(
        self,
        runtime: CampaignRuntime,
        services: ConfirmationMaintenanceServices,
    ) -> None:
        self.runtime = runtime
        self.services = services

    def schedule_setting_seed_maintenance(self, state: ConfirmationState) -> None:
        if not state.get("write_db", True):
            return

        pipeline_input = state["pipeline_input"]
        cache = state["cache"]
        if cache.get("setting_seed_maintenance_scheduled"):
            return
        args = (
            state["trace_id"],
            pipeline_input.turn_index,
        )
        existing_ids = {
            profile.npc_id for profile in state.get("existing_npc_profiles", [])
        }
        new_npc_profile_ids = [
            npc_id
            for npc_id in [
                *state.get("updated_npc_profile_ids", []),
            ]
            if npc_id not in existing_ids
        ]
        if (
            state.get("confirmed_director_npc_draft") is not None
            and state.get("director_npc_profile_id")
        ):
            new_npc_profile_ids.append(state["director_npc_profile_id"])
        new_npc_profile_ids = list(dict.fromkeys(new_npc_profile_ids))
        kwargs = {
            "core_memory": state["core_memory"],
            "scene_text": state["effective_scene_text"],
            "scene_facts_json": state["scene_facts_json"],
            "new_npc_profile_ids": new_npc_profile_ids,
        }
        future = self.runtime.jobs.submit_job(
            "setting_seed_extract",
            self.services.run_setting_seed_maintenance,
            *args,
            trace_id=state["trace_id"],
            turn_index=pipeline_input.turn_index,
            metadata={"campaign_id": self.runtime.campaign_id},
            **kwargs,
        )
        cache["setting_seed_maintenance_job_id"] = getattr(
            future,
            "job_id",
            None,
        )
        cache["setting_seed_maintenance_operation_id"] = getattr(
            future,
            "operation_id",
            None,
        )
        cache["setting_seed_maintenance_scheduled"] = True

    def schedule_scene_summary(self, state: ConfirmationState) -> None:
        cache = state["cache"]
        if not state.get("write_db", True):
            return

        pipeline_input = state["pipeline_input"]
        updated_state = state["updated_state"]
        summary_future = cache.get("scene_summary_future")
        if not cache.get("scene_summary_scheduled"):
            summary_future = self.runtime.jobs.submit_job(
                "scene_summary",
                self.services.run_scene_summary_maintenance,
                state["trace_id"],
                pipeline_input.turn_index,
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
            cache["scene_summary_operation_id"] = getattr(
                summary_future,
                "operation_id",
                None,
            )
            cache["scene_summary_scheduled"] = True


class ConfirmationEventBuilder:
    """Build final user-facing confirmation events without performing I/O."""

    def build(
        self,
        state: ConfirmationState,
        commit_result: ConfirmationCommitResult,
    ) -> list[dict]:
        pipeline_input = state["pipeline_input"]
        write_db = state.get("write_db", True)
        action_preview = state.get("action_preview")
        director_decision = state.get("director_decision")
        director_executions = state.get("director_executions")
        director_npc_profile_id = state.get("director_npc_profile_id")
        updated_entity_ids = state.get("updated_entity_ids", [])
        updated_npc_profile_ids = state.get("updated_npc_profile_ids", [])
        npc_extraction = state.get("npc_extraction")

        entity_update_data = {
            "updated_entity_ids": updated_entity_ids,
            "updated_npc_profile_ids": updated_npc_profile_ids,
        }
        if pipeline_input.run_director_agent:
            entity_update_data.update({
                "director_npc_profile_id": director_npc_profile_id,
                "director_decision": model_data(director_decision),
                "director_executions": model_data(director_executions),
            })

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
            "state_update_warnings": model_data(
                state.get("state_update_warnings", [])
            ),
            "scene_result": scene_result_data,
            "action_preview": model_data(action_preview),
            "short_term_memory_after": model_data(commit_result.short_term_after),
            "continuity_projection": model_data(
                state["continuity_projection"]
            ),
            "continuity_apply_result": model_data(
                commit_result.continuity_result
            ),
            "continuity_total": commit_result.continuity_total,
            "updated_entity_ids": updated_entity_ids,
            "updated_npc_profile_ids": updated_npc_profile_ids,
            "npc_extraction": (
                model_data(npc_extraction)
                if npc_extraction is not None
                else None
            ),
            "status": "committed" if write_db else "dry_run",
        }
        if pipeline_input.run_director_agent:
            finish_data.update({
                "director_npc_profile_id": director_npc_profile_id,
                "director_decision": model_data(director_decision),
                "director_executions": model_data(director_executions),
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
        pipeline_input = state["pipeline_input"]
        pending = state["pending"]
        initialization_record = None
        if (
            state.get("write_db", True)
            and getattr(pipeline_input, "complete_initialization", False)
        ):
            initialization_record = self.runtime.initialization.load()
            if (
                initialization_record is None
                or initialization_record.fingerprint
                != pending.initialization_fingerprint
                or initialization_record.phase != "opening_pending"
                or initialization_record.foundation is None
            ):
                raise RuntimeError(
                    "Opening confirmation lost its initialization record"
                )

        commit_result = self.committer.commit(state)

        if state.get("write_db", True):
            self.runtime.history.record_state_audit(
                state["turn_id"],
                state_changes=model_data(state["state_change_set"]),
                state_warnings=model_data(state.get("state_update_warnings", [])),
                state_before=model_data(pipeline_input.state_before),
                state_after=model_data(state["updated_state"]),
            )
            if initialization_record is not None:
                self.runtime.initialization.save(
                    initialization_record.model_copy(update={"phase": "completed"})
                )
            if state.get("journaled"):
                self.runtime.complete_confirmation(pending)
                state["journal_completed"] = True
            if isinstance(pending, PendingTurn):
                self.runtime.pending_turns.discard_group(pending.group_id)
            else:
                self.runtime.pending_openings.discard_group(pending.group_id)

        try:
            self.scheduler.schedule_setting_seed_maintenance(state)
        except Exception as exc:
            self.runtime.services.diagnostics.warning(
                source="confirmation",
                code="setting_seed_maintenance_schedule_failed",
                message=str(exc),
                trace_id=state["trace_id"],
                operation_type="confirmation",
                turn_index=_diagnostic_turn_index(state),
            )
            traceback.print_exc()
        try:
            self.scheduler.schedule_scene_summary(state)
        except Exception as exc:
            self.runtime.services.diagnostics.warning(
                source="confirmation",
                code="confirmation_maintenance_schedule_failed",
                message=str(exc),
                trace_id=state["trace_id"],
                operation_type="confirmation",
                turn_index=_diagnostic_turn_index(state),
            )
            traceback.print_exc()
        state["events"] = [
            *state.get("events", []),
            *self.event_builder.build(state, commit_result),
        ]
        return state
