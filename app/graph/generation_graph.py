from __future__ import annotations

from typing import Any

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph

from app.domain.roll.schemas import Difficulty
from app.runtime.session import get_campaign_runtime

from .generation_helpers import (
    finalize_pending_turn,
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


def _route_after_consistency_check(state: GenerationState) -> str:
    review = state["narrative_consistency_review"]
    return "revise" if review.decision == "revise" else "pass"


def generate_turn_stream(
    user_input: str,
    difficulty: Difficulty = "困难",
    max_chars: int = 1500,
):
    with get_campaign_runtime().confirmation_lock:
        yield from _generate_turn_stream(user_input, difficulty, max_chars)


def retry_turn_stream(turn_id: str):
    lock = get_campaign_runtime().confirmation_lock
    with lock:
        pending = get_campaign_runtime().pending_turns.get(turn_id)
        if pending is None:
            raise ValueError("Pending turn not found. Generate a scene before retrying.")

        with get_campaign_runtime().services.stores.current_state() as state_store:
            current_state = state_store.load_or_default()
        if current_state != pending.state_before:
            raise ValueError(
                "Pending turn is stale because CurrentState changed after generation. "
                "Generate a new turn from the current state."
            )

        version_index = max(
            variant.version_index
            for variant in get_campaign_runtime().pending_turns.variants(pending.group_id)
        ) + 1
        yield {
            "step": "retry_started",
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


def _generate_turn_stream(
    user_input: str,
    difficulty: Difficulty,
    max_chars: int,
    *,
    group_id: str | None = None,
    version_index: int = 1,
):
    state: dict[str, Any] = prepare_generation_state(user_input, difficulty, max_chars)
    event_count = 0
    for event in state.get("events", []):
        event_count += 1
        yield event

    graph = build_generation_graph()
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
