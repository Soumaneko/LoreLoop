from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.diagnostics import DiagnosticLogger
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.graph import confirmation_nodes
from app.graph.confirmation_graph import (
    build_confirmation_first_wave_graph,
    build_confirmation_second_wave_graph,
    capture_confirmation_node_errors,
)
from app.graph import confirmation_graph
from app.graph.confirmation_helpers import merge_confirmation_node_update
from app.graph.confirmation_helpers import prepare_confirmation_second_wave
from app.graph.confirmation_helpers import reset_confirmation_cache_after_rollback
from app.graph.confirmation_state import ConfirmationState
from app.graph.confirmation_lifecycle import ConfirmationFinalizer


def test_confirmation_first_wave_preserves_parallel_extract_dependencies():
    graph = build_confirmation_first_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("scene_fact_extract", "state_update") in edges
    assert ("scene_fact_extract", "entity_extract") in edges
    assert ("scene_fact_extract", "npc_profile_extract") in edges
    assert ("state_update", "scene_summarize") in edges
    assert ("scene_summarize", "__end__") in edges


def test_state_update_warning_is_emitted_cached_and_diagnosed(
    monkeypatch,
    tmp_path,
):
    current_state = CurrentState(
        scene=SceneState(scene_id="scene-1", location="门外"),
        player=PlayerState(name="主角"),
    )
    warning = StateChangeWarning(
        operation_id="op2",
        operation_type="npc_condition_add",
        target="米莉",
        code="npc_target_not_present",
        message="target missing",
    )
    change_set = StateChangeSet(decision="NPC 已离开。")

    class _Updater:
        def __init__(self, _gateway):
            self.last_change_set = change_set
            self.last_warnings = [warning]

        def update(self, state, *_args, **_kwargs):
            return state

    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _Updater)
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: object())
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "pipeline_input": object(),
        "trace_id": "trace-1",
        "cache": {},
        "state_before_update": current_state,
        "scene_facts_json": "{}",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "events": [],
        "node_errors": [],
        "write_db": True,
        "runtime_services": SimpleNamespace(diagnostics=diagnostics),
    }

    update = confirmation_nodes.state_update_node(state)
    events = merge_confirmation_node_update(state, update)

    assert update["state_update_warnings"] == [warning]
    assert events[0]["data"]["warnings"][0]["code"] == "npc_target_not_present"
    assert state["cache"]["state_update_warnings"] == [warning]
    diagnostic = diagnostics.read(trace_id="trace-1")[0]
    assert diagnostic["source"] == "state_update"
    assert diagnostic["code"] == "npc_target_not_present"


def test_only_selection_summaries_append_current_turn_summary():
    historical = [
        SceneSummary(
            id=f"summary-{index}",
            level=1,
            content=f"摘要 {index}",
            turn_start=index,
            turn_end=index,
        )
        for index in range(1, 6)
    ]
    current = SceneSummary(
        id="summary-6",
        level=1,
        content="本轮摘要",
        turn_start=6,
        turn_end=6,
    )

    class _Store:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_active(self):
            return historical

        def get_recent(self, limit):
            assert limit == 5
            return historical

    state = {
        "cache": {},
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_006", location="测试地点"),
            player=PlayerState(name="主角"),
        ),
        "turn_summary": current,
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(scene_summary=_Store)
        ),
        "events": [],
    }

    prepared = prepare_confirmation_second_wave(state)

    assert prepared["recent_summaries"] == historical
    assert prepared["scene_summaries"] == historical
    assert prepared["selection_summaries"] == [*historical, current]


def test_scene_summarize_uses_updated_state_and_confirmation_text(monkeypatch):
    captured = {}
    summary = SceneSummary(
        id="summary-current",
        level=1,
        content="本轮摘要",
        scene_ids=["scene-current"],
        turn_start=6,
        turn_end=6,
    )

    class _Summarizer:
        def __init__(self, _gateway):
            pass

        def summarize_turn(self, **kwargs):
            captured.update(kwargs)
            return summary

    updated_state = CurrentState(
        scene=SceneState(scene_id="scene-current", location="新地点"),
        player=PlayerState(name="主角"),
    )
    monkeypatch.setattr(confirmation_nodes, "SceneSummarizer", _Summarizer)
    result = confirmation_nodes.scene_summarize_node({
        "pipeline_input": SimpleNamespace(turn_index=6),
        "runtime_services": SimpleNamespace(
            gateway=lambda **_kwargs: object()
        ),
        "cache": {},
        "confirmation_scene_text": "确认正文",
        "updated_state": updated_state,
        "core_memory": CoreMemory(items=[]),
        "trace_id": "trace-6",
        "scene_facts": object(),
    })

    assert result["turn_summary"] is summary
    assert captured["scene_text"] == "确认正文"
    assert captured["current_state"] is updated_state
    assert "scene_facts" not in captured


def test_journal_completes_before_any_maintenance_is_scheduled():
    calls = []
    current_state = CurrentState(
        scene=SceneState(scene_id="scene_001", location="测试地点"),
        player=PlayerState(name="主角"),
    )

    class _Committer:
        def commit(self, _state):
            calls.append("business_commit")
            return object()

    class _Scheduler:
        def schedule_restock(self, _state):
            calls.append("restock")
            return True

        def schedule_followups(self, _state, _result, **_kwargs):
            calls.append("followups")
            return object()

    class _EventBuilder:
        def build(self, _state, _commit_result, _schedule_result):
            calls.append("events")
            return []

    runtime = SimpleNamespace(
        history=SimpleNamespace(
            record_state_audit=lambda *_args, **_kwargs: calls.append("audit")
        ),
        complete_confirmation=lambda _pending: calls.append("journal_completed"),
        pending_turns=SimpleNamespace(
            discard_group=lambda _group_id: calls.append("pending_removed")
        ),
        pending_openings=SimpleNamespace(
            remove=lambda _turn_id: calls.append("pending_removed")
        ),
    )
    state = {
        "write_db": True,
        "journaled": True,
        "turn_id": "turn-1",
        "pending": SimpleNamespace(),
        "pipeline_input": SimpleNamespace(state_before=current_state),
        "state_change_set": {},
        "updated_state": current_state,
        "cache": {},
        "events": [],
    }

    ConfirmationFinalizer(
        runtime,
        _Committer(),
        _Scheduler(),
        _EventBuilder(),
    ).finalize(state)

    assert calls == [
        "business_commit",
        "audit",
        "journal_completed",
        "pending_removed",
        "restock",
        "followups",
        "events",
    ]


def test_confirmation_second_wave_starts_after_helper_state_is_ready():
    graph = build_confirmation_second_wave_graph().get_graph()
    edges = {(edge.source, edge.target) for edge in graph.edges}

    assert ("__start__", "action_preview") in edges
    assert ("__start__", "director_event_update") in edges
    assert ("__start__", "entity_profile_update") in edges
    assert ("__start__", "npc_profile_update") in edges


def test_confirmation_events_reducer_appends_node_events():
    def first(_state: ConfirmationState) -> ConfirmationState:
        return {"events": [{"step": "first", "data": {}}]}

    def second(_state: ConfirmationState) -> ConfirmationState:
        return {"events": [{"step": "second", "data": {}}]}

    graph = StateGraph(ConfirmationState)
    graph.add_node("first", first)
    graph.add_node("second", second)
    graph.add_edge(START, "first")
    graph.add_edge("first", "second")
    graph.add_edge("second", END)

    result = graph.compile().invoke({"events": [{"step": "start", "data": {}}]})

    assert [event["step"] for event in result["events"]] == [
        "start",
        "first",
        "second",
    ]


def test_node_updates_are_persisted_to_retry_cache_at_merge_boundary():
    marker = object()
    state = {"events": [], "cache": {}, "write_db": True}

    merge_confirmation_node_update(
        state,
        {
            "scene_facts": marker,
            "entity_extraction": marker,
            "events": [{"step": "done", "data": {}}],
        },
    )

    assert state["cache"]["scene_facts"] is marker
    assert state["cache"]["entity_extraction"] is marker
    assert state["events"] == [{"step": "done", "data": {}}]


def test_dry_run_merge_does_not_cache_write_only_results():
    marker = object()
    state = {"events": [], "cache": {}, "write_db": False}

    merge_confirmation_node_update(
        state,
        {
            "entity_extraction": marker,
            "updated_entity_ids": ["entity_1"],
        },
    )

    assert "entity_extraction" not in state["cache"]
    assert "updated_entity_ids" not in state["cache"]


def test_parallel_success_is_cacheable_when_sibling_node_fails():
    marker = object()

    def succeeds(_state: ConfirmationState) -> ConfirmationState:
        return {"action_preview": marker}

    def fails(_state: ConfirmationState) -> ConfirmationState:
        raise RuntimeError("failed")

    graph = StateGraph(ConfirmationState)
    graph.add_node("succeeds", capture_confirmation_node_errors("succeeds", succeeds))
    graph.add_node("fails", capture_confirmation_node_errors("fails", fails))
    graph.add_edge(START, "succeeds")
    graph.add_edge(START, "fails")
    graph.add_edge("succeeds", END)
    graph.add_edge("fails", END)

    state = {"events": [], "node_errors": [], "cache": {}, "write_db": True}
    for update in graph.compile().stream(state, stream_mode="updates"):
        for node_update in update.values():
            merge_confirmation_node_update(state, node_update)

    assert state["cache"]["action_preview"] is marker
    assert state["node_errors"][0]["node"] == "fails"
    assert str(state["node_errors"][0]["exception"]) == "failed"


def test_rollback_cache_keeps_pure_results_and_discards_write_markers():
    marker = object()
    pending = SimpleNamespace(confirmation_cache={
        "key": "scene text",
        "scene_facts": marker,
        "updated_state": marker,
        "state_change_set": marker,
        "turn_summary": marker,
        "action_preview": marker,
        "entity_extraction": marker,
        "npc_extraction": marker,
        "director_event_result": marker,
        "updated_entity_ids": ["entity_1"],
        "updated_npc_profile_ids": ["npc_1"],
        "director_npc_profile_id": "npc_2",
        "state_saved": True,
        "short_term_saved": True,
        "scene_summary_scheduled": True,
    })

    reset_confirmation_cache_after_rollback(pending)

    assert pending.confirmation_cache == {
        "key": "scene text",
        "scene_facts": marker,
        "updated_state": marker,
        "state_change_set": marker,
        "turn_summary": marker,
        "action_preview": marker,
    }


def test_journaled_confirmation_failure_resets_rolled_back_cache(monkeypatch):
    marker = object()
    pending = SimpleNamespace(confirmation_cache={
        "key": "scene text",
        "scene_facts": marker,
        "updated_npc_profile_ids": ["npc_1"],
        "state_saved": True,
    })
    calls = []
    runtime = SimpleNamespace(
        begin_confirmation=lambda *_args, **_kwargs: True,
        jobs=SimpleNamespace(wait_for_idle=lambda: calls.append("wait")),
        history=SimpleNamespace(
            fail_confirmation=lambda *_args: calls.append("rollback")
        ),
    )

    def fail_pipeline(*_args, **_kwargs):
        raise RuntimeError("node failed")
        yield

    monkeypatch.setattr(confirmation_graph, "get_campaign_runtime", lambda: runtime)
    monkeypatch.setattr(
        confirmation_graph,
        "stream_confirmation_pipeline",
        fail_pipeline,
    )

    with pytest.raises(RuntimeError, match="node failed"):
        list(confirmation_graph._confirm_pipeline_locked(
            SimpleNamespace(turn_id="turn-1", pending=pending),
        ))

    assert calls == ["wait", "rollback"]
    assert pending.confirmation_cache == {
        "key": "scene text",
        "scene_facts": marker,
    }


def test_edited_scene_preview_drops_original_directions_and_uses_scene_facts(
    monkeypatch,
    configure_runtime_services,
):
    captured = {}
    scene_facts = SceneFactSet(scene_focus="编辑后的场景", facts=[])
    scene_result = SceneWriterResult(
        scene_text="原始正文",
        next_action_directions=[
            NextActionDirection(
                direction="继续原稿中的追逐",
                rationale="原稿保留了追逐。",
            )
        ],
    )

    class _Gateway:
        def set_trace_context(self, **_kwargs):
            return None

    class _PreviewAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            captured.update(kwargs)
            return {"previews": []}

    services = configure_runtime_services(gateway_factory=_Gateway)
    monkeypatch.setattr(confirmation_nodes, "ActionPreviewAgent", _PreviewAgent)

    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, scene_result=scene_result),
        "runtime_services": services,
        "trace_id": "trace",
        "cache": {},
        "effective_scene_text": "编辑后的正文",
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_001", location="新地点"),
            player=PlayerState(name="主角"),
        ),
        "core_memory": CoreMemory(items=[]),
        "recent_summaries": [],
        "scene_facts": scene_facts,
    }

    confirmation_nodes.action_preview_node(state)

    assert captured["next_action_directions"] == []
    assert captured["scene_facts"] is scene_facts
