from types import SimpleNamespace

import pytest

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.recall_memory.schemas import MemoryDraftExtractResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory import ShortTermMemory
from app.engine.session import PENDING_TURNS, PendingTurn
from app.engine import turn_confirmation


class _ContextStore:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def close(self):
        return None


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角", position="测试地点"),
    )


def test_failed_preview_retries_only_preview_and_dry_run_keeps_pending(monkeypatch):
    turn_id = "retryable-turn"
    state = _state()
    pending = PendingTurn(
        turn_id=turn_id,
        user_input="观察四周",
        state_before=state,
        context=SimpleNamespace(
            turn_resolution=SimpleNamespace(selected_type="advance")
        ),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文", changes_summary=[]),
        selected_memory_ids=[],
        turn_index=1,
        short_term_memory=ShortTermMemory(),
    )
    PENDING_TURNS[turn_id] = pending
    calls = {"state": 0, "memory": 0, "preview": 0}

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            calls["state"] += 1
            return current_state

    class _MemoryExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, *_args, **_kwargs):
            calls["memory"] += 1
            return MemoryDraftExtractResult(items=[])

    def _preview(**_kwargs):
        calls["preview"] += 1
        if calls["preview"] == 1:
            raise RuntimeError("preview failed")
        return {"previews": ["ok"]}

    monkeypatch.setattr(turn_confirmation, "create_gateway", lambda: object())
    monkeypatch.setattr(turn_confirmation, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(
        turn_confirmation,
        "MemoryDraftExtractor",
        _MemoryExtractor,
    )
    monkeypatch.setattr(
        turn_confirmation,
        "generate_action_preview_workflow",
        _preview,
    )
    monkeypatch.setattr(
        turn_confirmation,
        "core_memory_store",
        lambda: _ContextStore(load_required=lambda: CoreMemory(items=[])),
    )
    monkeypatch.setattr(
        turn_confirmation,
        "scene_summary_store",
        lambda: _ContextStore(list_active=lambda: [], get_recent=lambda _limit=5: []),
    )
    monkeypatch.setattr(
        turn_confirmation,
        "short_term_memory_store",
        lambda: _ContextStore(get_recent=lambda: ShortTermMemory()),
    )
    monkeypatch.setattr(
        turn_confirmation,
        "memory_store",
        lambda: _ContextStore(count=lambda: 0),
    )

    try:
        with pytest.raises(RuntimeError, match="preview failed"):
            list(
                turn_confirmation._confirm_turn_stream_locked(
                    turn_id,
                    write_db=False,
                )
            )

        events = list(
            turn_confirmation._confirm_turn_stream_locked(
                turn_id,
                write_db=False,
            )
        )

        assert calls == {"state": 1, "memory": 1, "preview": 2}
        assert events[-1]["data"]["status"] == "dry_run"
        assert turn_id in PENDING_TURNS
    finally:
        PENDING_TURNS.pop(turn_id, None)
