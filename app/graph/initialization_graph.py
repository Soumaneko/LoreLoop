from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph

from app.runtime.graph_cache import get_cached_graph
from app.runtime.session import get_campaign_runtime

from .initialization_helpers import (
    finalize_initialization_state,
    initialization_result,
    prepare_initialization_state,
)
from .initialization_nodes import opening_foundation_node, opening_scene_node
from .initialization_state import InitializationState


def build_initialization_graph():
    dependencies = (
        opening_foundation_node,
        opening_scene_node,
    )
    return get_cached_graph(
        "initialization:v2",
        dependencies,
        _compile_initialization_graph,
    )


def _compile_initialization_graph():
    graph = StateGraph(InitializationState)
    graph.add_node("opening_foundation", opening_foundation_node)
    graph.add_node("opening_scene", opening_scene_node)
    graph.add_edge(START, "opening_foundation")
    graph.add_edge("opening_foundation", "opening_scene")
    graph.add_edge("opening_scene", END)
    return graph.compile()


def initialization_stream(payload: dict[str, Any]):
    with get_campaign_runtime().confirmation_lock:
        state = prepare_initialization_state(payload)
        yield {
            "step": "initialization_started",
            "data": {
                "fingerprint": state["initialization_record"].fingerprint,
                "trace_id": state["opening_trace_id"],
            },
        }
        event_count = len(state.get("events", []))
        for update in build_initialization_graph().stream(
            state,
            stream_mode="updates",
        ):
            for node_update in update.values():
                state.update({
                    key: value
                    for key, value in node_update.items()
                    if key != "events"
                })
                events = node_update.get("events", [])
                state["events"] = [*state.get("events", []), *events]
                for event in events:
                    event_count += 1
                    yield event

        state = finalize_initialization_state(state)
        for event in state.get("events", [])[event_count:]:
            yield event
        return initialization_result(state)
