from app.tests.factories.stores import open_world_setting_store
import sqlite3

import pytest
from pydantic import ValidationError

from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.world_setting import (
    SettingCreateOutput,
    WorldSetting,
    WorldSettingCreator,
    WorldSettingStore,
)


def _setting(**updates) -> WorldSetting:
    data = {
        "kind": "faction",
        "name": "听潮院",
        "summary": "沿海城镇间共享航路情报的松散组织。",
        "public_facts": ["成员多为船主与领航员。"],
        "hidden_facts": ["核心议事人刻意隐瞒一条废弃航道。"],
        "created_turn": 3,
    }
    data.update(updates)
    return WorldSetting(**data)


class _Gateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
            kwargs["on_attempt_start"](1)
        result = kwargs["output_schema"].model_validate(self.response)
        if "output_validator" in kwargs and kwargs["output_validator"] is not None:
            kwargs["output_validator"](result)
        return result


def test_setting_kind_is_restricted_to_four_general_categories():
    assert set(WorldSetting.model_json_schema()["properties"]["kind"]["enum"]) == {
        "faction",
        "location",
        "item",
        "lore",
    }
    with pytest.raises(ValidationError):
        _setting(kind="organization")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "设" * 11),
        ("summary", "设" * 101),
        ("public_facts", ["设" * 101]),
        ("hidden_facts", ["设" * 101]),
    ],
)
def test_setting_text_fields_enforce_compact_limits(field, value):
    with pytest.raises(ValidationError):
        _setting(**{field: value})


def test_setting_reference_view_omits_facts():
    setting = _setting()
    assert setting.reference_view() == {
        "setting_id": setting.setting_id,
        "kind": setting.kind,
        "name": setting.name,
        "summary": setting.summary,
    }


def test_setting_dedup_view_omits_id_and_facts():
    setting = _setting()
    assert setting.dedup_view() == {
        "kind": setting.kind,
        "name": setting.name,
        "summary": setting.summary,
    }


def test_static_setting_store_round_trip_and_rejects_duplicate_name():
    store = open_world_setting_store()
    setting = _setting()
    store.save(setting)

    assert store.get_by_id(setting.setting_id) == setting
    with pytest.raises(ValueError, match="already exists"):
        store.save(_setting(name=" 听 潮院 "))
    store.close()


def test_setting_store_save_many_persists_all_atomically():
    store = open_world_setting_store()
    ids = store.save_many([
        _setting(name="北港", kind="location"),
        _setting(name="夜航司", kind="faction"),
    ])
    assert len(ids) == 2
    assert len(store.list_all()) == 2
    store.close()


def test_setting_store_save_many_rolls_back_entire_batch_on_collision():
    store = open_world_setting_store()
    store.save(_setting())
    first = _setting(name="北港", kind="location")
    second = _setting(name=" 北港 ", kind="lore")

    with pytest.raises(ValueError, match="already exists"):
        store.save_many([first, second])

    assert store.get_by_id(first.setting_id) is None
    assert store.get_by_id(second.setting_id) is None
    assert len(store.list_all()) == 1
    store.close()


def test_setting_store_record_mention_increments_hit_count():
    store = open_world_setting_store()
    setting = _setting()
    store.save(setting)

    assert store.get_by_id(setting.setting_id).hit_count == 0
    assert store.record_mention(setting.setting_id) is True
    assert store.record_mention(setting.setting_id) is True
    assert store.get_by_id(setting.setting_id).hit_count == 2
    assert store.record_mention("setting_missing") is False
    store.close()


def test_setting_store_migrates_v11_to_v12_adding_hit_count(tmp_path):
    from app.runtime.sqlite import SUPPORTED_SCHEMA_VERSIONS

    assert SUPPORTED_SCHEMA_VERSIONS["world_setting"] == 12
    db_path = tmp_path / "world_settings.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE world_settings (
            setting_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            normalized_name TEXT NOT NULL UNIQUE,
            summary TEXT NOT NULL,
            public_facts_json TEXT NOT NULL,
            hidden_facts_json TEXT NOT NULL,
            related_setting_ids_json TEXT NOT NULL,
            created_turn INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE schema_versions (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.execute(
        "INSERT INTO schema_versions(component, version) VALUES ('world_setting', 11)"
    )
    conn.execute(
        """
        INSERT INTO world_settings (
            setting_id, kind, name, normalized_name, summary,
            public_facts_json, hidden_facts_json, related_setting_ids_json,
            created_turn, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "setting_v11",
            "location",
            "灰烬图书馆",
            "灰烬图书馆",
            "一座石砌图书馆。",
            '["图书馆对登记学者开放。"]',
            "[]",
            "[]",
            1,
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
        ),
    )
    conn.commit()
    conn.close()

    store = open_world_setting_store(str(db_path))
    setting = store.get_by_id("setting_v11")
    assert setting is not None
    assert setting.hit_count == 0
    assert store.record_mention("setting_v11") is True
    assert store.get_by_id("setting_v11").hit_count == 1

    with sqlite3.connect(db_path) as check:
        version = check.execute(
            "SELECT version FROM schema_versions WHERE component = 'world_setting'"
        ).fetchone()
    assert int(version[0]) == 12
    store.close()


def test_setting_store_requires_existing_explicit_relations():
    store = open_world_setting_store()
    with pytest.raises(ValueError, match="unknown related"):
        store.save(_setting(related_setting_ids=["setting_missing"]))
    store.close()


def test_creator_freely_builds_complete_setting_from_limited_guidance():
    gateway = _Gateway({
        "name": "听潮院",
        "summary": "沿海城镇间共享航路情报的松散组织。",
        "public_facts": ["成员多为船主与领航员。"],
        "hidden_facts": ["核心议事人刻意隐瞒一条废弃航道。"],
    })
    core_memory = CoreMemory(items=[CoreMemoryItem(
        id="world",
        kind="world_invariant",
        strength="hard_constraint",
        source="player",
        content="这是一个以航海贸易为主的人类世界。",
    )])

    setting = WorldSettingCreator(gateway).create(
        kind="faction",
        guidance="创建一个调查异常航迹的组织",
        core_memory=core_memory,
        existing_settings=[_setting(name="潮讯社"), _setting(name="远帆会")],
        turn_index=3,
    )

    assert setting.name == "听潮院"
    assert setting.kind == "faction"
    assert gateway.calls[0]["temperature"] == 0.9
    assert (
        gateway.calls[0]["input_data"]["guidance"]
        == "创建一个调查异常航迹的组织"
    )
    existing_json = gateway.calls[0]["input_data"]["existing_settings_json"]
    assert "潮讯社" in existing_json
    assert "远帆会" in existing_json
    assert "public_facts" not in existing_json
    assert "hidden_facts" not in existing_json
    assert "related_settings_json" not in gateway.calls[0]["input_data"]


def _premise_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world_identity",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content="当前世界名为「大乾江湖」，世界风格为：武侠\n能力体系为：武学",
        ),
        CoreMemoryItem(
            id="core_player_identity",
            kind="protagonist_invariant",
            strength="hard_constraint",
            source="player",
            content="主角是顾平生，男性，21岁。",
        ),
        CoreMemoryItem(
            id="core_player_abilities",
            kind="ability_rule",
            strength="hard_constraint",
            source="player",
            content="主角的特殊能力：\n- 执念拓印：应承未了心愿即立因果契约",
        ),
        CoreMemoryItem(
            id="core_story_expectation",
            kind="narrative_preference",
            strength="strong_preference",
            source="player",
            content="玩家期望：以了愿客的身份浪迹天涯，白嫖天下绝学。",
        ),
        CoreMemoryItem(
            id="core_public_power_baseline",
            kind="ability_rule",
            strength="hard_constraint",
            source="campaign_setup",
            content="公开力量来源：武学\n社会普及率：较为常见",
        ),
    ])


def _open_foundation():
    from app.domain.opening_foundation import OpeningFoundation

    return OpeningFoundation.model_validate({
        "version": 1,
        "starting_location": {
            "name": "临江渡口",
            "description": "大乾东南水路要冲。",
        },
        "protagonist_opening_situation": "顾平生在酒楼落座。",
        "initial_resources": {
            "inventory": [],
            "money": 10,
            "fixed_assets": [],
        },
    })


def test_world_setting_creator_receives_only_world_premise():
    gateway = _Gateway({
        "name": "临江渡口",
        "summary": "大乾东南水路枢纽。",
        "public_facts": ["码头由漕帮管理。"],
    })

    WorldSettingCreator(gateway).create(
        kind="location",
        guidance="临江渡口——水路枢纽",
        core_memory=_premise_core_memory(),
        existing_settings=[],
        turn_index=0,
    )

    sent = gateway.calls[0]["input_data"]["core_memory_json"]
    assert "顾平生" not in sent
    assert "执念拓印" not in sent
    assert "了愿客" not in sent
    assert "大乾江湖" in sent
    assert "公开力量来源" in sent


def test_opening_setting_agent_receives_only_world_premise():
    from app.domain.opening_setting import OpeningSettingAgent

    gateway = _Gateway({
        "specs": [
            {"kind": "faction", "setting_description": "掌控商路的商会。"},
            {"kind": "location", "setting_description": "一座贸易港。"},
        ],
    })

    OpeningSettingAgent(gateway).generate(
        foundation=_open_foundation(),
        core_memory=_premise_core_memory(),
    )

    sent = gateway.calls[0]["input_data"]["core_memory_json"]
    assert "顾平生" not in sent
    assert "执念拓印" not in sent
    assert "了愿客" not in sent
    assert "大乾江湖" in sent
    assert "公开力量来源" in sent


def test_setting_requires_concrete_non_overlapping_facts():
    with pytest.raises(ValidationError):
        SettingCreateOutput(name="空壳", summary="没有事实。")
    with pytest.raises(ValidationError):
        SettingCreateOutput(
            name="冲突",
            summary="事实冲突。",
            public_facts=["同一事实"],
            hidden_facts=["同一事实"],
        )


def test_store_rejects_previous_schema_instead_of_migrating_it(tmp_path):
    db_path = tmp_path / "world_settings.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE world_settings (setting_id TEXT PRIMARY KEY, label TEXT)"
    )
    conn.commit()
    conn.close()

    with pytest.raises(RuntimeError, match="static Setting contract"):
        open_world_setting_store(str(db_path))


def test_setting_domain_has_no_extraction_or_maintenance_layer():
    from pathlib import Path

    names = {path.name for path in Path("app/domain/world_setting").glob("*.py")}
    assert names == {"__init__.py", "creator.py", "schemas.py", "store.py"}


def test_setting_appears_in_text_strips_surrounding_whitespace():
    from app.domain.world_setting.schemas import setting_appears_in_text

    folded = "莱尔走进灰烬图书馆。".casefold()
    assert setting_appears_in_text("灰烬图书馆", folded) is True
    assert setting_appears_in_text("  灰烬图书馆  ", folded) is True


def test_setting_appears_in_text_single_char_and_empty_boundaries():
    from app.domain.world_setting.schemas import setting_appears_in_text

    folded = "知道大道说道。".casefold()
    assert setting_appears_in_text("道", folded) is False
    assert setting_appears_in_text("大道", folded) is True
    assert setting_appears_in_text("", folded) is False
    assert setting_appears_in_text("不存在", folded) is False


def test_setting_appears_in_text_preserves_unicode_single_char_rule():
    from app.domain.world_setting.schemas import setting_appears_in_text

    folded = "正午的太阳把桥影投在河面上。".casefold()
    assert setting_appears_in_text("ß", "大桥下出现 SS 字样。".casefold()) is False
    assert setting_appears_in_text("ss", "大桥下出现 SS 字样。".casefold()) is True
    assert setting_appears_in_text("道", "知道大道说道。".casefold()) is False