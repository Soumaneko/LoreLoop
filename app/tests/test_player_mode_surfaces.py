import io
import json

import pytest

from app.core.client_error import ClientValidationError
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import WorldClock
from app.domain.director_event import DirectorEvent
from app.player_server import PlayerHandler
from app.runtime.player_records import (
    load_player_events,
)
from app.runtime.core_memory import (
    load_core_memory_items,
    save_core_memory_items,
)
from app.runtime.session import get_campaign_runtime
from app.runtime.settings import save_experience_mode


def _seed_core_memory():
    runtime = get_campaign_runtime()
    runtime.stores.core_memory.save(CoreMemory(items=[
        CoreMemoryItem(
            id="base_rule",
            kind="world_invariant",
            strength="hard_constraint",
            source="campaign_setup",
            content="魔法无法复活死者。",
        ),
    ]))


def _seed_event():
    runtime = get_campaign_runtime()
    with runtime.stores.director_event as store:
        store.save(DirectorEvent(
            title="旧桥封锁",
            subject="河务署",
            intent="控制渡河通道",
            method="封锁旧桥",
            macro_intent="改变城镇交通格局",
            deadline_clock=WorldClock().advance_phase(3),
            created_turn=1,
            updated_turn=1,
        ))


def test_records_route_accepts_requested_view(monkeypatch):
    import app.api as api_module

    captured = {}

    def fake_load_records(view="general"):
        captured["view"] = view
        return {}

    monkeypatch.setattr(api_module, "_load_player_records", fake_load_records)
    handler = object.__new__(PlayerHandler)
    handler.path = "/api/player/records?view=advanced"
    results = []
    handler._handle_player_json = lambda callback: results.append(callback())
    handler.do_GET()
    assert captured == {"view": "advanced"}


def test_load_player_events_in_player_mode(campaign_runtime):
    _seed_event()
    assert len(load_player_events()["events"]) == 1


def test_load_player_events_browses_creator_mode(campaign_runtime):
    _seed_event()
    save_experience_mode({"experience_mode": "creator"})
    payload = load_player_events()
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event["event_id"].startswith("director_event_")
    assert event["title"] == "旧桥封锁"
    assert event["stage"] == "hidden"
    assert event["deadline_clock"]["day"] >= 1
    assert event["created_turn"] == 1


def test_core_memory_can_be_edited_in_player_mode(campaign_runtime):
    _seed_core_memory()
    assert load_core_memory_items()["items"][0]["id"] == "base_rule"
    assert save_core_memory_items({"items": []})["items"] == []
    assert load_core_memory_items()["items"] == []


def test_core_memory_new_items_are_player_sourced_and_keep_existing_source(campaign_runtime):
    _seed_core_memory()
    save_experience_mode({"experience_mode": "creator"})
    saved = save_core_memory_items({"items": [
        {
            "id": "base_rule",
            "kind": "world_invariant",
            "strength": "hard_constraint",
            "content": "魔法无法复活死者。",
        },
        {
            "id": "",
            "kind": "narrative_preference",
            "strength": "strong_preference",
            "content": "保持克制的悬疑氛围。",
        },
    ]})
    by_id = {item["id"]: item for item in saved["items"]}
    assert by_id["base_rule"]["source"] == "campaign_setup"
    new_item = next(item for item in saved["items"] if item["id"] != "base_rule")
    assert new_item["id"].startswith("core_memory_")
    assert new_item["source"] == "player"
    assert new_item["content"] == "保持克制的悬疑氛围。"


def test_core_memory_rejects_invalid_items(campaign_runtime):
    _seed_core_memory()
    save_experience_mode({"experience_mode": "creator"})
    with pytest.raises(ClientValidationError):
        save_core_memory_items({"items": [
            {"id": "", "kind": "bogus_kind", "strength": "hard_constraint", "content": "x"},
        ]})
    with pytest.raises(ClientValidationError):
        save_core_memory_items({"items": [{"content": "  "}]})


def test_core_memory_rejects_uninitialized_runtime(campaign_runtime):
    save_experience_mode({"experience_mode": "creator"})
    with pytest.raises(ClientValidationError):
        save_core_memory_items({"items": []})


def test_events_route_dispatches_by_http_method(monkeypatch):
    import app.api as api_module

    calls = {"count": 0}

    def fake_load_events():
        calls["count"] += 1
        return {"events": []}

    monkeypatch.setattr(api_module, "_load_player_events", fake_load_events)
    handler = object.__new__(PlayerHandler)
    handler.path = "/api/player/events"
    results = []
    handler._handle_player_json = lambda callback: results.append(callback())
    handler.do_GET()
    assert calls["count"] == 1
    assert results == [{"events": []}]


def test_core_memory_routes_dispatch_by_http_method(monkeypatch):
    import app.api as api_module

    get_calls = {"count": 0}
    post_calls = {"count": 0}

    def fake_load_items():
        get_calls["count"] += 1
        return {"initialized": True, "items": []}

    def fake_save_items(payload):
        post_calls["count"] += 1
        return {"initialized": True, "items": payload.get("items", [])}

    monkeypatch.setattr(api_module, "_load_core_memory_items", fake_load_items)
    monkeypatch.setattr(api_module, "_save_core_memory_items", fake_save_items)

    get_handler = object.__new__(PlayerHandler)
    get_handler.path = "/api/player/core-memory"
    get_results = []
    get_handler._handle_player_json = lambda callback: get_results.append(callback())
    get_handler.do_GET()
    assert get_calls["count"] == 1

    body = json.dumps({"items": [{"id": "x"}]}).encode("utf-8")
    post_handler = object.__new__(PlayerHandler)
    post_handler.path = "/api/player/core-memory"
    post_handler.headers = {"Content-Length": str(len(body))}
    post_handler.rfile = io.BytesIO(body)
    post_results = []
    post_handler._handle_player_json = lambda callback: post_results.append(callback())
    post_handler.do_POST()
    assert post_calls["count"] == 1
    assert post_results == [{"initialized": True, "items": [{"id": "x"}]}]
