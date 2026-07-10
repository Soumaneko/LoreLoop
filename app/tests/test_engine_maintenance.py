from types import SimpleNamespace

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_summary.schemas import SceneSummary
from app.engine import maintenance
from app.graph import maintenance_helpers, maintenance_nodes


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_007",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角", position="测试地点"),
    )


class _Gateway:
    def __init__(self):
        self.context = {}

    def set_trace_context(self, **kwargs):
        self.context.update(kwargs)


class _Store:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None


def test_scene_summary_maintenance_sets_turn_trace_context(monkeypatch):
    gateway = _Gateway()
    monkeypatch.setattr(maintenance_helpers, "create_gateway", lambda: gateway)
    monkeypatch.setattr(
        maintenance_nodes,
        "scene_summary_store",
        lambda: _SceneSummaryStore(),
    )
    monkeypatch.setattr(
        maintenance_helpers,
        "scene_summary_store",
        lambda: _SceneSummaryStore(),
    )
    monkeypatch.setattr(maintenance_nodes, "SceneSummarizer", _SceneSummarizer)

    maintenance.run_scene_summary_maintenance(
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
    gateway = _Gateway()
    monkeypatch.setattr(maintenance_helpers, "create_gateway", lambda: gateway)
    monkeypatch.setattr(maintenance_nodes, "pending_memory_store", _Store)
    monkeypatch.setattr(maintenance_nodes, "memory_store", _Store)
    monkeypatch.setattr(maintenance_nodes, "entity_store", _Store)
    monkeypatch.setattr(maintenance_nodes, "scene_summary_store", _Store)
    monkeypatch.setattr(
        maintenance_nodes,
        "batch_classify_pending_memories_workflow",
        lambda **_kwargs: SimpleNamespace(
            triggered=False,
            pending_count=0,
            saved_ids=[],
            classified_pending_ids=[],
            batch_classify_count=0,
            should_maintain=False,
        ),
    )

    maintenance.run_memory_batch_maintenance(
        _state(),
        "trace-8",
        8,
        core_memory=CoreMemory(items=[]),
    )

    assert gateway.context == {
        "turn_index": 8,
        "session_type": "maintenance",
    }


class _SceneSummaryStore(_Store):
    def save_one(self, summary):
        return summary.id

    def should_condense(self, _level, *, threshold):
        return False


class _SceneSummarizer:
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
