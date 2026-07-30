from __future__ import annotations

import traceback

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.runtime.session import get_campaign_runtime

from .maintenance_helpers import (
    prepare_scene_summary_state,
)
from .maintenance_nodes import (
    director_npc_restock_node,
    scene_summary_condense_node,
    setting_seed_extract_node,
)
from .maintenance_state import (
    DirectorNPCMaintenanceState,
    SceneSummaryMaintenanceState,
)


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


def build_setting_seed_maintenance_graph():
    return get_cached_graph(
        "maintenance_setting_seed",
        (setting_seed_extract_node,),
        _compile_setting_seed_maintenance_graph,
    )


def _compile_setting_seed_maintenance_graph():
    graph = StateGraph(DirectorNPCMaintenanceState)
    graph.add_node("setting_seed_extract", setting_seed_extract_node)
    graph.add_edge(START, "setting_seed_extract")
    graph.add_edge("setting_seed_extract", END)
    return graph.compile()


def build_director_npc_maintenance_graph():
    return get_cached_graph(
        "maintenance_director_npc",
        (setting_seed_extract_node, director_npc_restock_node),
        _compile_director_npc_maintenance_graph,
    )


def _compile_director_npc_maintenance_graph():
    graph = StateGraph(DirectorNPCMaintenanceState)
    graph.add_node("setting_seed_extract", setting_seed_extract_node)
    graph.add_node("director_npc_restock", director_npc_restock_node)
    graph.add_edge(START, "setting_seed_extract")
    graph.add_edge("setting_seed_extract", "director_npc_restock")
    graph.add_edge("director_npc_restock", END)
    return graph.compile()


def run_setting_seed_maintenance(
    trace_id,
    turn_index,
    *,
    core_memory,
    scene_text,
    scene_facts_json,
    new_npc_profile_ids,
) -> None:
    try:
        state: DirectorNPCMaintenanceState = {
            "events": [],
            "runtime_services": get_campaign_runtime().services,
            "trace_id": trace_id,
            "turn_index": turn_index,
            "core_memory": core_memory,
            "scene_text": scene_text,
            "scene_facts_json": scene_facts_json,
            "new_npc_profile_ids": new_npc_profile_ids,
        }
        result = build_setting_seed_maintenance_graph().invoke(state)
        seed_result = result["setting_seed_result"]
        print(
            "[debug-server] setting seed maintenance "
            f"saved={len(seed_result.saved_setting_ids)} "
            f"candidates={len(seed_result.extraction.candidates)}"
        )
    except Exception:
        traceback.print_exc()
        raise


def run_director_npc_maintenance(
    trace_id,
    turn_index,
    *,
    core_memory,
    protagonist_name,
    scene_text,
    scene_facts_json,
    new_npc_profile_ids,
) -> None:
    try:
        state: DirectorNPCMaintenanceState = {
            "events": [],
            "runtime_services": get_campaign_runtime().services,
            "trace_id": trace_id,
            "turn_index": turn_index,
            "core_memory": core_memory,
            "protagonist_name": protagonist_name,
            "scene_text": scene_text,
            "scene_facts_json": scene_facts_json,
            "new_npc_profile_ids": new_npc_profile_ids,
        }
        result = build_director_npc_maintenance_graph().invoke(state)
        created_drafts = result.get("created_drafts", [])
        created_names = ", ".join(draft.name for draft in created_drafts) or "none"
        print(
            "[debug-server] director npc maintenance "
            f"created_count={len(created_drafts)} created={created_names}"
        )
    except Exception:
        traceback.print_exc()
        raise


def run_scene_summary_maintenance(
    trace_id,
    turn_index,
    *,
    core_memory,
) -> None:
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
    except Exception:
        traceback.print_exc()
        raise
