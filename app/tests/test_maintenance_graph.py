from app.tests.factories.stores import open_scene_summary_store
import json
from types import SimpleNamespace

from app.core.diagnostics import DiagnosticLogger
from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_summary.maintenance import SceneSummaryMaintenanceResult
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.summarizer import SceneSummaryWarning
from app.graph import maintenance_graph
from app.graph import maintenance_helpers, maintenance_nodes
from app.runtime.session import get_campaign_runtime


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_007",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )


def test_scene_summary_graph_has_only_llm_workflow_nodes():
    graph = maintenance_graph.build_scene_summary_maintenance_graph()

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "scene_summary_condense",
        "__end__",
    }


def test_scene_summary_graph_preserves_recursive_condensation(
    monkeypatch,
    configure_runtime_services,
):
    mock = MockLLM()
    mock.set_responses(
        [
            json.dumps({"content": "L2 汇总。"}, ensure_ascii=False),
            json.dumps({"content": "L3 汇总。"}, ensure_ascii=False),
        ]
    )
    store = open_scene_summary_store(":memory:")
    store.save_one(
        SceneSummary(
            id="l1-current",
            level=1,
            content="第2回合摘要。",
            scene_ids=["scene_002"],
            turn_start=2,
            turn_end=2,
        )
    )
    store.save_one(
        SceneSummary(
            id="l1-existing",
            level=1,
            content="第1回合摘要。",
            scene_ids=["scene_001"],
            turn_start=1,
            turn_end=1,
        )
    )
    store.save_one(
        SceneSummary(
            id="l2-existing",
            level=2,
            content="旧 L2 摘要。",
            source_ids=["old-l1"],
            scene_ids=["scene_001"],
            turn_start=1,
            turn_end=1,
        )
    )

    class _StoreContext:
        def __enter__(self):
            return store

        def __exit__(self, *_args):
            return None

    configure_runtime_services(scene_summary=_StoreContext)
    monkeypatch.setattr(
        maintenance_nodes,
        "maintenance_gateway",
        lambda **_kwargs: mock.build_gateway(),
    )

    try:
        services = get_campaign_runtime().services
        state = maintenance_helpers.prepare_scene_summary_state(
            "trace-2",
            2,
            core_memory=CoreMemory(items=[]),
            threshold=2,
            services=services,
        )
        result = maintenance_graph.build_scene_summary_maintenance_graph().invoke(state)

        assert len(result["result"].created_summaries) == 2
        assert len(result["result"].condensed_ids) == 2
        assert store.count_uncondensed(1) == 0
        assert store.count_uncondensed(2) == 0
        assert store.count_uncondensed(3) == 1
    finally:
        store.close()


class _TraceGateway:
    def __init__(self):
        self.context = {}

    def set_trace_context(self, **kwargs):
        self.context.update(kwargs)


class _TraceStore:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


class _TraceSceneSummaryStore(_TraceStore):
    def save_one(self, summary):
        return summary.id

    def should_condense(self, _level, *, threshold):
        return False


class _TraceSceneSummarizer:
    def __init__(self, _gateway):
        pass

    def summarize_turn(self, **kwargs):
        return SceneSummary(
            id="summary-7",
            level=1,
            content="摘要",
            scene_ids=[kwargs["scene_id"]],
            turn_start=kwargs["turn_index"],
            turn_end=kwargs["turn_index"],
        )


def test_scene_summary_maintenance_sets_turn_trace_context(
    monkeypatch,
    configure_runtime_services,
):
    gateway = _TraceGateway()
    configure_runtime_services(
        gateway_factory=lambda: gateway,
        scene_summary=_TraceSceneSummaryStore,
    )
    monkeypatch.setattr(
        maintenance_nodes,
        "SceneSummarizer",
        _TraceSceneSummarizer,
    )

    maintenance_graph.run_scene_summary_maintenance(
        "trace-7",
        7,
        core_memory=CoreMemory(items=[]),
    )

    assert gateway.context == {
        "turn_index": 7,
        "session_type": "maintenance",
    }


def test_scene_summary_condense_skip_emits_diagnostic_and_keeps_sources(
    monkeypatch,
    tmp_path,
):
    store = open_scene_summary_store(":memory:")
    store.save_one(SceneSummary(
        id="s1", level=1, content="摘要1", turn_start=1, turn_end=1,
    ))
    store.save_one(SceneSummary(
        id="s2", level=1, content="摘要2", turn_start=2, turn_end=2,
    ))

    class _StoreContext:
        def __enter__(self):
            return store

        def __exit__(self, *_args):
            return None

    class _SkipSummarizer:
        def __init__(self, _gateway):
            self.last_warnings = [SceneSummaryWarning(
                code="scene_summary_condense_skipped",
                message="skip condense",
            )]

        def condense(self, **_kwargs):
            return None

    monkeypatch.setattr(maintenance_nodes, "SceneSummarizer", _SkipSummarizer)
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "events": [],
        "runtime_services": SimpleNamespace(
            gateway=lambda **_kwargs: object(),
            stores=SimpleNamespace(scene_summary=_StoreContext()),
            diagnostics=diagnostics,
        ),
        "trace_id": "trace-maintenance",
        "turn_index": 1,
        "core_memory": CoreMemory(items=[]),
        "threshold": 2,
        "result": SceneSummaryMaintenanceResult(),
    }

    result = maintenance_nodes.scene_summary_condense_node(state)

    assert result["result"].condensed_ids == []
    assert store.count_uncondensed(1) == 2
    assert store.count_uncondensed(2) == 0
    diagnostic = diagnostics.read(trace_id="trace-maintenance")[0]
    assert diagnostic["source"] == "scene_summary"
    assert diagnostic["code"] == "scene_summary_condense_skipped"
    store.close()
