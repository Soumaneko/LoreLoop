from __future__ import annotations

from typing import Any
import uuid

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.domain.roll.schemas import Difficulty
from app.domain.short_term_memory import ShortTermMemory
from app.domain.narrative_rewrite import RewriteStrategySelector
from app.runtime.session import get_campaign_runtime

from .generation_helpers import (
    finalize_pending_turn,
    gateway_for,
    prepare_generation_state,
)
from .generation_events import GenerationEventPresenter
from .generation_nodes import (
    check_narrative_consistency_node,
    compose_narrative_node,
    consume_director_events_node,
    evaluate_roll_modifiers_node,
    extract_context_facts_node,
    maybe_director_npc_node,
    parse_action_node,
    rephrase_narrative_node,
    recompose_from_feedback_node,
    rewrite_from_source_node,
    resolve_turn_node,
    generate_canon_queries_node,
    retrieve_canon_evidence_node,
    revise_narrative_node,
)
from .generation_state import GenerationEvent, GenerationState


def build_generation_graph():
    dependencies = (
        extract_context_facts_node,
        consume_director_events_node,
        parse_action_node,
        evaluate_roll_modifiers_node,
        resolve_turn_node,
        maybe_director_npc_node,
        compose_narrative_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        "generation",
        dependencies,
        _compile_generation_graph,
    )


def _compile_generation_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("extract_context_facts", extract_context_facts_node)
    graph.add_node("consume_director_events", consume_director_events_node)
    graph.add_node("parse_action", parse_action_node)
    graph.add_node("evaluate_roll_modifiers", evaluate_roll_modifiers_node)
    graph.add_node("resolve_turn", resolve_turn_node)
    graph.add_node("maybe_director_npc", maybe_director_npc_node)
    graph.add_node("compose_narrative", compose_narrative_node)
    graph.add_node("generate_canon_queries", generate_canon_queries_node)
    graph.add_node("retrieve_canon_evidence", retrieve_canon_evidence_node)
    graph.add_node("check_narrative_consistency", check_narrative_consistency_node)
    graph.add_node("revise_narrative", revise_narrative_node)

    graph.add_edge(START, "extract_context_facts")
    graph.add_edge(START, "parse_action")
    graph.add_edge("parse_action", "evaluate_roll_modifiers")
    graph.add_edge(["extract_context_facts", "evaluate_roll_modifiers"], "resolve_turn")
    graph.add_edge("resolve_turn", "consume_director_events")
    graph.add_edge("resolve_turn", "maybe_director_npc")
    graph.add_edge(
        ["maybe_director_npc", "consume_director_events"],
        "compose_narrative",
    )
    graph.add_edge("compose_narrative", "generate_canon_queries")
    graph.add_edge("generate_canon_queries", "retrieve_canon_evidence")
    graph.add_edge("retrieve_canon_evidence", "check_narrative_consistency")
    graph.add_conditional_edges(
        "check_narrative_consistency",
        _route_after_consistency_check,
        {
            "pass": END,
            "revise": "revise_narrative",
        },
    )
    graph.add_edge("revise_narrative", END)

    return graph.compile()


def build_rephrase_graph():
    dependencies = (
        rephrase_narrative_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        "generation_rephrase",
        dependencies,
        _compile_rephrase_graph,
    )


def _compile_rephrase_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("rephrase_narrative", rephrase_narrative_node)
    graph.add_node("generate_canon_queries", generate_canon_queries_node)
    graph.add_node("retrieve_canon_evidence", retrieve_canon_evidence_node)
    graph.add_node("check_narrative_consistency", check_narrative_consistency_node)
    graph.add_node("revise_narrative", revise_narrative_node)

    graph.add_edge(START, "rephrase_narrative")
    graph.add_edge("rephrase_narrative", "generate_canon_queries")
    graph.add_edge("generate_canon_queries", "retrieve_canon_evidence")
    graph.add_edge("retrieve_canon_evidence", "check_narrative_consistency")
    graph.add_conditional_edges(
        "check_narrative_consistency",
        _route_after_consistency_check,
        {
            "pass": END,
            "revise": "revise_narrative",
        },
    )
    graph.add_edge("revise_narrative", END)
    return graph.compile()


def _route_after_consistency_check(state: GenerationState) -> str:
    review = state["narrative_consistency_review"]
    return "revise" if review.decision == "revise" else "pass"


def _route_feedback_rewrite(state: GenerationState) -> str:
    return state["rewrite_strategy_decision"].strategy


def build_feedback_rewrite_graph():
    dependencies = (
        rewrite_from_source_node,
        recompose_from_feedback_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_feedback_rewrite,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        "generation_feedback_rewrite",
        dependencies,
        _compile_feedback_rewrite_graph,
    )


def _compile_feedback_rewrite_graph():
    graph = StateGraph(GenerationState)
    graph.add_node("rewrite_from_source", rewrite_from_source_node)
    graph.add_node("recompose", recompose_from_feedback_node)
    graph.add_node("generate_canon_queries", generate_canon_queries_node)
    graph.add_node("retrieve_canon_evidence", retrieve_canon_evidence_node)
    graph.add_node("check_narrative_consistency", check_narrative_consistency_node)
    graph.add_node("revise_narrative", revise_narrative_node)
    graph.add_conditional_edges(
        START,
        _route_feedback_rewrite,
        {
            "revise_from_source": "rewrite_from_source",
            "recompose": "recompose",
            "no_rewrite": END,
        },
    )
    graph.add_edge("rewrite_from_source", "generate_canon_queries")
    graph.add_edge("recompose", "generate_canon_queries")
    graph.add_edge("generate_canon_queries", "retrieve_canon_evidence")
    graph.add_edge("retrieve_canon_evidence", "check_narrative_consistency")
    graph.add_conditional_edges(
        "check_narrative_consistency",
        _route_after_consistency_check,
        {"pass": END, "revise": "revise_narrative"},
    )
    graph.add_edge("revise_narrative", END)
    return graph.compile()


def generate_turn_stream(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
):
    with get_campaign_runtime().confirmation_lock:
        yield from _generate_turn_stream(user_input, difficulty, max_chars)


def reroll_turn_stream(turn_id: str):
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        pending, version_index = _retry_source(runtime, turn_id)
        yield {
            "step": "reroll_started",
            "data": {
                "turn_id": turn_id,
                "group_id": pending.group_id,
                "version_index": version_index,
            },
        }
        yield from _generate_turn_stream(
            pending.user_input,
            pending.difficulty,
            pending.max_chars,
            group_id=pending.group_id,
            version_index=version_index,
        )


def rephrase_turn_stream(turn_id: str):
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        pending, version_index = _retry_source(runtime, turn_id)
        trace_id = str(uuid.uuid4())
        state = _pending_context_state(
            runtime,
            pending,
            trace_id=trace_id,
            events=[{
                "step": "rephrase_started",
                "data": {
                    "turn_id": turn_id,
                    "group_id": pending.group_id,
                    "version_index": version_index,
                    "trace_id": trace_id,
                },
            }],
        )
        yield from _stream_generation_graph(
            state,
            build_rephrase_graph(),
            group_id=pending.group_id,
            version_index=version_index,
        )


def feedback_rewrite_stream(
    turn_id: str,
    *,
    source_scene_text: str,
    user_feedback: str,
):
    source_scene_text = source_scene_text.strip()
    user_feedback = user_feedback.strip()
    if not source_scene_text:
        raise ValueError("source_scene_text is required")
    if not user_feedback:
        raise ValueError("user_feedback is required")
    if len(user_feedback) > 2000:
        raise ValueError("user_feedback must not exceed 2000 characters")

    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        pending, version_index = _retry_source(runtime, turn_id)
        trace_id = str(uuid.uuid4())
        state = _pending_context_state(
            runtime,
            pending,
            trace_id=trace_id,
            events=[{
                "step": "feedback_rewrite_started",
                "data": {
                    "turn_id": turn_id,
                    "group_id": pending.group_id,
                    "version_index": version_index,
                    "trace_id": trace_id,
                },
            }],
        )
        state.update({
            "rewrite_feedback": user_feedback,
            "rewrite_source_scene_text": source_scene_text,
        })
        decision = RewriteStrategySelector(gateway_for(state)).select(
            context=pending.context,
            source_scene_text=source_scene_text,
            user_feedback=user_feedback,
            recent_scene=state.get("recent_scene"),
            turn_index=pending.turn_index,
            trace_id=trace_id,
        )
        state["rewrite_strategy_decision"] = decision
        state["events"].append({
            "step": "feedback_rewrite_strategy_selected",
            "data": {
                "trace_id": trace_id,
                "strategy": decision.strategy,
                "reason_code": decision.reason_code,
            },
        })
        if decision.strategy == "no_rewrite":
            yield from state["events"]
            return
        yield from _stream_generation_graph(
            state,
            build_feedback_rewrite_graph(),
            group_id=pending.group_id,
            version_index=version_index,
        )


def _pending_context_state(
    runtime,
    pending,
    *,
    trace_id: str,
    events: list[GenerationEvent],
) -> GenerationState:
    short_term_memory = pending.short_term_memory or ShortTermMemory()
    recent_scene = (
        short_term_memory.recent_scenes[-1]
        if short_term_memory.recent_scenes
        else None
    )
    return {
        "events": events,
        "runtime_services": runtime.services,
        "runtime": runtime,
        "user_input": pending.user_input,
        "difficulty": pending.difficulty,
        "max_chars": pending.max_chars,
        "trace_id": trace_id,
        "current_state": pending.state_before,
        "short_term_memory": short_term_memory,
        "turn_index": pending.turn_index,
        "next_turn_index": pending.turn_index,
        "context": pending.context,
        "turn_resolution": pending.context.turn_resolution,
        "player_resolution": pending.player_resolution,
        "director_npc_result": pending.director_npc_result,
        "recent_scene": recent_scene,
    }


def _retry_source(runtime, turn_id: str):
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before retrying.")

    with runtime.services.stores.current_state() as state_store:
        current_state = state_store.load_or_default()
    if current_state != pending.state_before:
        raise ValueError(
            "Pending turn is stale because CurrentState changed after generation. "
            "Generate a new turn from the current state."
        )

    version_index = max(
        variant.version_index
        for variant in runtime.pending_turns.variants(pending.group_id)
    ) + 1
    return pending, version_index


def _generate_turn_stream(
    user_input: str,
    difficulty: Difficulty,
    max_chars: int,
    *,
    group_id: str | None = None,
    version_index: int = 1,
):
    state: dict[str, Any] = prepare_generation_state(user_input, difficulty, max_chars)
    yield from _stream_generation_graph(
        state,
        build_generation_graph(),
        group_id=group_id,
        version_index=version_index,
    )


def _stream_generation_graph(
    state: GenerationState,
    graph,
    *,
    group_id: str | None,
    version_index: int,
):
    event_count = 0
    for event in state.get("events", []):
        event_count += 1
        yield event

    event_presenter = GenerationEventPresenter()

    def append_and_yield(event: GenerationEvent):
        nonlocal event_count
        state["events"] = [*state.get("events", []), event]
        event_count += 1
        yield event

    for stream_mode, update in graph.stream(
        state,
        stream_mode=["updates", "custom"],
    ):
        if stream_mode == "custom":
            yield update
            continue
        for node_name, node_update in update.items():
            events = node_update.get("events", [])
            state.update({
                key: value
                for key, value in node_update.items()
                if key != "events"
            })

            for event in event_presenter.present(node_name, events):
                yield from append_and_yield(event)

    for event in event_presenter.finish():
        yield from append_and_yield(event)

    state = finalize_pending_turn(
        state,
        group_id=group_id,
        version_index=version_index,
    )
    for event in state.get("events", [])[event_count:]:
        event_count += 1
        yield event
