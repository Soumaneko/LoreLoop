from __future__ import annotations

import traceback
from concurrent.futures import Future

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.runtime.serialization import model_data
from app.runtime.session import get_campaign_runtime
from app.runtime.stores import pending_memory_store

from .maintenance_helpers import (
    memory_batch_route,
    prepare_manual_batch_state,
    prepare_manual_memory_maintenance_state,
    prepare_memory_batch_state,
    prepare_scene_summary_state,
    scene_summary_route,
)
from .maintenance_nodes import (
    director_npc_restock_node,
    memory_batch_classify_node,
    memory_maintain_node,
    scene_summarize_node,
    scene_summary_condense_node,
)
from .maintenance_state import (
    DirectorNPCRestockState,
    MemoryMaintenanceState,
    SceneSummaryMaintenanceState,
)


def build_scene_summary_maintenance_graph():
    dependencies = (
        scene_summarize_node,
        scene_summary_condense_node,
        scene_summary_route,
    )
    return get_cached_graph(
        "maintenance_scene_summary",
        dependencies,
        _compile_scene_summary_maintenance_graph,
    )


def _compile_scene_summary_maintenance_graph():
    graph = StateGraph(SceneSummaryMaintenanceState)
    graph.add_node("scene_summarize", scene_summarize_node)
    graph.add_node("scene_summary_condense", scene_summary_condense_node)

    graph.add_edge(START, "scene_summarize")
    graph.add_conditional_edges(
        "scene_summarize",
        scene_summary_route,
        {
            "scene_summary_condense": "scene_summary_condense",
            "done": END,
        },
    )
    graph.add_edge("scene_summary_condense", END)
    return graph.compile()


def build_memory_batch_maintenance_graph():
    dependencies = (
        memory_batch_classify_node,
        memory_maintain_node,
        memory_batch_route,
    )
    return get_cached_graph(
        "maintenance_memory_batch",
        dependencies,
        _compile_memory_batch_maintenance_graph,
    )


def _compile_memory_batch_maintenance_graph():
    graph = StateGraph(MemoryMaintenanceState)
    graph.add_node("memory_batch_classify", memory_batch_classify_node)
    graph.add_node("memory_maintain", memory_maintain_node)

    graph.add_edge(START, "memory_batch_classify")
    graph.add_conditional_edges(
        "memory_batch_classify",
        memory_batch_route,
        {
            "memory_maintain": "memory_maintain",
            "done": END,
        },
    )
    graph.add_edge("memory_maintain", END)
    return graph.compile()


def build_memory_maintenance_graph():
    return get_cached_graph(
        "maintenance_memory",
        (memory_maintain_node,),
        _compile_memory_maintenance_graph,
    )


def _compile_memory_maintenance_graph():
    graph = StateGraph(MemoryMaintenanceState)
    graph.add_node("memory_maintain", memory_maintain_node)
    graph.add_edge(START, "memory_maintain")
    graph.add_edge("memory_maintain", END)
    return graph.compile()


def build_director_npc_restock_graph():
    return get_cached_graph(
        "maintenance_director_npc_restock",
        (director_npc_restock_node,),
        _compile_director_npc_restock_graph,
    )


def _compile_director_npc_restock_graph():
    graph = StateGraph(DirectorNPCRestockState)
    graph.add_node("director_npc_restock", director_npc_restock_node)
    graph.add_edge(START, "director_npc_restock")
    graph.add_edge("director_npc_restock", END)
    return graph.compile()


def run_director_npc_restock(
    trace_id,
    turn_index,
    *,
    core_memory,
    protagonist_name,
) -> None:
    try:
        state: DirectorNPCRestockState = {
            "events": [],
            "runtime_services": get_campaign_runtime().services,
            "trace_id": trace_id,
            "turn_index": turn_index,
            "core_memory": core_memory,
            "protagonist_name": protagonist_name,
        }
        result = build_director_npc_restock_graph().invoke(state)
        created_drafts = result.get("created_drafts", [])
        created_names = ", ".join(draft.name for draft in created_drafts) or "none"
        print(
            "[debug-server] director npc restock "
            f"created_count={len(created_drafts)} created={created_names}"
        )
    except Exception:
        print("[debug-server] director npc restock failed")
        traceback.print_exc()
        raise


def run_scene_summary_maintenance(
    scene_text,
    current_state,
    scene_id,
    turn_index,
    trace_id,
    *,
    core_memory,
) -> None:
    try:
        state = prepare_scene_summary_state(
            scene_text,
            current_state,
            scene_id,
            turn_index,
            trace_id,
            core_memory=core_memory,
            threshold=10,
        )
        graph_state = build_scene_summary_maintenance_graph().invoke(state)
        result = graph_state["result"]
        print(
            "[debug-server] scene summary maintenance "
            f"l1={result.saved_l1_id} condensed={len(result.condensed_ids)}"
        )
    except Exception:
        print("[debug-server] scene summary maintenance failed")
        traceback.print_exc()
        raise


def run_memory_batch_maintenance(
    current_state,
    trace_id,
    turn_index,
    *,
    core_memory,
    wait_for_summary: Future | None = None,
) -> None:
    try:
        if wait_for_summary is not None:
            wait_for_summary.result()
        state = prepare_memory_batch_state(
            current_state,
            trace_id,
            turn_index,
            core_memory=core_memory,
            turn_threshold=5,
        )
        graph_state = build_memory_batch_maintenance_graph().invoke(state)
        result = graph_state["batch_result"]
        maintenance_result = graph_state.get("maintenance_result")
        print(
            "[debug-server] memory batch maintenance "
            f"triggered={result.triggered} pending={result.pending_count} "
            f"saved={len(result.saved_ids)} classified={len(result.classified_pending_ids)}"
        )
        if maintenance_result is not None:
            print(
                "[debug-server] memory maintenance "
                f"summaries={maintenance_result.summary_count} "
                f"changed={maintenance_result.changed_count} "
                f"cursor={maintenance_result.cursor_after}"
            )
    except Exception:
        print("[debug-server] memory batch maintenance failed")
        traceback.print_exc()
        raise


def batch_classify_stream():
    state = prepare_manual_batch_state()
    pending_items = state.get("pending_items", [])
    yield {
        "step": "batch_classify_started",
        "data": {
            "trace_id": state["trace_id"],
            "pending_count": len(pending_items),
            "unclassified_turn_count": state.get("unclassified_turn_count", 0),
            "pending_total": state.get("pending_total", 0),
        },
    }
    if not pending_items:
        yield {
            "step": "batch_classify_done",
            "data": {"reason": "No pending memories need classification."},
        }
        return

    yield {"step": "batch_classifying", "data": {}}
    graph_state = build_memory_batch_maintenance_graph().invoke(state)
    result = graph_state["batch_result"]
    maintenance_result = graph_state.get("maintenance_result")
    yield {
        "step": "batch_classified",
        "data": {
            "triggered": result.triggered,
            "raw_candidates": model_data(result.raw_candidates),
            "final_memories": model_data(result.final_memories),
            "discarded_count": result.discarded_count,
            "self_check_result": model_data(result.self_check_result),
            "should_maintain": result.should_maintain,
            "entity_links_created": result.entity_links_created,
            "maintenance": model_data(maintenance_result),
        },
    }
    yield {
        "step": "batch_classify_done",
        "data": {
            "saved_ids": result.saved_ids,
            "classified_pending_ids": result.classified_pending_ids,
            "pending_count": result.pending_count,
            "entity_links_created": result.entity_links_created,
            "maintenance": model_data(maintenance_result),
        },
    }


def memory_maintain_stream():
    state, active_count, total_count = prepare_manual_memory_maintenance_state()
    yield {
        "step": "memory_maintain_started",
        "data": {
            "trace_id": state["trace_id"],
            "active_count": active_count,
            "total_count": total_count,
            "inactive_count": total_count - active_count,
        },
    }
    graph_state = build_memory_maintenance_graph().invoke(state)
    result = graph_state["maintenance_result"]
    yield {
        "step": "memory_maintain_done",
        "data": {
            "active_before": active_count,
            "summaries": result.summary_count,
            "changed": result.changed_count,
            "cursor_before": result.cursor_before,
            "cursor_after": result.cursor_after,
            "proposals": model_data(result.proposals),
        },
    }
