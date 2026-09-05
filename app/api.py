"""Public application API used by the debug server and local tools."""

from typing import Any

from app.core.client_error import ClientValidationError
from app.graph.confirmation_graph import confirm_stream
from app.graph.generation_graph import (
    feedback_rewrite_stream,
    generate_turn_stream,
    rephrase_turn_stream,
    reroll_turn_stream,
)
from app.graph.initialization_graph import initialization_stream
from app.graph.opening_rewrite import rewrite_opening_stream
from app.graph.initialization_helpers import (
    generate_campaign_setup_stream,
    preview_initialization_config,
)
from app.runtime.debug_state import load_debug_state
from app.runtime.player_records import (
    load_player_events as _load_player_events,
    load_player_records as _load_player_records,
    records_view_for_experience_mode,
    update_player_record as _update_player_record,
)
from app.runtime.core_memory import (
    load_core_memory_items as _load_core_memory_items,
    save_core_memory_items as _save_core_memory_items,
)
from app.runtime.player_state import load_player_history, load_player_state
from app.runtime.settings import (
    experience_mode as _experience_mode,
    save_canon_validation,
    save_dice_display,
    save_difficulty,
    save_experience_mode,
    save_max_chars,
)
from app.runtime.reader_preferences import (
    load_reader_preferences,
    save_reader_preferences,
)
from app.runtime.model_config import (
    activate_model_config,
    delete_model_config,
    list_model_configs,
    model_config_status,
    save_model_config,
    test_model_connection,
)
from app.runtime.models_dev_catalog import catalog_response as model_presets
from app.runtime.initialization_draft import (
    load_initialization_draft,
    save_initialization_draft,
)
from app.runtime.saves import (
    copy_save,
    create_save,
    delete_save,
    export_save_archive,
    import_save_archive,
    list_saves,
    rename_save,
    switch_save,
)
from app.runtime.session import get_campaign_runtime
from app.runtime.jobs import JobStatus
from app.runtime.operation_models import OperationStatus
from app.runtime.operation_types import operation_policy


def _submit_managed_operation(
    operation_type: str,
    stream_factory,
    /,
    *args,
    request_id: str | None = None,
    metadata: dict | None = None,
    supersedes_recoverable_types: tuple[str, ...] | None = None,
    **kwargs,
) -> str:
    policy = operation_policy(operation_type)
    runtime = get_campaign_runtime()
    with runtime.operation_submission():
        if not runtime.is_model_configured():
            raise ClientValidationError("请先在模型设置中配置并测试连接")
        return runtime.operations.submit_stream(
            policy.operation_type,
            stream_factory,
            *args,
            request_id=request_id,
            exclusive_key=policy.exclusive_key,
            requires_ack=policy.requires_ack,
            supersedes_recoverable_types=(
                policy.supersedes
                if supersedes_recoverable_types is None
                else supersedes_recoverable_types
            ),
            metadata=metadata,
            **kwargs,
        )


def start_generation_operation(
    user_input: str,
    *,
    difficulty: str,
    max_chars: int,
    request_id: str | None = None,
) -> str:
    return _submit_managed_operation(
        "generation",
        generate_turn_stream,
        user_input,
        difficulty=difficulty,
        max_chars=max_chars,
        request_id=request_id,
        metadata={"user_input": user_input},
    )


def _start_pending_variant_operation(
    operation_type: str,
    stream_factory,
    turn_id: str,
    *,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ClientValidationError("Pending turn not found. Generate a scene before retrying.")
    return _submit_managed_operation(
        operation_type,
        stream_factory,
        turn_id,
        request_id=request_id,
        metadata={
            "turn_id": turn_id,
            "group_id": pending.group_id,
            "user_input": pending.user_input,
            "retry_mode": operation_type,
        },
    )


def start_rephrase_operation(
    turn_id: str,
    *,
    request_id: str | None = None,
) -> str:
    return _start_pending_variant_operation(
        "rephrase",
        rephrase_turn_stream,
        turn_id,
        request_id=request_id,
    )


def start_reroll_operation(
    turn_id: str,
    *,
    request_id: str | None = None,
) -> str:
    return _start_pending_variant_operation(
        "reroll",
        reroll_turn_stream,
        turn_id,
        request_id=request_id,
    )


def start_feedback_rewrite_operation(
    turn_id: str,
    *,
    source_scene_text: str,
    user_feedback: str,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ClientValidationError("Pending turn not found. Generate a scene before rewriting.")
    return _submit_managed_operation(
        "feedback_rewrite",
        feedback_rewrite_stream,
        turn_id,
        source_scene_text=source_scene_text,
        user_feedback=user_feedback,
        request_id=request_id,
        metadata={
            "turn_id": turn_id,
            "group_id": pending.group_id,
            "user_input": pending.user_input,
        },
    )


def start_opening_rewrite_operation(
    turn_id: str,
    *,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    pending = runtime.pending_openings.get(turn_id)
    if pending is None:
        raise ClientValidationError("Pending opening not found.")
    variants = runtime.pending_openings.variants(pending.group_id)
    version_index = max(item.version_index for item in variants) + 1
    return _submit_managed_operation(
        "opening_rewrite",
        rewrite_opening_stream,
        turn_id,
        request_id=request_id,
        metadata={
            "turn_id": turn_id,
            "group_id": pending.group_id,
            "version_index": version_index,
            "opening": True,
            "user_input": "",
        },
    )


def start_confirmation_operation(
    turn_id: str,
    *,
    write_db: bool = True,
    scene_text: str | None = None,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    pending = runtime.pending_turns.get(turn_id)
    opening = runtime.pending_openings.get(turn_id)
    selected = pending or opening
    if selected is None:
        raise ClientValidationError("Pending turn not found. Generate a scene before confirming.")
    return _submit_managed_operation(
        "confirmation",
        confirm_stream,
        turn_id,
        write_db=write_db,
        scene_text=scene_text,
        request_id=request_id,
        supersedes_recoverable_types=(None if write_db else ()),
        metadata={
            "turn_id": turn_id,
            "kind": "turn" if pending is not None else "opening",
            "user_input": getattr(selected, "user_input", ""),
            "write_db": write_db,
        },
    )


def save_pending_scene_edit(
    turn_id: str,
    scene_text: str | None,
) -> dict:
    return get_campaign_runtime().save_pending_scene_edit(turn_id, scene_text)


def load_player_records(view: str = "general") -> dict:
    """Return the player-visible archive projection for the records drawer."""
    return _load_player_records(view=view)


def update_player_record(
    kind: str,
    record_id: str,
    payload: dict | None,
    view: str = "general",
) -> dict:
    """Update one existing continuity/NPC/Setting record from the player UI."""
    return _update_player_record(kind, record_id, payload, view=view)


def player_records_view() -> str:
    """Return the server-owned record projection tier for the current mode."""
    return records_view_for_experience_mode(_experience_mode())


def load_player_events() -> dict:
    """Return the creator-mode read-only Director Event browse projection."""
    return _load_player_events()


def load_core_memory_items() -> dict:
    """Return the creator-mode Core Memory item editor projection."""
    return _load_core_memory_items()


def save_core_memory_items(payload: dict) -> dict:
    """Persist the curated Core Memory item list from the creator editor."""
    return _save_core_memory_items(payload)


def record_player_diagnostic(payload: dict[str, Any]) -> dict[str, bool]:
    severity = str(payload.get("severity", "error")).strip().lower()
    if severity not in {"warning", "error"}:
        raise ClientValidationError("severity must be warning or error")
    code = str(payload.get("code", "player_frontend_error")).strip()
    if not code or len(code) > 120:
        raise ClientValidationError("diagnostic code must contain 1-120 characters")
    message = str(payload.get("message", "")).strip()
    if not message:
        raise ClientValidationError("diagnostic message is required")
    runtime = get_campaign_runtime()
    logger = runtime.diagnostics
    log = logger.warning if severity == "warning" else logger.error
    log(
        source="player_frontend",
        code=code,
        message=message[:2000],
        campaign_id=runtime.campaign_id,
        details={
            "context": str(payload.get("context", ""))[:120],
            "stack": str(payload.get("stack", ""))[:8000],
            "path": str(payload.get("path", ""))[:500],
        },
    )
    return {"recorded": True}


def start_campaign_setup_operation(
    payload: dict,
    *,
    request_id: str | None = None,
) -> str:
    return _submit_managed_operation(
        "initialization_campaign_setup",
        generate_campaign_setup_stream,
        payload,
        request_id=request_id,
        metadata={"stage": "campaign_setup"},
    )


def start_initialization_operation(
    payload: dict,
    *,
    request_id: str | None = None,
) -> str:
    return _submit_managed_operation(
        "initialization_opening",
        initialization_stream,
        payload,
        request_id=request_id,
        metadata={"stage": "opening"},
    )


def get_operation(operation_id: str) -> dict | None:
    operation = get_campaign_runtime().operations.get(operation_id)
    return operation.as_dict() if operation is not None else None


def acknowledge_operation(operation_id: str) -> dict:
    return get_campaign_runtime().operations.acknowledge(operation_id).as_dict()


def cancel_operation(operation_id: str) -> dict:
    runtime = get_campaign_runtime()
    operation = runtime.operations.get(operation_id)
    if operation is None:
        raise ClientValidationError("Operation not found")
    policy = operation_policy(operation.operation_type)
    if not policy.player_visible or not policy.cancellable:
        raise ClientValidationError("This operation cannot be cancelled")
    return runtime.operations.cancel(operation_id).as_dict()


def list_operations(
    *,
    status: OperationStatus | None = None,
    operation_type: str | None = None,
    active_only: bool = False,
    recoverable_only: bool = False,
    limit: int | None = 100,
) -> list[dict]:
    return [
        operation.as_dict()
        for operation in get_campaign_runtime().operations.list(
            status=status,
            operation_type=operation_type,
            active_only=active_only,
            recoverable_only=recoverable_only,
            limit=limit,
        )
    ]


def get_maintenance_job(job_id: str) -> dict | None:
    job = get_campaign_runtime().jobs.get(job_id)
    return job.as_dict() if job is not None else None


def list_maintenance_jobs(
    *,
    status: JobStatus | None = None,
    job_type: str | None = None,
    limit: int = 100,
) -> list[dict]:
    return [
        job.as_dict()
        for job in get_campaign_runtime().jobs.list(
            status=status,
            job_type=job_type,
            limit=limit,
        )
    ]


def undo_last_turn() -> dict:
    return get_campaign_runtime().undo()


def redo_last_turn() -> dict:
    return get_campaign_runtime().redo()


def discard_pending_turn(turn_id: str) -> dict:
    return get_campaign_runtime().discard_pending_turn(turn_id)


__all__ = [
    "acknowledge_operation",
    "cancel_operation",
    "create_save",
    "copy_save",
    "delete_save",
    "export_save_archive",
    "discard_pending_turn",
    "get_operation",
    "get_maintenance_job",
    "import_save_archive",
    "load_debug_state",
    "load_player_history",
    "load_player_records",
    "load_player_state",
    "load_initialization_draft",
    "load_reader_preferences",
    "activate_model_config",
    "delete_model_config",
    "list_model_configs",
    "model_config_status",
    "model_presets",
    "list_maintenance_jobs",
    "list_operations",
    "list_saves",
    "rename_save",
    "preview_initialization_config",
    "redo_last_turn",
    "save_dice_display",
    "save_canon_validation",
    "save_difficulty",
    "save_experience_mode",
    "load_player_events",
    "load_core_memory_items",
    "save_core_memory_items",
    "player_records_view",
    "save_initialization_draft",
    "save_max_chars",
    "save_model_config",
    "save_pending_scene_edit",
    "record_player_diagnostic",
    "save_reader_preferences",
    "start_confirmation_operation",
    "start_generation_operation",
    "start_feedback_rewrite_operation",
    "start_initialization_operation",
    "start_opening_rewrite_operation",
    "start_rephrase_operation",
    "start_reroll_operation",
    "start_campaign_setup_operation",
    "switch_save",
    "test_model_connection",
    "undo_last_turn",
    "update_player_record",
]
