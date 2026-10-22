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

from .confirmation_cache import ConfirmationCache
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
    npc_profile_store: Callable
    core_memory_store: Callable
    promote_staged_director_npc_profile: Callable
    apply_staged_disclosure_upgrades: Callable
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
        if not effective_scene_text.strip():
            raise ValueError("effective_scene_text is empty; cannot confirm empty body")
        cache = ConfirmationCache(pending.confirmation_cache)
        cache.begin(effective_scene_text)

        if write_db:
            with services.current_state_store as state_store:
                persisted_state = state_store.load_or_default()
            if persisted_state != pipeline_input.state_before:
                raise ValueError(pipeline_input.stale_message)

        director_npc_profile_ids = cache.result("director_npc_profile_ids")
        confirmed_director_npc_drafts = cache.result("confirmed_director_npc_drafts")
        if write_db and not cache.produced("director_npc_profile_ids"):
            confirmations = services.promote_staged_director_npc_profile(
                pending,
                turn_index=pipeline_input.turn_index,
            )
            director_npc_profile_ids = [
                confirmation.npc_id for confirmation in confirmations
            ]
            confirmed_director_npc_drafts = [
                confirmation.draft for confirmation in confirmations
            ]
            cache.set_result("director_npc_profile_ids", director_npc_profile_ids)
            cache.set_result("confirmed_director_npc_drafts", confirmed_director_npc_drafts)

        if write_db:
            services.apply_staged_disclosure_upgrades(pending)

        # The staged Director NPC is NOT pre-added to CurrentState participants
        # here: whether they are actually present is decided by StateUpdater
        # from the confirmed Scene Facts. The confirmation system note still
        # maps an unnamed in-scene figure to the introduced NPCs for extraction.
        state_before_update = pipeline_input.state_before
        confirmation_scene_text = (
            services.project_scene_text_with_staged_director_npc(
            effective_scene_text,
            pending=pending,
            confirmed_director_npc_drafts=confirmed_director_npc_drafts,
            )
        )

        existing_npc_profiles = cache.result("existing_npc_profiles") or []
        if write_db and not cache.produced("existing_npc_profiles"):
            with services.npc_profile_store as npc_store:
                existing_npc_profiles = npc_store.list_all()
            cache.set_result("existing_npc_profiles", existing_npc_profiles)

        core_memory = services.core_memory_store.load_required()
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
            "cache": pending.confirmation_cache,
            "director_npc_profile_ids": director_npc_profile_ids,
            "confirmed_director_npc_drafts": confirmed_director_npc_drafts,
            "state_before_update": state_before_update,
            "confirmation_scene_text": confirmation_scene_text,
            "existing_npc_profiles": existing_npc_profiles,
            "core_memory": core_memory,
        }


@dataclass(frozen=True)
class ConfirmationCommitServices:
    current_state_store: Callable
    short_term_memory_store: Callable
    confirmed_history_store: Callable
    npc_profile_store: Callable
    continuity_store: Callable
    scene_summary_store: Callable


@dataclass
class ConfirmationCommitResult:
    short_term_after: Any
    continuity_result: ContinuityApplyResult
    continuity_total: int


class ConfirmationCommitter:
    """Persist the synchronous records in the current campaign transaction."""

    def __init__(self, services: ConfirmationCommitServices) -> None:
        self.services = services

    def commit(self, state: ConfirmationState) -> ConfirmationCommitResult:
        services = self.services
        write_db = state.get("write_db", True)
        pipeline_input = state["pipeline_input"]
        cache = ConfirmationCache(state["cache"])
        updated_state = state["updated_state"]

        if not write_db:
            with services.short_term_memory_store as short_store:
                short_term_after = short_store.get_recent()
            with services.continuity_store as continuity_store:
                continuity_total = continuity_store.count()
            return ConfirmationCommitResult(
                short_term_after=short_term_after,
                continuity_result=ContinuityApplyResult(),
                continuity_total=continuity_total,
            )

        with services.current_state_store as state_store:
            state_store.save(updated_state)

        with services.scene_summary_store as summary_store:
            summary_store.save_one(state["turn_summary"])

        with services.short_term_memory_store as short_store:
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
            short_term_after = short_store.get_recent()

        with services.confirmed_history_store as history_store:
            if pipeline_input.clear_confirmed_history_before_save:
                history_store.clear()
            history_store.upsert_turn(
                scene_id=updated_state.scene.scene_id,
                turn_index=pipeline_input.turn_index,
                user_input=pipeline_input.user_input,
                scene_text=state["effective_scene_text"],
            )

        with (
            services.confirmed_history_store as history_store,
            services.continuity_store as continuity_store,
        ):
            if pipeline_input.clear_continuity_before_save:
                continuity_store.clear()
            documents = history_store.documents_for_turn(
                pipeline_input.turn_index
            )
            evidence_documents = {
                document.document_id: document
                for document in documents
                if document.source_type == "scene_paragraph"
            }
            if not cache.produced("npc_turn_result"):
                raise RuntimeError(
                    "Confirmation commit is missing npc_turn_result"
                )
            npc_turn_result = cache.result("npc_turn_result")
            accepted_changes = (
                npc_turn_result.process_result.accepted_changes
            )
            with services.npc_profile_store as npc_store:
                npc_store.record_accepted_field_changes(
                    changes=accepted_changes,
                    evidence_documents=list(evidence_documents.values()),
                    turn_index=pipeline_input.turn_index,
                    scene_id=updated_state.scene.scene_id,
                    reset_before_record=(
                        pipeline_input.clear_confirmed_history_before_save
                    ),
                )
            continuity_result = ContinuityService(
                None,
                continuity_store,
            ).apply(
                state["continuity_projection"],
                turn_index=pipeline_input.turn_index,
                evidence_documents=evidence_documents,
            )
            continuity_total = continuity_store.count()

        return ConfirmationCommitResult(
            short_term_after=short_term_after,
            continuity_result=continuity_result,
            continuity_total=continuity_total,
        )


@dataclass(frozen=True)
class ConfirmationMaintenanceServices:
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

    def schedule_scene_summary(self, state: ConfirmationState) -> None:
        cache = ConfirmationCache(state["cache"])
        if not state.get("write_db", True):
            return

        pipeline_input = state["pipeline_input"]
        updated_state = state["updated_state"]
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
        cache.set_result("scene_summary_future", summary_future)
        cache.set_result("scene_summary_job_id", getattr(
            summary_future,
            "job_id",
            None,
        ))
        cache.set_result("scene_summary_operation_id", getattr(
            summary_future,
            "operation_id",
            None,
        ))



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
        director_npc_profile_ids = state.get("director_npc_profile_ids", [])
        updated_npc_profile_ids = state.get("updated_npc_profile_ids", [])
        npc_warnings = state.get("npc_warnings", [])

        profile_update_data = {
            "updated_npc_profile_ids": updated_npc_profile_ids,
            "director_npc_profile_ids": director_npc_profile_ids,
            "director_decision": model_data(director_decision),
            "director_executions": model_data(director_executions),
            "warnings": list(npc_warnings),
        }

        scene_result_data = model_data(pipeline_input.scene_result)
        if state.get("scene_text") is not None:
            scene_result_data["scene_text"] = state["scene_text"]

        finish_data = {
            "turn_id": state["turn_id"],
            "trace_id": state["trace_id"],
            "write_db": write_db,
            "warning_codes": ["confirmation_maintenance_schedule_failed"] if state.get("maintenance_schedule_failed") else [],
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
            "updated_npc_profile_ids": updated_npc_profile_ids,
            "status": "committed" if write_db else "dry_run",
            "director_npc_profile_ids": director_npc_profile_ids,
            "director_decision": model_data(director_decision),
            "director_executions": model_data(director_executions),
        }

        return [
            {"step": "npc_profiles_updated", "data": profile_update_data},
            {"step": "finish", "data": finish_data},
        ]


class ConfirmationFinalizer:
    """Coordinate commit, maintenance scheduling, cleanup, and presentation.

    Phase 2 splits the finalizer across the campaign transaction boundary: the
    canonical writes and their confirmation record run inside
    ``BEGIN IMMEDIATE ... COMMIT``; everything that must only happen after a
    committed confirmation (undo publication, pending cleanup,
    maintenance scheduling, user-facing events) runs in
    :meth:`finalize_after_commit`.
    """

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

    def finalize_in_transaction(
        self,
        state: ConfirmationState,
    ) -> tuple[ConfirmationState, ConfirmationCommitResult]:
        """Canonical writes that must be atomic with the confirmation."""
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
            self._write_recent_director_log(state)
            self.runtime.history.record_confirmation(
                state["runtime_services"].stores._get_conn(),
                state["turn_id"],
                scene_text=state["effective_scene_text"],
                initialization_fingerprint=(
                    initialization_record.fingerprint if initialization_record else None
                ),
                state_changes=model_data(state["state_change_set"]),
                state_warnings=model_data(state.get("state_update_warnings", [])),
                state_before=model_data(pipeline_input.state_before),
                state_after=model_data(state["updated_state"]),
            )

        return state, commit_result

    def finalize_after_commit(
        self,
        state: ConfirmationState,
        commit_result: ConfirmationCommitResult,
    ) -> ConfirmationState:
        """Control-plane follow-up that must only run after the transaction commits."""
        pipeline_input = state["pipeline_input"]
        pending = state["pending"]
        if state.get("write_db", True):
            if state.get("journaled"):
                self.runtime.complete_confirmation(pending)
            if isinstance(pending, PendingTurn):
                self.runtime.pending_turns.discard_group(pending.group_id)
            else:
                self.runtime.pending_openings.discard_group(pending.group_id)

        try:
            self.scheduler.schedule_scene_summary(state)
        except Exception as exc:
            state["maintenance_schedule_failed"] = True
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

    def _write_recent_director_log(self, state: ConfirmationState) -> None:
        """Persist the confirmed turn's generation + confirmation decisions.

        Runs inside the confirmation transaction, so it must use the
        operation-level stores from ``state``. Using the session-level
        ``self.runtime.services.stores`` would open a second connection to the
        same campaign.db while this transaction holds ``BEGIN IMMEDIATE`` and
        deadlock/timeout on the lock.
        """
        from app.domain.director.log import assemble_director_log_entry

        stores = state["runtime_services"].stores
        pipeline_input = state["pipeline_input"]
        entries: list[dict] = []
        gen_entry = getattr(state["pending"], "director_log_entry", None)
        if isinstance(gen_entry, dict) and gen_entry.get("phase") == "generation":
            entries.append(gen_entry)
        decision = state.get("director_decision")
        if decision is not None:
            entries.append(assemble_director_log_entry(
                turn_index=pipeline_input.turn_index,
                phase="confirmation",
                decision=decision,
                stores=stores,
                executions=state.get("director_executions"),
            ))
        if not entries:
            return
        self._write_recent_director_log_with_retry(entries, state, stores=stores)

    def _write_recent_director_log_with_retry(
        self,
        entries: list[dict],
        state: ConfirmationState,
        *,
        stores=None,
        attempts: int = 3,
    ) -> None:
        last_error: Exception | None = None
        stores = stores or state["runtime_services"].stores
        for attempt in range(1, attempts + 1):
            try:
                with stores.director_log as log_store:
                    log_store.save_entries(entries)
                return
            except Exception as exc:
                last_error = exc
        self.runtime.services.diagnostics.warning(
            source="confirmation",
            code="recent_director_log_write_failed",
            message=f"recent director log write failed after {attempts} attempts: {last_error}",
            trace_id=state["trace_id"],
            operation_type="confirmation",
            turn_index=_diagnostic_turn_index(state),
        )
