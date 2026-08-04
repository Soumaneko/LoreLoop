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
        return kwargs["output_schema"].model_validate(self.response)


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
    store = WorldSettingStore()
    setting = _setting()
    store.save(setting)

    assert store.get_by_id(setting.setting_id) == setting
    with pytest.raises(ValueError, match="already exists"):
        store.save(_setting(name=" 听 潮院 "))
    store.close()


def test_setting_store_save_many_persists_all_atomically():
    store = WorldSettingStore()
    ids = store.save_many([
        _setting(name="北港", kind="location"),
        _setting(name="夜航司", kind="faction"),
    ])
    assert len(ids) == 2
    assert len(store.list_all()) == 2
    store.close()


def test_setting_store_save_many_rolls_back_entire_batch_on_collision():
    store = WorldSettingStore()
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
    store = WorldSettingStore()
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

    store = WorldSettingStore(str(db_path))
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
    store = WorldSettingStore()
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
        WorldSettingStore(str(db_path))


def test_setting_domain_has_no_extraction_or_maintenance_layer():
    from pathlib import Path

    names = {path.name for path in Path("app/domain/world_setting").glob("*.py")}
    assert names == {"__init__.py", "creator.py", "schemas.py", "store.py"}
