from __future__ import annotations

from typing import Any
import uuid

from langgraph.graph import END, START, StateGraph
from app.runtime.graph_cache import get_cached_graph
from app.runtime.settings import canon_validation_enabled, experience_mode

from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.domain.roll.schemas import Difficulty
from app.domain.short_term_memory import ShortTermMemory
from app.domain.narrative_rewrite import RewriteStrategySelector
from app.domain.narrative_rewrite.schemas import RewriteStrategyDecision
from app.runtime.serialization import model_data
from app.runtime.services import RuntimeServices
from app.runtime.session import get_campaign_runtime

from .generation_helpers import (
    finalize_pending_turn,
    gateway_for,
    hydrate_generation_prefix,
    prepare_generation_state,
    reset_generation_suffix,
)
from .generation_events import GenerationEventPresenter
from .fold import fold_state_keys
from .generation_nodes import (
    _diagnostics,
    check_narrative_consistency_node,
    compose_narrative_node,
    director_agent_stage_node,
    evaluate_roll_modifiers_node,
    extract_context_facts_node,
    parse_action_node,
    rephrase_narrative_node,
    recompose_from_feedback_node,
    rewrite_from_source_node,
    resolve_rolls_node,
    resolve_turn_node,
    generate_canon_queries_node,
    retrieve_canon_evidence_node,
    revise_narrative_node,
)


def _prefix_replay_events(state: GenerationState) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    if "action_parse_result" in state and state["action_parse_result"] is not None:
        events.append({
            "step": "action_parsed",
            "data": {
                "raw_action_parse_result": model_data(
                    state.get("raw_action_parse_result") or state["action_parse_result"]
                ),
                "action_self_check_result": model_data(state.get("action_self_check_result")),
                "action_parse_result": model_data(state["action_parse_result"]),
                "touched_event_ids": list(state["action_parse_result"].touched_event_ids),
            },
        })
    if "context_facts" in state and state["context_facts"] is not None:
        events.append({
            "step": "context_facts_done",
            "data": {
                "context_facts": model_data(state["context_facts"]),
                "context_search_plan": model_data(state.get("context_search_plan")),
                "context_history_evidence": model_data(state.get("context_history_evidence")),
                "continuity_selection": model_data(state.get("continuity_selection")),
                "salvaged": False,
            },
        })
    return events
from .generation_state import GenerationEvent, GenerationState


def build_generation_graph(
    *,
    canon_validation_is_enabled: bool,
):
    dependencies = (
        extract_context_facts_node,
        director_agent_stage_node,
        parse_action_node,
        evaluate_roll_modifiers_node,
        resolve_turn_node,
        compose_narrative_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        (
            "generation"
            f":canon={int(canon_validation_is_enabled)}"
        ),
        dependencies,
        lambda: _compile_generation_graph(
            canon_validation_is_enabled=canon_validation_is_enabled,
        ),
    )


def _compile_generation_graph(
    *,
    canon_validation_is_enabled: bool,
):
    graph = StateGraph(GenerationState)
    graph.add_node("extract_context_facts", extract_context_facts_node)
    graph.add_node("parse_action", parse_action_node)
    graph.add_node("evaluate_roll_modifiers", evaluate_roll_modifiers_node)
    graph.add_node("resolve_turn", resolve_turn_node)
    graph.add_node("compose_narrative", compose_narrative_node)

    graph.add_edge(START, "parse_action")
    graph.add_edge("parse_action", "extract_context_facts")
    graph.add_edge("parse_action", "evaluate_roll_modifiers")
    graph.add_edge(["extract_context_facts", "evaluate_roll_modifiers"], "resolve_turn")
    graph.add_node("director_agent_stage", director_agent_stage_node)
    graph.add_edge("resolve_turn", "director_agent_stage")
    graph.add_edge("director_agent_stage", "compose_narrative")

    _add_canon_validation_tail(
        graph,
        source_nodes=("compose_narrative",),
        enabled=canon_validation_is_enabled,
        compose_routing=True,
    )

    return graph.compile()


def build_rephrase_graph(*, canon_validation_is_enabled: bool):
    dependencies = (
        rephrase_narrative_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        f"generation_rephrase:canon={int(canon_validation_is_enabled)}",
        dependencies,
        lambda: _compile_rephrase_graph(
            canon_validation_is_enabled=canon_validation_is_enabled,
        ),
    )


def _compile_rephrase_graph(*, canon_validation_is_enabled: bool):
    graph = StateGraph(GenerationState)
    graph.add_node("rephrase_narrative", rephrase_narrative_node)

    graph.add_edge(START, "rephrase_narrative")
    _add_canon_validation_tail(
        graph,
        source_nodes=("rephrase_narrative",),
        enabled=canon_validation_is_enabled,
        compose_routing=True,
    )
    return graph.compile()


def build_reroll_graph(*, canon_validation_is_enabled: bool):
    dependencies = (
        resolve_rolls_node,
        resolve_turn_node,
        director_agent_stage_node,
        compose_narrative_node,
        generate_canon_queries_node,
        retrieve_canon_evidence_node,
        check_narrative_consistency_node,
        revise_narrative_node,
        _route_after_consistency_check,
    )
    return get_cached_graph(
        f"generation_reroll:canon={int(canon_validation_is_enabled)}",
        dependencies,
        lambda: _compile_reroll_graph(
            canon_validation_is_enabled=canon_validation_is_enabled,
        ),
    )


def _compile_reroll_graph(*, canon_validation_is_enabled: bool):
    graph = StateGraph(GenerationState)
    graph.add_node("resolve_rolls", resolve_rolls_node)
    graph.add_node("resolve_turn", resolve_turn_node)
    graph.add_node("director_agent_stage", director_agent_stage_node)
    graph.add_node("compose_narrative", compose_narrative_node)

    graph.add_edge(START, "resolve_rolls")
    graph.add_edge("resolve_rolls", "resolve_turn")
    graph.add_edge("resolve_turn", "director_agent_stage")
    graph.add_edge("director_agent_stage", "compose_narrative")

    _add_canon_validation_tail(
        graph,
        source_nodes=("compose_narrative",),
        enabled=canon_validation_is_enabled,
        compose_routing=True,
    )
    return graph.compile()


def _route_after_compose(state: GenerationState) -> str:
    return "empty" if state.get("composer_empty") else "canon"


def _route_after_consistency_check(state: GenerationState) -> str:
    review = state["narrative_consistency_review"]
    return "revise" if review.decision == "revise" else "pass"


def _route_feedback_rewrite(state: GenerationState) -> str:
    return state["rewrite_strategy_decision"].strategy


def build_feedback_rewrite_graph(*, canon_validation_is_enabled: bool):
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
        f"generation_feedback_rewrite:canon={int(canon_validation_is_enabled)}",
        dependencies,
        lambda: _compile_feedback_rewrite_graph(
            canon_validation_is_enabled=canon_validation_is_enabled,
        ),
    )


def _compile_feedback_rewrite_graph(*, canon_validation_is_enabled: bool):
    graph = StateGraph(GenerationState)
    graph.add_node("rewrite_from_source", rewrite_from_source_node)
    graph.add_node("recompose", recompose_from_feedback_node)
    graph.add_conditional_edges(
        START,
        _route_feedback_rewrite,
        {
            "revise_from_source": "rewrite_from_source",
            "recompose": "recompose",
            "no_rewrite": END,
        },
    )
    _add_canon_validation_tail(
        graph,
        source_nodes=("rewrite_from_source", "recompose"),
        enabled=canon_validation_is_enabled,
    )
    return graph.compile()


def _add_canon_validation_tail(
    graph: StateGraph,
    *,
    source_nodes: tuple[str, ...],
    enabled: bool,
    compose_routing: bool = False,
) -> None:
    if not enabled:
        for source_node in source_nodes:
            if compose_routing:
                graph.add_conditional_edges(
                    source_node,
                    _route_after_compose,
                    {"empty": END, "canon": END},
                )
            else:
                graph.add_edge(source_node, END)
        return

    graph.add_node("generate_canon_queries", generate_canon_queries_node)
    graph.add_node("retrieve_canon_evidence", retrieve_canon_evidence_node)
    graph.add_node("check_narrative_consistency", check_narrative_consistency_node)
    graph.add_node("revise_narrative", revise_narrative_node)
    for source_node in source_nodes:
        if compose_routing:
            graph.add_conditional_edges(
                source_node,
                _route_after_compose,
                {"empty": END, "canon": "generate_canon_queries"},
            )
        else:
            graph.add_edge(source_node, "generate_canon_queries")
    graph.add_edge("generate_canon_queries", "retrieve_canon_evidence")
    graph.add_edge("retrieve_canon_evidence", "check_narrative_consistency")
    graph.add_conditional_edges(
        "check_narrative_consistency",
        _route_after_consistency_check,
        {"pass": END, "revise": "revise_narrative"},
    )
    graph.add_edge("revise_narrative", END)


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
        trace_id = str(uuid.uuid4())
        yield {
            "step": "reroll_started",
            "data": {
                "turn_id": turn_id,
                "group_id": pending.group_id,
                "version_index": version_index,
                "trace_id": trace_id,
            },
        }
        prefix_snapshot = pending.prefix_snapshot
        if prefix_snapshot is not None and not prefix_snapshot.has_fallback:
            state = prepare_generation_state(
                pending.user_input,
                pending.difficulty,
                pending.max_chars,
            )
            state["trace_id"] = trace_id
            state["experience_mode"] = experience_mode()
            hydrate_generation_prefix(state, prefix_snapshot)
            reset_generation_suffix(state)
            state.setdefault("events", []).extend(_prefix_replay_events(state))
            yield from _stream_generation_graph(
                state,
                build_reroll_graph(
                    canon_validation_is_enabled=canon_validation_enabled(),
                ),
                group_id=pending.group_id,
                version_index=version_index,
            )
        else:
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
            build_rephrase_graph(
                canon_validation_is_enabled=canon_validation_enabled(),
            ),
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
        if len(source_scene_text) > pending.max_chars:
            raise ValueError(
                "source_scene_text exceeds pending max_chars"
            )
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
        yield state["events"][0]
        state.update({
            "rewrite_feedback": user_feedback,
            "rewrite_source_scene_text": source_scene_text,
        })
        try:
            decision = RewriteStrategySelector(gateway_for(state)).select(
                context=pending.context,
                source_scene_text=source_scene_text,
                user_feedback=user_feedback,
                recent_scene=state.get("recent_scene"),
                turn_index=pending.turn_index,
                trace_id=trace_id,
            )
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            _diagnostics(state).warning(
                source="rewrite_strategy",
                code="rewrite_strategy_fallback",
                message=(
                    "Rewrite strategy selection failed; conservatively "
                    f"keeping the original scene: {exc}"
                ),
                trace_id=trace_id,
                task="narrative_rewrite_select",
                turn_index=pending.turn_index,
                details={"strategy_unavailable": True},
            )
            decision = RewriteStrategyDecision(
                strategy="no_rewrite",
                reason_code="strategy_unavailable",
                reasoning="重试策略选择失败，保守不重写。",
                rewrite_requirements=[],
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
            yield from state["events"][1:]
            return
        yield from _stream_generation_graph(
            state,
            build_feedback_rewrite_graph(
                canon_validation_is_enabled=canon_validation_enabled(),
            ),
            group_id=pending.group_id,
            version_index=version_index,
            emitted_event_count=1,
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
    # A derived rewrite (rephrase/feedback rewrite) refines an existing variant,
    # so it inherits that variant's experience_mode and Director materials. Mode
    # switching only affects brand-new generations (new turn, reroll), which run
    # the full generation graph including the Director stage.
    uow = runtime.uow()
    return {
        "events": events,
        "runtime_services": RuntimeServices(
            stores=uow,
            gateway_factory=runtime.services.gateway_factory,
            jobs=runtime.services.jobs,
            diagnostics=runtime.services.diagnostics,
        ),
        "runtime": runtime,
        "user_input": pending.user_input,
        "difficulty": pending.difficulty,
        "max_chars": pending.max_chars,
        "experience_mode": pending.experience_mode,
        "trace_id": trace_id,
        "current_state": pending.state_before,
        "short_term_memory": short_term_memory,
        "turn_index": pending.turn_index,
        "next_turn_index": pending.turn_index,
        "context": pending.context,
        "turn_resolution": pending.context.turn_resolution,
        "player_resolution": pending.player_resolution,
        "director_stage": pending.director_stage,
        "director_log_entry": getattr(pending, "director_log_entry", None),
        "recent_scene": recent_scene,
    }


def _retry_source(runtime, turn_id: str):
    pending = runtime.pending_turns.get(turn_id)
    if pending is None:
        raise ValueError("Pending turn not found. Generate a scene before retrying.")

    # Keep reading through the configured services so tests that inject a fake
    # current_state store continue to work. In production runtime.services.stores
    # is the session-level projection; the operation-level UoW is created by the
    # generation state preparation that follows.
    with runtime.services.stores.current_state as state_store:
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
    runtime = get_campaign_runtime()
    mode = experience_mode()
    cached_snapshot = runtime.generation_prefix_cache.get(
        runtime.generation_cache_epoch,
        user_input,
    )
    if cached_snapshot is not None and not cached_snapshot.has_fallback:
        state: dict[str, Any] = prepare_generation_state(
            user_input,
            difficulty,
            max_chars,
        )
        state["experience_mode"] = mode
        hydrate_generation_prefix(state, cached_snapshot)
        reset_generation_suffix(state)
        state.setdefault("events", []).extend(_prefix_replay_events(state))
        yield from _stream_generation_graph(
            state,
            build_reroll_graph(
                canon_validation_is_enabled=canon_validation_enabled(),
            ),
            group_id=group_id,
            version_index=version_index,
        )
    else:
        state: dict[str, Any] = prepare_generation_state(
            user_input,
            difficulty,
            max_chars,
        )
        state["experience_mode"] = mode
        yield from _stream_generation_graph(
            state,
            build_generation_graph(
                canon_validation_is_enabled=canon_validation_enabled(),
            ),
            group_id=group_id,
            version_index=version_index,
        )


def _stream_generation_graph(
    state: GenerationState,
    graph,
    *,
    group_id: str | None,
    version_index: int,
    emitted_event_count: int = 0,
):
    event_count = emitted_event_count
    try:
        for event in state.get("events", [])[emitted_event_count:]:
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
                fold_state_keys(state, node_update)
                for event in event_presenter.present(
                    node_name,
                    node_update.get("events", []),
                ):
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
    finally:
        runtime_services = state.get("runtime_services")
        if runtime_services is not None:
            stores = getattr(runtime_services, "stores", None)
            if stores is not None and getattr(stores, "close", None) is not None:
                stores.close()
