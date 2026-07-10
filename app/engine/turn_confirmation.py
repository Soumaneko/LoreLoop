from dataclasses import dataclass, field
from typing import Any

from app.graph.confirmation_graph import stream_confirmation_pipeline

from .session import (
    PENDING_OPENINGS,
    PENDING_TURNS,
    confirmation_lock,
)


@dataclass
class _ConfirmationPipelineInput:
    turn_id: str
    pending: Any
    state_before: Any
    scene_result: Any
    user_input: str
    turn_index: int
    stale_message: str
    pending_registry: dict[str, Any]
    update_director_event: bool = True
    handle_director_npc: bool = True
    director_npc_restock_sync: bool = False
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
):
    if turn_id in PENDING_OPENINGS:
        yield from confirm_opening_stream(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
        )
        return
    yield from confirm_turn_stream(
        turn_id,
        write_db=write_db,
        scene_text=scene_text,
    )


def confirm_turn_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
):
    lock = confirmation_lock(turn_id)
    with lock:
        yield from _confirm_turn_stream_locked(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
        )


def _confirm_turn_stream_locked(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
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
    )


def _confirm_pipeline_locked(
    pipeline_input: _ConfirmationPipelineInput,
    write_db: bool = True,
    scene_text: str | None = None,
):
    yield from stream_confirmation_pipeline(
        pipeline_input,
        write_db=write_db,
        scene_text=scene_text,
    )


def confirm_opening_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
):
    lock = confirmation_lock(turn_id)
    with lock:
        yield from _confirm_opening_stream_locked(
            turn_id,
            write_db=write_db,
            scene_text=scene_text,
        )


def _confirm_opening_stream_locked(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
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
            stale_message=(
                "Pending opening is stale because initialization state changed. "
                "Initialize again before confirming."
            ),
            pending_registry=PENDING_OPENINGS,
            update_director_event=False,
            handle_director_npc=True,
            director_npc_restock_sync=True,
            clear_short_term_before_save=True,
            dry_run_memory_reason="DB writes are disabled; opening scene was not saved.",
            confirm_started_extra={"opening": True},
            finish_extra={"opening": True},
        ),
        write_db=write_db,
        scene_text=scene_text,
    )
