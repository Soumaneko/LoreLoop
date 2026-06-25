from types import SimpleNamespace

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.engine import maintenance


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
    monkeypatch.setattr(maintenance, "create_gateway", lambda: gateway)
    monkeypatch.setattr(
        maintenance,
        "scene_summary_store",
        _Store,
    )
    monkeypatch.setattr(
        maintenance,
        "run_scene_summary_maintenance_workflow",
        lambda **_kwargs: SimpleNamespace(
            saved_l1_id="summary-7",
            condensed_ids=[],
        ),
    )

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
    monkeypatch.setattr(maintenance, "create_gateway", lambda: gateway)
    monkeypatch.setattr(maintenance, "pending_memory_store", _Store)
    monkeypatch.setattr(maintenance, "memory_store", _Store)
    monkeypatch.setattr(maintenance, "entity_store", _Store)
    monkeypatch.setattr(
        maintenance,
        "batch_classify_pending_memories_workflow",
        lambda **_kwargs: SimpleNamespace(
            triggered=False,
            pending_count=0,
            saved_ids=[],
            classified_pending_ids=[],
            batch_classify_count=0,
            should_consolidate=False,
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
