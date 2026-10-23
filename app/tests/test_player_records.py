import io
import json
import pytest
from app.core.client_error import ClientValidationError
from app.domain.continuity.schemas import ContinuityItem
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.world_setting.schemas import WorldSetting
from app.player_server import PlayerHandler
from app.runtime.player_records import load_player_records, update_player_record
from app.runtime.session import get_campaign_runtime
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import WorldClock
from app.domain.director_event import DirectorEvent
from app.runtime.core_memory import save_core_memory_items
from app.runtime.settings import save_experience_mode


def _seed_records():
    runtime = get_campaign_runtime()
    with runtime.stores.current_state as store:
        store.save(CurrentState(
            scene=SceneState(scene_id="scene_1", location="老橡树镇"),
            player=PlayerState(name="莱尔"),
        ))
    with runtime.stores.continuity as store:
        with store.transaction():
            store.insert(ContinuityItem(
                kind="commitment",
                subject="找回木偶",
                premise="帮助埃利奥找回被窃的木偶。",
                current_state=None,
                entities=["埃利奥"],
                location="老橡树镇",
                created_turn=1,
                updated_turn=1,
            ))
    with runtime.stores.npc_profile as store:
        store.upsert(NPCProfile(
            name="埃利奥",
            aliases=["木偶师"],
            role="流浪木偶师",
            core_motivation="找回被窃的木偶。",
            hidden_secret="曾目睹一桩旧案。",
            last_scene_summary="他在灰烬图书馆当众修好了被窃的木偶。",
            last_scene_turn=3,
            name_hit_count=2,
        ))
    with runtime.stores.world_setting as store:
        store.save(WorldSetting(
            kind="location",
            name="灰烬图书馆",
            summary="一座封存旧时代档案的石砌图书馆。",
            public_facts=["图书馆对登记学者开放。"],
            hidden_facts=["地下层保存着未登记档案。"],
            created_turn=1,
            hit_count=2,
        ))


def test_load_player_records_projects_continuity_npc_and_setting(campaign_runtime):
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
    assert "director_only" not in data["npc_profiles"][0]
    assert data["world_settings"][0]["fields"]["name"] == "灰烬图书馆"
    assert "hidden_facts" not in data["world_settings"][0]["fields"]


def test_load_player_records_advanced_view_includes_director_and_hidden_fields(campaign_runtime):
    _seed_records()
    data = load_player_records(view="advanced")

    assert data["npc_profiles"][0]["director_only"]["hidden_secret"] == "曾目睹一桩旧案。"
    assert "source_director_npc_id" not in data["npc_profiles"][0]["director_only"]
    assert "created_turn" not in data["npc_profiles"][0]["director_only"]
    assert data["world_settings"][0]["fields"]["hidden_facts"] == ["地下层保存着未登记档案。"]
    assert "setting_id" not in data["world_settings"][0]["fields"]
    assert "created_turn" not in data["world_settings"][0]["fields"]


def test_load_player_records_general_view_filters_unappeared_records(campaign_runtime):
    _seed_records()
    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile as store:
        store.upsert(NPCProfile(
            name="柳千帆",
            role="镖师",
            core_motivation="押送一箱封条货物。",
            name_hit_count=0,
        ))
    with runtime.stores.world_setting as store:
        store.save(WorldSetting(
            kind="faction",
            name="北陆商盟",
            summary="掌控北境商路的商会联盟。",
            public_facts=["统一收取过路费。"],
            created_turn=2,
        ))

    data = load_player_records()
    npc_names = {item["public"]["name"] for item in data["npc_profiles"]}
    setting_names = {item["fields"]["name"] for item in data["world_settings"]}
    assert npc_names == {"埃利奥"}
    assert setting_names == {"灰烬图书馆"}

    advanced = load_player_records(view="advanced")
    assert {item["public"]["name"] for item in advanced["npc_profiles"]} == {
        "埃利奥", "柳千帆",
    }
    assert {item["fields"]["name"] for item in advanced["world_settings"]} == {
        "灰烬图书馆", "北陆商盟",
    }


def test_load_player_records_general_view_hides_unmentioned_npcs(campaign_runtime):
    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile as store:
        store.upsert(NPCProfile(
            name="沈栖舟",
            role="北境行商",
            source_director_npc_id="draft_shen",
            name_hit_count=0,
        ))
        store.upsert(NPCProfile(
            name="柳听澜",
            role="镖局掌柜",
            source_director_npc_id="draft_liu",
            name_hit_count=2,
        ))

    data = load_player_records()
    npc_names = {item["public"]["name"] for item in data["npc_profiles"]}
    assert "沈栖舟" not in npc_names
    assert "柳听澜" in npc_names

    advanced = load_player_records(view="advanced")
    assert {item["public"]["name"] for item in advanced["npc_profiles"]} == {
        "沈栖舟", "柳听澜",
    }


def test_load_player_records_rejects_unknown_view():
    with pytest.raises(ValueError, match="unknown records view"):
        load_player_records(view="secret")

def test_update_npc_public_fields_preserves_last_scene_record(campaign_runtime):
    _seed_records()
    runtime = get_campaign_runtime()
    npc_id = load_player_records(view="advanced")["npc_profiles"][0]["id"]
    # load 响应统一投影：general 视图的 public 记录携带只读 pair。
    loaded = load_player_records()["npc_profiles"][0]
    assert loaded["public"]["last_scene_summary"] == "他在灰烬图书馆当众修好了被窃的木偶。"
    assert loaded["public"]["last_scene_turn"] == 3
    # update 响应走同一投影，pair 仍然可见且未变。
    result = update_player_record(
        "npc",
        npc_id,
        {"role": "巡回木偶师"},
    )
    assert result["public"]["role"] == "巡回木偶师"
    assert result["public"]["last_scene_summary"] == "他在灰烬图书馆当众修好了被窃的木偶。"
    assert result["public"]["last_scene_turn"] == 3
    # 提交 last-scene pair 属于未知字段，被拒绝。
    with pytest.raises(ValueError, match="NPC general fields only"):
        update_player_record(
            "npc",
            npc_id,
            {"last_scene_summary": "篡改的摘要"},
        )
    with pytest.raises(ValueError, match="NPC general fields only"):
        update_player_record(
            "npc",
            npc_id,
            {"last_scene_turn": 0},
        )
    with runtime.stores.npc_profile as store:
        saved = store.get_by_id(npc_id)
        assert saved is not None
        assert saved.role == "巡回木偶师"
        assert saved.last_scene_summary == "他在灰烬图书馆当众修好了被窃的木偶。"
        assert saved.last_scene_turn == 3

def test_update_npc_rejects_director_fields_in_general_and_blank_name(campaign_runtime):
    _seed_records()
    npc_id = load_player_records(view="advanced")["npc_profiles"][0]["id"]
    with pytest.raises(ValueError, match="NPC general fields only"):
        update_player_record("npc", npc_id, {"hidden_secret": "改写"})
    with pytest.raises(ValueError, match="NPC advanced fields only"):
        update_player_record(
            "npc", npc_id, {"source_director_npc_id": "npc_x"}, view="advanced"
        )
    with pytest.raises(ClientValidationError, match="名称不能为空"):
        update_player_record("npc", npc_id, {"name": "  "})

def test_update_setting_rejects_hidden_facts_in_general_and_unknown_fields(campaign_runtime):
    _seed_records()
    setting_id = load_player_records(view="advanced")["world_settings"][0]["id"]
    with pytest.raises(ValueError, match="Setting general fields only"):
        update_player_record("setting", setting_id, {"hidden_facts": ["机密"]})
    with pytest.raises(ValueError, match="Setting advanced fields only"):
        update_player_record("setting", setting_id, {"related_setting_ids": ["s1"]}, view="advanced")
    with pytest.raises(ValueError, match="Setting not found"):
        update_player_record("setting", "missing", {"name": "无"})

def test_update_player_record_validates_kind_id_and_payload():
    with pytest.raises(ValueError, match="unknown record kind"):
        update_player_record("unknown", "id", {})
    with pytest.raises(ValueError, match="record_id"):
        update_player_record("npc", "", {})
    with pytest.raises(ValueError, match="payload"):
        update_player_record("npc", "some-id", None)


def test_update_npc_preserves_disclosure_level(campaign_runtime):
    _seed_records()
    runtime = get_campaign_runtime()
    npc_id = load_player_records(view="advanced")["npc_profiles"][0]["id"]
    with runtime.stores.npc_profile as store:
        profile = store.get_by_id(npc_id)
        profile.disclosure_level = "intimate"
        store.upsert(profile)

    result = update_player_record(
        "npc",
        npc_id,
        {"role": "巡回木偶师"},
    )
    assert result["public"]["role"] == "巡回木偶师"
    with runtime.stores.npc_profile as store:
        assert store.get_by_id(npc_id).disclosure_level == "intimate"


def test_record_validation_is_actionable_without_revealing_hidden_conflict(campaign_runtime):
    from app.core.client_error import ClientValidationError
    _seed_records()
    npc_id = load_player_records()["npc_profiles"][0]["id"]
    runtime = get_campaign_runtime()
    with runtime.stores.npc_profile as store:
        store.upsert(NPCProfile(name="未披露的真实身份", aliases=["冲突别名"], name_hit_count=0))
    with pytest.raises(ClientValidationError) as conflict:
        update_player_record("npc", npc_id, {"name": "冲突别名"})
    assert "冲突" in str(conflict.value)
    assert "未披露的真实身份" not in str(conflict.value)
    with pytest.raises(ClientValidationError, match="字段格式不合法"):
        update_player_record("npc", npc_id, {"known_abilities": [1]})
    with runtime.stores.npc_profile as store:
        assert store.get_by_id(npc_id).aliases == ["木偶师"]


def _player_request(path, payload=None):
    handler = object.__new__(PlayerHandler)
    handler.path = path
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status, message=None: setattr(handler, "status", status)
    handler.send_header = lambda *_args: None
    handler.end_headers = lambda: None
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    handler.headers = {"Content-Length": str(len(body))}
    handler.rfile = io.BytesIO(body)
    (handler.do_POST if payload is not None else handler.do_GET)()
    assert handler.status == 200, handler.wfile.getvalue()
    return json.loads(handler.wfile.getvalue())["data"]


def test_records_api_reads_selected_view_and_saves_edits(campaign_runtime):
    _seed_records()
    general = _player_request("/api/player/records")
    assert "hidden_facts" not in general["world_settings"][0]["fields"]
    advanced = _player_request("/api/player/records?view=advanced")
    setting = advanced["world_settings"][0]
    assert setting["fields"]["hidden_facts"] == ["地下层保存着未登记档案。"]
    _player_request("/api/player/records/update", {
        "kind": "setting", "record_id": setting["id"], "view": "advanced",
        "payload": {"summary": "玩家编辑的新摘要"},
    })
    updated = _player_request("/api/player/records?view=advanced")["world_settings"][0]
    assert updated["fields"]["summary"] == "玩家编辑的新摘要"
    assert updated["fields"]["hidden_facts"] == setting["fields"]["hidden_facts"]


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


@pytest.mark.parametrize("mode", ["player", "creator"])
def test_load_player_events_in_owner_modes(campaign_runtime, mode):
    _seed_event()
    save_experience_mode({"experience_mode": mode})
    payload = _player_request("/api/player/events")
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event["event_id"].startswith("director_event_")
    assert event["title"] == "旧桥封锁"
    assert event["stage"] == "hidden"
    assert event["deadline_clock"]["day"] >= 1
    assert event["created_turn"] == 1


def test_core_memory_can_be_edited_in_player_mode(campaign_runtime):
    _seed_core_memory()
    assert _player_request("/api/player/core-memory")["items"][0]["id"] == "base_rule"
    assert _player_request("/api/player/core-memory", {"items": []})["items"] == []
    assert _player_request("/api/player/core-memory")["items"] == []


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
