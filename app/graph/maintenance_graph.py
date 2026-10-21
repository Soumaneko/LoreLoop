from __future__ import annotations

import traceback

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.runtime.session import get_campaign_runtime

from .maintenance_helpers import prepare_scene_summary_state
from .maintenance_nodes import scene_summary_condense_node
from .maintenance_state import SceneSummaryMaintenanceState


def build_scene_summary_maintenance_graph():
    dependencies = (
        scene_summary_condense_node,
    )
    return get_cached_graph(
        "maintenance_scene_summary",
        dependencies,
        _compile_scene_summary_maintenance_graph,
    )


def _compile_scene_summary_maintenance_graph():
    graph = StateGraph(SceneSummaryMaintenanceState)
    graph.add_node("scene_summary_condense", scene_summary_condense_node)

    graph.add_edge(START, "scene_summary_condense")
    graph.add_edge("scene_summary_condense", END)
    return graph.compile()


def run_scene_summary_maintenance(
    trace_id,
    turn_index,
    *,
    core_memory,
) -> dict:
    state = None
    try:
        state = prepare_scene_summary_state(
            trace_id,
            turn_index,
            core_memory=core_memory,
            threshold=10,
        )
        graph_state = build_scene_summary_maintenance_graph().invoke(state)
        result = graph_state["result"]
        print(
            "[debug-server] scene summary maintenance "
            f"condensed={len(result.condensed_ids)}"
        )
        return {"warning_codes": [code for event in graph_state.get("events", [])
                                  for code in event.get("data", {}).get("warning_codes", [])]}
    except Exception:
        traceback.print_exc()
        raise
    finally:
        if state is not None:
            state["runtime_services"].stores.close()
