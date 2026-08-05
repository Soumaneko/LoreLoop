from __future__ import annotations

from concurrent.futures import CancelledError
from functools import wraps
from typing import Callable

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.runtime.session import (
    get_campaign_runtime,
)
from app.runtime.settings import director_enabled

from .confirmation_helpers import (
    finalize_confirmation_state,
    merge_confirmation_node_update,
    prepare_confirmation_second_wave,
    prepare_confirmation_state,
    reset_confirmation_cache_after_rollback,
)
from .confirmation_nodes import (
    action_preview_node,
    continuity_project_node,
    director_agent_confirm_node,
    director_event_review_node,
    npc_profile_extract_node,
    npc_profile_update_node,
    scene_fact_extract_node,
    scene_summarize_node,
    setting_appearance_update_node,
    state_update_node,
)
from .confirmation_state import ConfirmationPipelineInput, ConfirmationState


ConfirmationNode = Callable[[ConfirmationState], ConfirmationState]


def capture_confirmation_node_errors(
    node_name: str,
    node: ConfirmationNode,
) -> ConfirmationNode:
    @wraps(node)
    def wrapped(state: ConfirmationState) -> ConfirmationState:
        try:
            return node(state)
        except CancelledError:
            raise
        except Exception as exc:
            return {
                "node_errors": [{"node": node_name, "exception": exc}],
            }

    return wrapped


def build_confirmation_first_wave_graph():
    dependencies = (
        scene_fact_extract_node,
        state_update_node,
        scene_summarize_node,
        npc_profile_extract_node,
    )
    return get_cached_graph(
        "confirmation_first_wave",
        dependencies,
        _compile_confirmation_first_wave_graph,
    )


def _compile_confirmation_first_wave_graph():
    graph = StateGraph(ConfirmationState)
    graph.add_node(
        "scene_fact_extract",
        capture_confirmation_node_errors("scene_fact_extract", scene_fact_extract_node),
    )
    graph.add_node(
        "state_update",
        capture_confirmation_node_errors("state_update", state_update_node),
    )
    graph.add_node(
        "scene_summarize",
        capture_confirmation_node_errors("scene_summarize", scene_summarize_node),
    )
    graph.add_node(
        "npc_profile_extract",
        capture_confirmation_node_errors("npc_profile_extract", npc_profile_extract_node),
    )

    graph.add_edge(START, "scene_fact_extract")
    graph.add_edge("scene_fact_extract", "state_update")
    graph.add_edge("scene_fact_extract", "npc_profile_extract")
    graph.add_edge("state_update", "scene_summarize")
    graph.add_edge("scene_summarize", END)
    graph.add_edge("npc_profile_extract", END)

    return graph.compile()


def build_confirmation_second_wave_graph():
    dependencies = (
        action_preview_node,
        continuity_project_node,
        npc_profile_update_node,
        setting_appearance_update_node,
    )
    return get_cached_graph(
        "confirmation_second_wave",
        dependencies,
        _compile_confirmation_second_wave_graph,
    )


def _compile_confirmation_second_wave_graph():
    graph = StateGraph(ConfirmationState)
    graph.add_node(
        "action_preview",
        capture_confirmation_node_errors("action_preview", action_preview_node),
    )
    graph.add_node(
        "continuity_project",
        capture_confirmation_node_errors(
            "continuity_project",
            continuity_project_node,
        ),
    )
    graph.add_node(
        "npc_profile_update",
        capture_confirmation_node_errors(
            "npc_profile_update",
            npc_profile_update_node,
        ),
    )
    graph.add_node(
        "setting_appearance_update",
        capture_confirmation_node_errors(
            "setting_appearance_update",
            setting_appearance_update_node,
        ),
    )

    graph.add_edge(START, "action_preview")
    graph.add_edge(START, "continuity_project")
    graph.add_edge(START, "npc_profile_update")
    graph.add_edge(START, "setting_appearance_update")
    graph.add_edge("action_preview", END)
    graph.add_edge("continuity_project", END)
    graph.add_edge("npc_profile_update", END)
    graph.add_edge("setting_appearance_update", END)

    return graph.compile()


def build_confirmation_director_graph():
    return get_cached_graph(
        "confirmation_director_agent",
        (director_event_review_node, director_agent_confirm_node),
        _compile_confirmation_director_graph,
    )


def _compile_confirmation_director_graph():
    graph = StateGraph(ConfirmationState)
    graph.add_node(
        "director_event_review",
        capture_confirmation_node_errors(
            "director_event_review",
            director_event_review_node,
        ),
    )
    graph.add_node(
        "director_agent_confirm",
        capture_confirmation_node_errors(
            "director_agent_confirm",
            director_agent_confirm_node,
        ),
    )
    graph.add_edge(START, "director_event_review")
    graph.add_edge("director_event_review", "director_agent_confirm")
    graph.add_edge("director_agent_confirm", END)
    return graph.compile()


def stream_confirmation_graph(graph, state: ConfirmationState):
    for update in graph.stream(state, stream_mode="updates"):
        for node_update in update.values():
            yield from merge_confirmation_node_update(state, node_update)
    if state.get("node_errors"):
        raise state["node_errors"][0]["exception"]


def stream_confirmation_pipeline(
    pipeline_input: ConfirmationPipelineInput,
    *,
    write_db: bool = True,
    scene_text: str | None = None,
    journaled: bool = False,
):
    state: ConfirmationState = prepare_confirmation_state(
        pipeline_input,
        write_db=write_db,
        scene_text=scene_text,
        journaled=journaled,
    )
    event_count = 0
    for event in state.get("events", []):
        event_count += 1
        yield event

    first_wave_graph = build_confirmation_first_wave_graph()
    second_wave_graph = build_confirmation_second_wave_graph()
    for event in stream_confirmation_graph(first_wave_graph, state):
        event_count += 1
        yield event

    state = prepare_confirmation_second_wave(state)
    state["node_errors"] = []
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event

    for event in stream_confirmation_graph(second_wave_graph, state):
        event_count += 1
        yield event

    director_graph = build_confirmation_director_graph()
    for event in stream_confirmation_graph(director_graph, state):
        event_count += 1
        yield event

    state = finalize_confirmation_state(state)
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event


def confirm_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
):
    runtime = get_campaign_runtime()
    if runtime.pending_openings.get(turn_id) is not None:
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
    lock = get_campaign_runtime().confirmation_lock
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
    runtime = get_campaign_runtime()
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before confirming.")

    director_is_enabled = director_enabled()
    yield from _confirm_pipeline_locked(
        ConfirmationPipelineInput(
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
            run_director_agent=director_is_enabled,
        ),
        write_db=write_db,
        scene_text=scene_text,
    )


def _confirm_pipeline_locked(
    pipeline_input: ConfirmationPipelineInput,
    write_db: bool = True,
    scene_text: str | None = None,
):
    runtime = get_campaign_runtime()
    journaled = runtime.begin_confirmation(
        pipeline_input.pending,
        write_db=write_db,
        scene_text=scene_text,
    )
    try:
        yield from stream_confirmation_pipeline(
            pipeline_input,
            write_db=write_db,
            scene_text=scene_text,
            journaled=journaled,
        )
    except BaseException as exc:
        if journaled:
            runtime.jobs.wait_for_idle()
            try:
                runtime.history.fail_confirmation(pipeline_input.turn_id, exc)
            finally:
                reset_confirmation_cache_after_rollback(pipeline_input.pending)
        raise


def confirm_opening_stream(
    turn_id: str,
    write_db: bool = True,
    scene_text: str | None = None,
):
    lock = get_campaign_runtime().confirmation_lock
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
    runtime = get_campaign_runtime()
    pending = runtime.pending_openings.get(turn_id)
    if pending is None:
        raise ValueError("Pending opening not found. Initialize again before confirming.")

    director_is_enabled = getattr(pending.config, "director_enabled", True)
    initialization = runtime.initialization.load()
    if (
        initialization is None
        or initialization.phase != "opening_pending"
        or initialization.fingerprint != pending.initialization_fingerprint
        or initialization.foundation is None
    ):
        raise RuntimeError("Pending opening does not match initialization state")
    yield from _confirm_pipeline_locked(
        ConfirmationPipelineInput(
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
            run_director_agent=director_is_enabled,
            clear_short_term_before_save=True,
            clear_confirmed_history_before_save=True,
            clear_continuity_before_save=True,
            confirm_started_extra={"opening": True},
            finish_extra={"opening": True},
            complete_initialization=True,
        ),
        write_db=write_db,
        scene_text=scene_text,
    )
