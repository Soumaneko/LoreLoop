import io
import json

import pytest

from app import player_server
from app.domain.continuity.schemas import ContinuityItem
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.world_setting.schemas import WorldSetting
from app.player_server import PlayerHandler
from app.runtime.player_records import load_player_records, update_player_record
from app.runtime.session import get_campaign_runtime


def _seed_records():
    runtime = get_campaign_runtime()
    with runtime.stores.current_state() as store:
        store.save(CurrentState(
            scene=SceneState(scene_id="scene_1", location="老橡树镇"),
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
    with runtime.stores.npc_profile() as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师"],
            role="流浪木偶师",
            core_motivation="找回被窃的木偶。",
            hidden_secret="曾目睹一桩旧案。",
        ))
    with runtime.stores.world_setting() as store:
        store.save(WorldSetting(
            kind="location",
            name="灰烬图书馆",
            summary="一座封存旧时代档案的石砌图书馆。",
            public_facts=["图书馆对登记学者开放。"],
            hidden_facts=["地下层保存着未登记档案。"],
            created_turn=1,
        ))


def test_load_player_records_projects_continuity_npc_and_setting():
    _seed_records()
    data = load_player_records()

    assert set(data) >= {
        "maintenance_running",
        "continuity",
        "npc_profiles",
        "world_settings",
    }
    assert data["continuity"][0]["subject"] == "找回木偶"
    assert data["npc_profiles"][0]["public"]["name"] == "埃利奥"
    assert data["npc_profiles"][0]["director_only"]["hidden_secret"]
    assert data["world_settings"][0]["fields"]["name"] == "灰烬图书馆"


def test_update_continuity_preserves_status_and_normalizes_null():
    _seed_records()
    item = load_player_records()["continuity"][0]
    result = update_player_record(
        "continuity",
        item["id"],
        {"subject": "找回失窃木偶", "location": ""},
    )
    assert result["subject"] == "找回失窃木偶"
    assert result["location"] is None
    assert result["status"] == "active"


def test_update_npc_public_fields_preserves_director_fields_and_absence():
    _seed_records()
    runtime = get_campaign_runtime()
    npc_id = load_player_records()["npc_profiles"][0]["id"]
    with runtime.stores.npc_profile() as store:
        profile = store.get_by_id(npc_id)
        profile.absent_turns = 6
        store.upsert(profile)

    result = update_player_record(
        "npc",
        npc_id,
        {"role": "巡回木偶师", "aliases": ["木偶师", "  "]},
    )
    assert result["public"]["role"] == "巡回木偶师"
    assert result["public"]["aliases"] == ["木偶师"]
    assert result["director_only"]["hidden_secret"] == "曾目睹一桩旧案。"
    with runtime.stores.npc_profile() as store:
        assert store.get_by_id(npc_id).absent_turns == 6


def test_update_npc_rejects_director_fields_and_blank_name():
    _seed_records()
    npc_id = load_player_records()["npc_profiles"][0]["id"]
    with pytest.raises(ValueError, match="read-only"):
        update_player_record("npc", npc_id, {"hidden_secret": "改写"})
    with pytest.raises(ValueError, match="name must not be empty"):
        update_player_record("npc", npc_id, {"name": "  "})


def test_update_setting_edits_static_fields():
    _seed_records()
    setting_id = load_player_records()["world_settings"][0]["id"]
    result = update_player_record(
        "setting",
        setting_id,
        {
            "kind": "lore",
            "summary": "关于旧时代档案封存制度的通称。",
            "public_facts": ["学者常用这个词。", ""],
        },
    )
    assert result["fields"]["kind"] == "lore"
    assert result["fields"]["public_facts"] == ["学者常用这个词。"]


def test_update_setting_rejects_unknown_fields_and_missing_record():
    _seed_records()
    setting_id = load_player_records()["world_settings"][0]["id"]
    with pytest.raises(ValueError, match="Setting fields only"):
        update_player_record("setting", setting_id, {"aliases": ["旧称"]})
    with pytest.raises(ValueError, match="Setting not found"):
        update_player_record("setting", "missing", {"name": "无"})


def test_update_setting_rejects_duplicate_name():
    _seed_records()
    runtime = get_campaign_runtime()
    with runtime.stores.world_setting() as store:
        second = WorldSetting(
            kind="item",
            name="旧钥匙",
            summary="一把来源不明的铜钥匙。",
            public_facts=["钥匙表面有灼痕。"],
            created_turn=2,
        )
        store.save(second)
    with pytest.raises(ValueError, match="already exists"):
        update_player_record("setting", second.setting_id, {"name": "灰烬图书馆"})


def test_update_player_record_validates_kind_id_and_payload():
    with pytest.raises(ValueError, match="unknown record kind"):
        update_player_record("unknown", "id", {})
    with pytest.raises(ValueError, match="record_id"):
        update_player_record("npc", "", {})
    with pytest.raises(ValueError, match="payload"):
        update_player_record("npc", "some-id", None)


def test_player_records_routes_exposed():
    import inspect

    assert "/api/player/records" in inspect.getsource(PlayerHandler.do_GET)
    assert "/api/player/records/update" in inspect.getsource(PlayerHandler.do_POST)


def test_api_facade_exposes_records_functions():
    import app.api

    assert callable(app.api.load_player_records)
    assert callable(app.api.update_player_record)


def test_player_records_update_route_dispatches_by_http_method(monkeypatch):
    import app.api as api_module

    captured = {}

    def fake_update(kind, record_id, payload):
        captured.update(kind=kind, record_id=record_id, payload=payload)
        return {"updated": True}

    monkeypatch.setattr(api_module, "_update_player_record", fake_update)
    body = json.dumps({
        "kind": "setting",
        "record_id": "abc",
        "payload": {"summary": "新摘要"},
    }).encode("utf-8")
    handler = object.__new__(player_server.PlayerHandler)
    handler.path = "/api/player/records/update"
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    results = []
    handler._handle_player_json = lambda callback: results.append(callback())
    handler.do_POST()

    assert captured == {
        "kind": "setting",
        "record_id": "abc",
        "payload": {"summary": "新摘要"},
    }
    assert results == [{"updated": True}]
