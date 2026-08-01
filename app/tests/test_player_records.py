import io
import json
from types import SimpleNamespace

import pytest

from app import player_server
from app.domain.continuity.schemas import ContinuityItem
from app.domain.current_state import (
    CurrentState,
    ParticipantState,
    PlayerState,
    SceneState,
)
from app.domain.entity.schemas import EntityProfile
from app.domain.npc_profile.schemas import NPCProfile
from app.player_server import PlayerHandler
from app.runtime.player_records import (
    load_player_records,
    update_player_record,
)
from app.runtime.session import get_campaign_runtime


def _seed_records():
    runtime = get_campaign_runtime()
    with runtime.stores.current_state() as store:
        store.save(CurrentState(
            scene=SceneState(
                scene_id="scene_1",
                location="老橡树镇",
                participants=[ParticipantState(name="莱尔")],
            ),
            player=PlayerState(name="莱尔"),
        ))
    with runtime.stores.continuity() as store:
        with store.transaction():
            store.insert(ContinuityItem(
                kind="commitment",
                subject="找回木偶",
                statement="帮助埃利奥找回被窃的木偶。",
                entities=["埃利奥"],
                location="老橡树镇",
                created_turn=1,
                updated_turn=1,
            ))
            store.insert(ContinuityItem(
                kind="open_thread",
                subject="灰烬图书馆的密门",
                statement="图书馆深处有一扇密门尚未打开。",
                status="resolved",
                created_turn=1,
                updated_turn=2,
                closed_turn=2,
            ))
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师"],
            role="流浪木偶师",
            public_summary="一名公开身份不明的木偶师。",
            core_motivation="找回被窃的木偶。",
            hidden_secret="曾目睹一桩旧案。",
            current_location="老橡树镇",
        ))
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="location",
            name="灰烬图书馆",
            aliases=["图书馆"],
            summary="藏有大量旧书的公开地点。",
        ))


def test_load_player_records_projects_continuity_npc_entity():
    runtime = get_campaign_runtime()
    _seed_records()

    data = load_player_records()

    assert set(data) >= {"maintenance_running", "continuity", "npc_profiles", "entity_profiles"}
    assert len(data["continuity"]) == 2
    active = next(item for item in data["continuity"] if item["status"] == "active")
    resolved = next(item for item in data["continuity"] if item["status"] == "resolved")
    assert set(active["editable"]) == {
        "subject", "statement", "entities", "location", "deadline",
    }
    assert resolved["editable"] == []
    assert active["subject"] == "找回木偶"

    [npc] = data["npc_profiles"]
    assert npc["public"]["name"] == "埃利奥"
    assert npc["public"]["role"] == "流浪木偶师"
    assert npc["director_only"]["hidden_secret"] == "曾目睹一桩旧案。"
    assert npc["director_only"]["core_motivation"] == "找回被窃的木偶。"
    assert "hidden_secret" not in npc["public"]

    [entity] = data["entity_profiles"]
    assert entity["entity_type"] == "location"
    assert entity["fields"]["name"] == "灰烬图书馆"


def test_update_continuity_edits_fields_without_status_change():
    _seed_records()
    data = load_player_records()
    active = next(item for item in data["continuity"] if item["status"] == "active")

    result = update_player_record(
        "continuity",
        active["id"],
        {"subject": "找回木偶（重）", "deadline": "第三天前"},
    )

    assert result["subject"] == "找回木偶（重）"
    assert result["deadline"] == "第三天前"
    assert result["status"] == "active"
    assert result["statement"] == "帮助埃利奥找回被窃的木偶。"

    again = load_player_records()
    updated = next(item for item in again["continuity"] if item["id"] == active["id"])
    assert updated["subject"] == "找回木偶（重）"


def test_update_continuity_fills_null_fields():
    _seed_records()
    data = load_player_records()
    active = next(item for item in data["continuity"] if item["status"] == "active")
    assert active["deadline"] is None

    result = update_player_record("continuity", active["id"], {"deadline": "明日黄昏"})
    assert result["deadline"] == "明日黄昏"


def test_update_continuity_rejects_resolved_item_and_unknown_id():
    _seed_records()
    data = load_player_records()
    resolved = next(item for item in data["continuity"] if item["status"] == "resolved")

    with pytest.raises(ValueError, match="only active continuity items"):
        update_player_record("continuity", resolved["id"], {"subject": "改写"})
    with pytest.raises(ValueError, match="not found"):
        update_player_record("continuity", "missing-id", {"subject": "改写"})


def test_update_npc_public_fields_preserves_director_fields():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    result = update_player_record(
        "npc",
        npc_id,
        {"current_location": "图书馆", "attitude_to_player": "友好"},
    )

    assert result["public"]["current_location"] == "图书馆"
    assert result["public"]["attitude_to_player"] == "友好"
    assert result["director_only"]["hidden_secret"] == "曾目睹一桩旧案。"
    assert result["director_only"]["core_motivation"] == "找回被窃的木偶。"
    assert result["public"]["role"] == "流浪木偶师"


def test_update_npc_preserves_absent_turns_and_refreshes_timestamp():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]
    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile() as store:
        current = store.get_by_id(npc_id)
        assert current is not None
        current.absent_turns = 3
        store.upsert(current)
        stale_updated_at = current.updated_at

    result = update_player_record("npc", npc_id, {"current_location": "图书馆"})

    with runtime.stores.npc_profile() as store:
        saved = store.get_by_id(npc_id)
    assert saved is not None
    assert saved.absent_turns == 3
    assert saved.updated_at != stale_updated_at
    assert saved.updated_at > stale_updated_at
    assert result["public"]["current_location"] == "图书馆"


def test_update_continuity_preserves_null_for_untouched_optional_fields():
    _seed_records()
    data = load_player_records()
    active = next(item for item in data["continuity"] if item["status"] == "active")
    assert active["deadline"] is None

    result = update_player_record(
        "continuity",
        active["id"],
        {"subject": "只改主题"},
    )

    assert result["subject"] == "只改主题"
    assert result["location"] == "老橡树镇"
    assert result["deadline"] is None


def test_update_continuity_treats_empty_string_as_null_for_optional_fields():
    _seed_records()
    data = load_player_records()
    active = next(item for item in data["continuity"] if item["status"] == "active")

    result = update_player_record(
        "continuity",
        active["id"],
        {"subject": "主题", "location": "", "deadline": ""},
    )

    assert result["location"] is None
    assert result["deadline"] is None


def test_update_npc_treats_empty_string_as_null_for_optional_fields():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    result = update_player_record(
        "npc",
        npc_id,
        {"role": "", "current_location": "图书馆"},
    )

    assert result["public"]["role"] is None
    assert result["public"]["current_location"] == "图书馆"


def test_update_npc_filters_empty_items_in_list_fields():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    result = update_player_record(
        "npc",
        npc_id,
        {
            "aliases": ["木偶师", "   ", ""],
            "personality_traits": ["谨慎", "", "  "],
            "known_abilities": ["", "口才"],
        },
    )

    assert result["public"]["aliases"] == ["木偶师"]
    assert result["public"]["personality_traits"] == ["谨慎"]
    assert result["public"]["known_abilities"] == ["口才"]


def test_update_entity_strips_name_and_alias_whitespace():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]

    result = update_player_record(
        "entity",
        entity_id,
        {"name": " 灰烬图书馆 ", "aliases": [" 图书馆 ", "旧馆"]},
    )

    assert result["fields"]["name"] == "灰烬图书馆"
    assert result["fields"]["aliases"] == ["图书馆", "旧馆"]


def test_update_entity_rejects_padded_same_type_name_conflict():
    _seed_records()
    data = load_player_records()
    runtime = get_campaign_runtime()
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="location",
            name="北郡商会",
            summary="一处商号。",
        ))
    entity_id = data["entity_profiles"][0]["id"]
    assert data["entity_profiles"][0]["fields"]["name"] == "灰烬图书馆"

    with pytest.raises(ValueError, match="conflicts with existing profile"):
        update_player_record("entity", entity_id, {"name": "  北郡商会  "})


def test_update_npc_rejects_padded_name_conflict():
    _seed_records()
    data = load_player_records()
    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(name="木偶师"))
    npc_id = data["npc_profiles"][0]["id"]
    assert data["npc_profiles"][0]["public"]["name"] == "埃利奥"

    with pytest.raises(ValueError, match="conflicts with existing profile"):
        update_player_record("npc", npc_id, {"name": "  木偶师  "})


def test_update_npc_rejects_whitespace_only_name():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    with pytest.raises(ValueError, match="name must not be empty"):
        update_player_record("npc", npc_id, {"name": "   "})


def test_update_entity_rejects_whitespace_only_name():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]

    with pytest.raises(ValueError, match="name must not be empty"):
        update_player_record("entity", entity_id, {"name": "  \t "})


def test_update_entity_treats_empty_string_as_null_for_optional_fields():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]

    result = update_player_record(
        "entity",
        entity_id,
        {"summary": "", "parent_location": "老橡树镇"},
    )

    assert result["fields"]["summary"] is None
    assert result["fields"]["parent_location"] == "老橡树镇"


def test_update_npc_rejects_director_fields():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    with pytest.raises(ValueError, match="read-only"):
        update_player_record("npc", npc_id, {"hidden_secret": "新秘密"})
    with pytest.raises(ValueError, match="read-only"):
        update_player_record("npc", npc_id, {"core_motivation": "新动机"})


def test_update_npc_rejects_unknown_fields_and_missing_profile():
    _seed_records()
    data = load_player_records()
    npc_id = data["npc_profiles"][0]["id"]

    with pytest.raises(ValueError, match="public fields only"):
        update_player_record("npc", npc_id, {"npc_conditions": "x"})
    with pytest.raises(ValueError, match="not found"):
        update_player_record("npc", "missing-npc", {"role": "x"})


def test_update_entity_edits_semantic_fields():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]

    result = update_player_record(
        "entity",
        entity_id,
        {"summary": "新建的图书馆。", "parent_location": "老橡树镇"},
    )

    assert result["fields"]["summary"] == "新建的图书馆。"
    assert result["fields"]["parent_location"] == "老橡树镇"
    assert result["entity_type"] == "location"


def test_update_entity_rejects_unknown_fields_and_missing_profile():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]

    with pytest.raises(ValueError, match="semantic fields only"):
        update_player_record("entity", entity_id, {"confidence": 0.9})
    with pytest.raises(ValueError, match="not found"):
        update_player_record("entity", "missing-entity", {"summary": "x"})


def test_update_entity_rejects_same_type_name_conflict():
    _seed_records()
    data = load_player_records()
    runtime = get_campaign_runtime()
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="location",
            name="北郡商会",
            summary="一处商号。",
        ))
    entity_id = data["entity_profiles"][0]["id"]
    assert data["entity_profiles"][0]["fields"]["name"] == "灰烬图书馆"

    with pytest.raises(ValueError, match="conflicts with existing profile"):
        update_player_record("entity", entity_id, {"name": "北郡商会"})

    with runtime.stores.entity() as store:
        saved = store.get_by_id(entity_id)
    assert saved is not None
    assert saved.name == "灰烬图书馆"


def test_update_entity_allows_same_name_across_different_types():
    _seed_records()
    data = load_player_records()
    runtime = get_campaign_runtime()
    with runtime.stores.entity() as store:
        store.upsert(EntityProfile(
            entity_type="item",
            name="灰烬图书馆",
            summary="同名不同类的物品。",
        ))
    entity_id = data["entity_profiles"][0]["id"]
    assert data["entity_profiles"][0]["entity_type"] == "location"

    result = update_player_record("entity", entity_id, {"name": "灰烬图书馆"})
    assert result["fields"]["name"] == "灰烬图书馆"


def test_update_entity_refreshes_timestamp():
    _seed_records()
    data = load_player_records()
    entity_id = data["entity_profiles"][0]["id"]
    runtime = get_campaign_runtime()
    with runtime.stores.entity() as store:
        current = store.get_by_id(entity_id)
        assert current is not None
        stale_updated_at = current.updated_at

    update_player_record("entity", entity_id, {"summary": "更新后的摘要。"})

    with runtime.stores.entity() as store:
        saved = store.get_by_id(entity_id)
    assert saved is not None
    assert saved.updated_at != stale_updated_at
    assert saved.updated_at > stale_updated_at


def test_update_player_record_validates_kind_and_id():
    with pytest.raises(ValueError, match="unknown record kind"):
        update_player_record("mystery", "id", {})
    with pytest.raises(ValueError, match="record_id"):
        update_player_record("npc", "", {})
    with pytest.raises(ValueError, match="payload"):
        update_player_record("npc", "some-id", None)


def test_player_records_routes_exposed():
    import inspect
    get_src = inspect.getsource(PlayerHandler.do_GET)
    post_src = inspect.getsource(PlayerHandler.do_POST)
    assert "/api/player/records" in get_src
    assert "/api/player/records/update" in post_src


def test_api_facade_exposes_records_functions():
    import app.api
    assert "load_player_records" in app.api.__all__
    assert "update_player_record" in app.api.__all__
    assert callable(app.api.load_player_records)
    assert callable(app.api.update_player_record)


def test_player_records_update_route_dispatches_by_http_method(monkeypatch):
    import app.api as api_module
    captured = {}

    def fake_update(kind, record_id, payload):
        captured["kind"] = kind
        captured["record_id"] = record_id
        captured["payload"] = payload
        return {"updated": True}

    monkeypatch.setattr(api_module, "_update_player_record", fake_update)

    body = json.dumps({
        "kind": "continuity",
        "record_id": "abc",
        "payload": {"subject": "新主题"},
    }).encode("utf-8")
    handler = object.__new__(player_server.PlayerHandler)
    handler.path = "/api/player/records/update"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    results = []
    handler._handle_player_json = lambda callback: results.append(callback())
    handler.do_POST()

    assert captured == {
        "kind": "continuity",
        "record_id": "abc",
        "payload": {"subject": "新主题"},
    }
    assert results == [{"updated": True}]
