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
from .initialization_nodes import opening_scene_node
from .initialization_state import InitializationState


def build_initialization_graph():
    dependencies = (
        opening_scene_node,
    )
    return get_cached_graph(
        "initialization",
        dependencies,
        _compile_initialization_graph,
    )


def _compile_initialization_graph():
    graph = StateGraph(InitializationState)
    graph.add_node("opening_scene", opening_scene_node)
    graph.add_edge(START, "opening_scene")
    graph.add_edge("opening_scene", END)
    return graph.compile()


def apply_core_memory_init(payload: dict[str, Any]) -> dict[str, Any]:
    with get_campaign_runtime().confirmation_lock:
        state = prepare_initialization_state(payload)
        graph_state = build_initialization_graph().invoke(state)
        finalized = finalize_initialization_state(graph_state)
        return initialization_result(finalized)
