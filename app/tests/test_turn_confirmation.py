from concurrent.futures import Future
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.entity.schemas import EntityExtractionResult
from app.domain.npc_profile.schemas import NPCExtractionResult
from app.domain.recall_memory.schemas import MemoryDraftExtractResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory import ShortTermMemory
from app.runtime.session import PENDING_OPENINGS, PENDING_TURNS, PendingOpening, PendingTurn
from app.graph import confirmation_graph as turn_confirmation
from app.graph import confirmation_helpers
from app.graph import confirmation_nodes


class _ContextStore:
    def __init__(self, **values):
        self.__dict__.update(values)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def close(self):
        return None


class _ActionPreviewAgent:
    def __init__(self, _gateway):
        pass

    def generate(self, **_kwargs):
        return {"previews": []}


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
        group_id=turn_id,
        version_index=1,
        user_input="观察四周",
        difficulty="困难",
        max_chars=1500,
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文"),
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

    class _RetryingActionPreviewAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            return _preview(**kwargs)

    class _FakeGateway:
        def set_trace_context(self, **kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            return kwargs["output_schema"].model_validate({})

    monkeypatch.setattr(confirmation_nodes, "create_gateway", lambda: _FakeGateway())
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(
        confirmation_nodes,
        "MemoryDraftExtractor",
        _MemoryExtractor,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _RetryingActionPreviewAgent,
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "core_memory_store",
        lambda: _ContextStore(load_required=lambda: CoreMemory(items=[])),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "scene_summary_store",
        lambda: _ContextStore(list_active=lambda: [], get_recent=lambda _limit=5: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "short_term_memory_store",
        lambda: _ContextStore(get_recent=lambda: ShortTermMemory()),
    )
    monkeypatch.setattr(
        confirmation_helpers,
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


def test_reentered_confirmation_passes_cached_summary_future_to_batch(monkeypatch):
    turn_id = "summary-future-turn"
    state = _state()
    summary_future = Future()
    summary_future.set_result(None)
    pending = PendingTurn(
        turn_id=turn_id,
        group_id=turn_id,
        version_index=1,
        user_input="继续观察",
        difficulty="困难",
        max_chars=1500,
        state_before=state,
        context=SimpleNamespace(turn_resolution=SimpleNamespace()),
        outline=SimpleNamespace(),
        scene_result=SceneWriterResult(scene_text="场景正文"),
        turn_index=14,
        short_term_memory=ShortTermMemory(),
        confirmation_cache={
            "key": "场景正文",
            "scene_summary_scheduled": True,
            "scene_summary_future": summary_future,
        },
    )
    PENDING_TURNS[turn_id] = pending
    sibling_turn_id = "summary-future-turn-v2"
    PENDING_TURNS[sibling_turn_id] = replace(
        pending,
        turn_id=sibling_turn_id,
        version_index=2,
        confirmation_cache={},
    )
    captured = {}

    class _FakeGateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            return kwargs["output_schema"].model_validate({})

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            return current_state

    class _MemoryExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, *_args, **_kwargs):
            return MemoryDraftExtractResult(items=[])

    class _EntityExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return EntityExtractionResult()

    class _NPCExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return NPCExtractionResult()

    monkeypatch.setattr(confirmation_nodes, "create_gateway", lambda: _FakeGateway())
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(confirmation_nodes, "MemoryDraftExtractor", _MemoryExtractor)
    monkeypatch.setattr(confirmation_nodes, "EntityExtractor", _EntityExtractor)
    monkeypatch.setattr(confirmation_nodes, "NPCProfileExtractor", _NPCExtractor)
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _ActionPreviewAgent,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_director_event_workflow",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_entity_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_npc_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "sync_npc_profile_absence_from_current_state",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "core_memory_store",
        lambda: _ContextStore(load_required=lambda: CoreMemory(items=[])),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "current_state_store",
        lambda: _ContextStore(load_or_default=lambda: state, save=lambda _state: None),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "scene_summary_store",
        lambda: _ContextStore(list_active=lambda: [], get_recent=lambda _limit=5: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "short_term_memory_store",
        lambda: _ContextStore(
            add_turn=lambda **_kwargs: None,
            get_recent=lambda: ShortTermMemory(),
        ),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "pending_memory_store",
        lambda: _ContextStore(
            save_many=lambda _items: [],
            count=lambda: 5,
            should_trigger_batch=lambda turn_threshold=5: True,
        ),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "memory_store",
        lambda: _ContextStore(count=lambda: 0, list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "entity_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "entity_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "npc_profile_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "npc_profile_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "director_event_store",
        lambda: _ContextStore(),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "director_npc_store",
        lambda: _ContextStore(
            list_standby=lambda: [object()]
            * confirmation_helpers.DIRECTOR_NPC_STANDBY_TARGET
        ),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "director_npc_store",
        lambda: _ContextStore(
            list_standby=lambda: [object()]
            * confirmation_helpers.DIRECTOR_NPC_STANDBY_TARGET
        ),
    )

    def _submit_once(_fn, /, *_args, **kwargs):
        captured["wait_for_summary"] = kwargs.get("wait_for_summary")
        return True

    monkeypatch.setattr(confirmation_helpers, "submit_memory_batch_once", _submit_once)

    try:
        events = list(
            turn_confirmation._confirm_turn_stream_locked(
                turn_id,
                write_db=True,
            )
        )
        group_removed = (
            turn_id not in PENDING_TURNS
            and sibling_turn_id not in PENDING_TURNS
        )
    finally:
        PENDING_TURNS.pop(turn_id, None)
        PENDING_TURNS.pop(sibling_turn_id, None)

    assert events[-1]["data"]["status"] == "committed"
    assert captured["wait_for_summary"] is summary_future
    assert group_removed is True


def test_opening_confirmation_restocks_director_npc_synchronously(monkeypatch):
    turn_id = "opening-director-npc"
    state = _state()
    pending = PendingOpening(
        turn_id=turn_id,
        config=SimpleNamespace(),
        current_state=state,
        scene_result=SceneWriterResult(scene_text="开场正文"),
    )
    PENDING_OPENINGS[turn_id] = pending
    calls = {"restock": 0}

    class _FakeGateway:
        def set_trace_context(self, **_kwargs):
            pass

        def run(self, **kwargs):
            if kwargs.get("task") == "scene_fact_extract":
                return kwargs["output_schema"].model_validate(
                    {"scene_focus": "测试场景事实", "facts": []}
                )
            return kwargs["output_schema"].model_validate({})

    class _StateUpdater:
        def __init__(self, _gateway):
            pass

        def update(self, current_state, *_args, **_kwargs):
            return current_state

    class _MemoryExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, *_args, **_kwargs):
            return MemoryDraftExtractResult(items=[])

    class _EntityExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return EntityExtractionResult()

    class _NPCExtractor:
        def __init__(self, _gateway):
            pass

        def extract(self, **_kwargs):
            return NPCExtractionResult()

    monkeypatch.setattr(confirmation_nodes, "create_gateway", lambda: _FakeGateway())
    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _StateUpdater)
    monkeypatch.setattr(confirmation_nodes, "MemoryDraftExtractor", _MemoryExtractor)
    monkeypatch.setattr(confirmation_nodes, "EntityExtractor", _EntityExtractor)
    monkeypatch.setattr(confirmation_nodes, "NPCProfileExtractor", _NPCExtractor)
    monkeypatch.setattr(
        confirmation_nodes,
        "ActionPreviewAgent",
        _ActionPreviewAgent,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_entity_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "update_npc_profiles_from_extraction",
        lambda **_kwargs: SimpleNamespace(saved_ids=[]),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "sync_npc_profile_absence_from_current_state",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "core_memory_store",
        lambda: _ContextStore(load_required=lambda: CoreMemory(items=[])),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "current_state_store",
        lambda: _ContextStore(load_or_default=lambda: state, save=lambda _state: None),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "scene_summary_store",
        lambda: _ContextStore(list_active=lambda: [], get_recent=lambda _limit=5: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "short_term_memory_store",
        lambda: _ContextStore(
            clear=lambda: None,
            add_turn=lambda **_kwargs: None,
            get_recent=lambda: ShortTermMemory(),
        ),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "pending_memory_store",
        lambda: _ContextStore(
            save_many=lambda _items: [],
            count=lambda: 0,
            should_trigger_batch=lambda turn_threshold=5: False,
        ),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "memory_store",
        lambda: _ContextStore(count=lambda: 0, list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "entity_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "entity_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "npc_profile_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "npc_profile_store",
        lambda: _ContextStore(list_all=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "director_npc_store",
        lambda: _ContextStore(list_standby=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "director_npc_store",
        lambda: _ContextStore(list_standby=lambda: []),
    )
    monkeypatch.setattr(
        confirmation_helpers,
        "run_scene_summary_maintenance",
        lambda *_args, **_kwargs: None,
    )

    def _restock(*_args, **_kwargs):
        calls["restock"] += 1

    monkeypatch.setattr(confirmation_helpers, "run_director_npc_restock", _restock)

    try:
        events = list(
            turn_confirmation._confirm_opening_stream_locked(
                turn_id,
                write_db=True,
            )
        )
    finally:
        PENDING_OPENINGS.pop(turn_id, None)

    assert calls["restock"] == 1
    assert events[-1]["data"]["status"] == "committed"
    assert events[-1]["data"]["director_npc_restock_scheduled"] is True
