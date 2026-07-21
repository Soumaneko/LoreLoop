"""Public application API used by the debug server and local tools."""

from app.graph.confirmation_graph import confirm_stream
from app.graph.generation_graph import generate_turn_stream, retry_turn_stream
from app.graph.initialization_graph import initialization_stream
from app.graph.initialization_helpers import (
    generate_story_direction_stream,
    preview_core_memory,
)
from app.graph.maintenance_graph import batch_classify_stream, memory_maintain_stream
from app.runtime.debug_state import load_debug_state
from app.runtime.player_state import load_player_history, load_player_state
from app.runtime.settings import save_difficulty, save_max_chars
from app.runtime.saves import create_save, list_saves, switch_save
from app.runtime.session import get_campaign_runtime
from app.runtime.jobs import JobStatus
from app.runtime.operations import OperationStatus


def start_generation_operation(
    user_input: str,
    *,
    difficulty: str,
    max_chars: int,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    return runtime.operations.submit_stream(
        "generation",
        generate_turn_stream,
        user_input,
        difficulty=difficulty,
        max_chars=max_chars,
        request_id=request_id,
        exclusive_key="generation",
        metadata={"user_input": user_input},
    )


def start_retry_operation(
    turn_id: str,
    *,
    request_id: str | None = None,
) -> str:
    runtime = get_campaign_runtime()
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before retrying.")
    return runtime.operations.submit_stream(
        "retry",
        retry_turn_stream,
        turn_id,
        request_id=request_id,
        exclusive_key="generation",
        metadata={
            "turn_id": turn_id,
            "group_id": pending.group_id,
            "user_input": pending.user_input,
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
        raise ValueError("Pending turn not found. Generate a scene before confirming.")
    return runtime.operations.submit_stream(
        "confirmation",
        confirm_stream,
        turn_id,
        write_db=write_db,
        scene_text=scene_text,
        request_id=request_id,
        exclusive_key="confirmation",
        metadata={
            "turn_id": turn_id,
            "kind": "turn" if pending is not None else "opening",
            "user_input": getattr(selected, "user_input", ""),
            "write_db": write_db,
        },
    )


def start_batch_classify_operation(*, request_id: str | None = None) -> str:
    return get_campaign_runtime().operations.submit_stream(
        "maintenance_batch_classify_manual",
        batch_classify_stream,
        request_id=request_id,
        exclusive_key="manual_maintenance",
        metadata={"manual": True},
    )


def start_memory_maintain_operation(*, request_id: str | None = None) -> str:
    return get_campaign_runtime().operations.submit_stream(
        "maintenance_memory_maintain_manual",
        memory_maintain_stream,
        request_id=request_id,
        exclusive_key="manual_maintenance",
        metadata={"manual": True},
    )


def start_story_direction_operation(
    payload: dict,
    *,
    request_id: str | None = None,
) -> str:
    return get_campaign_runtime().operations.submit_stream(
        "initialization_story_direction",
        generate_story_direction_stream,
        payload,
        request_id=request_id,
        exclusive_key="initialization",
        metadata={"stage": "story_direction"},
    )


def start_initialization_operation(
    payload: dict,
    *,
    request_id: str | None = None,
) -> str:
    return get_campaign_runtime().operations.submit_stream(
        "initialization_opening",
        initialization_stream,
        payload,
        request_id=request_id,
        exclusive_key="initialization",
        metadata={"stage": "opening"},
    )


def get_operation(operation_id: str) -> dict | None:
    operation = get_campaign_runtime().operations.get(operation_id)
    return operation.as_dict() if operation is not None else None


def list_operations(
    *,
    status: OperationStatus | None = None,
    operation_type: str | None = None,
    active_only: bool = False,
    limit: int = 100,
) -> list[dict]:
    return [
        operation.as_dict()
        for operation in get_campaign_runtime().operations.list(
            status=status,
            operation_type=operation_type,
            active_only=active_only,
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
    "create_save",
    "discard_pending_turn",
    "get_operation",
    "get_maintenance_job",
    "load_debug_state",
    "load_player_history",
    "load_player_state",
    "list_maintenance_jobs",
    "list_operations",
    "list_saves",
    "preview_core_memory",
    "redo_last_turn",
    "save_difficulty",
    "save_max_chars",
    "start_batch_classify_operation",
    "start_confirmation_operation",
    "start_generation_operation",
    "start_initialization_operation",
    "start_memory_maintain_operation",
    "start_retry_operation",
    "start_story_direction_operation",
    "switch_save",
    "undo_last_turn",
]
