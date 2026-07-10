from concurrent.futures import Future
import json
from types import SimpleNamespace

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.recall_memory.maintenance import (
    BatchClassifyPendingMemoryWorkflowResult,
    MaintainMemoriesWorkflowResult,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.store import SceneSummaryStore
from app.graph import maintenance_graph
from app.graph import maintenance_helpers, maintenance_nodes
from app.graph.maintenance_state import MemoryMaintenanceState


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_007",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角", position="测试地点"),
    )


def test_scene_summary_graph_has_only_llm_workflow_nodes():
    graph = maintenance_graph.build_scene_summary_maintenance_graph()

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "scene_summarize",
        "scene_summary_condense",
        "__end__",
    }


def test_scene_summary_graph_preserves_recursive_condensation(monkeypatch):
    mock = MockLLM()
    mock.set_responses(
        [
            json.dumps({"content": "第2回合摘要。"}, ensure_ascii=False),
            json.dumps({"content": "L2 汇总。"}, ensure_ascii=False),
            json.dumps({"content": "L3 汇总。"}, ensure_ascii=False),
        ]
    )
    store = SceneSummaryStore(":memory:")
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

    monkeypatch.setattr(maintenance_nodes, "scene_summary_store", _StoreContext)
    monkeypatch.setattr(maintenance_helpers, "scene_summary_store", _StoreContext)
    monkeypatch.setattr(
        maintenance_nodes,
        "maintenance_gateway",
        lambda **_kwargs: mock.build_gateway(),
    )

    try:
        state = maintenance_helpers.prepare_scene_summary_state(
            "第2回合正文。",
            _state(),
            "scene_002",
            2,
            "trace-2",
            core_memory=CoreMemory(items=[]),
            threshold=2,
        )
        result = maintenance_graph.build_scene_summary_maintenance_graph().invoke(state)

        assert len(result["result"].created_summaries) == 3
        assert len(result["result"].condensed_ids) == 2
        assert store.count_uncondensed(1) == 0
        assert store.count_uncondensed(2) == 0
        assert store.count_uncondensed(3) == 1
    finally:
        store.close()


def test_memory_batch_graph_preserves_classify_then_maintain(monkeypatch):
    calls = []

    def _classify(_state):
        calls.append("memory_batch_classify")
        return {
            "batch_result": BatchClassifyPendingMemoryWorkflowResult(
                triggered=True,
                should_maintain=True,
            ),
            "events": [{"step": "memory_batch_classified", "data": {}}],
        }

    def _maintain(_state):
        calls.append("memory_maintain")
        return {
            "maintenance_result": MaintainMemoriesWorkflowResult(),
            "events": [{"step": "memory_maintained", "data": {}}],
        }

    monkeypatch.setattr(maintenance_graph, "memory_batch_classify_node", _classify)
    monkeypatch.setattr(maintenance_graph, "memory_maintain_node", _maintain)
    graph = maintenance_graph.build_memory_batch_maintenance_graph()
    state: MemoryMaintenanceState = {
        "events": [],
        "trace_id": "trace-7",
        "turn_index": 7,
        "current_state": _state(),
        "core_memory": CoreMemory(items=[]),
    }

    result = graph.invoke(state)

    assert calls == ["memory_batch_classify", "memory_maintain"]
    assert [event["step"] for event in result["events"]] == [
        "memory_batch_classified",
        "memory_maintained",
    ]


def test_memory_batch_graph_skips_maintain_when_classification_does_not_trigger(
    monkeypatch,
):
    def _classify(_state):
        return {
            "batch_result": BatchClassifyPendingMemoryWorkflowResult(triggered=False),
            "events": [],
        }

    monkeypatch.setattr(maintenance_graph, "memory_batch_classify_node", _classify)
    monkeypatch.setattr(
        maintenance_graph,
        "memory_maintain_node",
        lambda _state: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    graph = maintenance_graph.build_memory_batch_maintenance_graph()

    result = graph.invoke(
        {
            "events": [],
            "trace_id": "trace-8",
            "turn_index": 8,
            "current_state": _state(),
            "core_memory": CoreMemory(items=[]),
        }
    )

    assert result["batch_result"].triggered is False


def test_background_memory_batch_waits_for_summary_before_invoking_graph(monkeypatch):
    order = []

    class _SummaryFuture(Future):
        def result(self, timeout=None):
            order.append("summary")
            return None

    class _Graph:
        def invoke(self, _state):
            order.append("memory")
            return {
                "batch_result": SimpleNamespace(
                    triggered=False,
                    pending_count=0,
                    saved_ids=[],
                    classified_pending_ids=[],
                )
            }

    monkeypatch.setattr(
        maintenance_graph,
        "build_memory_batch_maintenance_graph",
        lambda: _Graph(),
    )

    maintenance_graph.run_memory_batch_maintenance(
        _state(),
        "trace-9",
        9,
        core_memory=CoreMemory(items=[]),
        wait_for_summary=_SummaryFuture(),
    )

    assert order == ["summary", "memory"]


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


def test_scene_summary_maintenance_sets_turn_trace_context(monkeypatch):
    gateway = _TraceGateway()
    monkeypatch.setattr(maintenance_helpers, "create_gateway", lambda: gateway)
    monkeypatch.setattr(
        maintenance_nodes,
        "scene_summary_store",
        _TraceSceneSummaryStore,
    )
    monkeypatch.setattr(
        maintenance_helpers,
        "scene_summary_store",
        _TraceSceneSummaryStore,
    )
    monkeypatch.setattr(
        maintenance_nodes,
        "SceneSummarizer",
        _TraceSceneSummarizer,
    )

    maintenance_graph.run_scene_summary_maintenance(
        "正文",
        _state(),
        "scene_007",
        7,
        "trace-7",
        core_memory=CoreMemory(items=[]),
    )

    assert gateway.context == {
        "turn_index": 7,
        "session_type": "maintenance",
    }


def test_memory_batch_maintenance_sets_turn_trace_context(monkeypatch):
    gateway = _TraceGateway()
    monkeypatch.setattr(maintenance_helpers, "create_gateway", lambda: gateway)
    monkeypatch.setattr(maintenance_nodes, "pending_memory_store", _TraceStore)
    monkeypatch.setattr(maintenance_nodes, "memory_store", _TraceStore)
    monkeypatch.setattr(maintenance_nodes, "entity_store", _TraceStore)
    monkeypatch.setattr(maintenance_nodes, "scene_summary_store", _TraceStore)
    monkeypatch.setattr(
        maintenance_nodes,
        "batch_classify_pending_memories_workflow",
        lambda **_kwargs: SimpleNamespace(
            triggered=False,
            pending_count=0,
            saved_ids=[],
            classified_pending_ids=[],
            should_maintain=False,
        ),
    )

    maintenance_graph.run_memory_batch_maintenance(
        _state(),
        "trace-8",
        8,
        core_memory=CoreMemory(items=[]),
    )

    assert gateway.context == {
        "turn_index": 8,
        "session_type": "maintenance",
    }
