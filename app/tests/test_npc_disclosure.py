import json

import pytest

from app.domain.npc_profile.schemas import (
    DISCLOSED_FIELD_SETS,
    NPCProfile,
)
from app.runtime.sqlite import connect_sqlite, ensure_exact_schema_version


def _profile():
    return NPCProfile(
        name="林澈",
        aliases=["林医生"],
        role="社区医生",
        public_summary="诊所的坐堂医生。",
        appearance="常穿深色风衣。",
        personality_traits=["谨慎", "耐心"],
        known_abilities=["急救"],
        faction=None,
        attitude_to_player="克制冷淡。",
        social_style="克制而友善。",
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        encounter_potentials=["诊所求助"],
        director_notes="导演笔记。",
    )


def test_default_disclosure_level_is_remote():
    assert _profile().disclosure_level == "remote"


def test_disclosure_view_remote_drops_face_to_face_and_hidden_fields():
    view = _profile().disclosure_view()
    data = view.model_dump(mode="json")

    assert data["name"] == "林澈"
    assert data["role"] == "社区医生"
    assert data["social_style"] == "克制而友善。"
    assert data["attitude_to_player"] == "克制冷淡。"
    assert data["appearance"] is None
    assert data["personality_traits"] == []
    assert data["known_abilities"] == []
    assert data["core_motivation"] is None
    assert data["hidden_secret"] is None


def test_disclosure_view_face_to_face_adds_appearance_and_personality():
    data = _profile().disclosure_view("face_to_face").model_dump(mode="json")

    assert data["appearance"] == "常穿深色风衣。"
    assert data["personality_traits"] == ["谨慎", "耐心"]
    assert data["known_abilities"] == ["急救"]
    assert data["conflict_style"] == "先协商后行动。"
    assert data["core_motivation"] is None
    assert data["hidden_secret"] is None


def test_disclosure_view_intimate_reveals_everything():
    data = _profile().disclosure_view("intimate").model_dump(mode="json")

    assert data["core_motivation"] == "保护社区居民。"
    assert data["hidden_secret"] == "曾目击一宗未结案件。"
    assert data["encounter_potentials"] == ["诊所求助"]


def test_disclosure_view_uses_profile_level_when_not_specified():
    profile = _profile().model_copy(update={"disclosure_level": "intimate"})
    data = profile.disclosure_view().model_dump(mode="json")
    assert data["hidden_secret"] == "曾目击一宗未结案件。"


def test_disclosed_field_sets_cover_all_levels():
    assert DISCLOSED_FIELD_SETS["remote"] is not None
    assert DISCLOSED_FIELD_SETS["face_to_face"] is not None
    assert DISCLOSED_FIELD_SETS["intimate"] is None
    assert DISCLOSED_FIELD_SETS["remote"] < DISCLOSED_FIELD_SETS["face_to_face"]


def test_npc_profile_store_migrates_v3_to_v4(tmp_path):
    db_path = str(tmp_path / "npc_profiles.db")
    conn = connect_sqlite(db_path)
    conn.execute(
        "CREATE TABLE npc_profiles ("
        "id TEXT PRIMARY KEY, name TEXT NOT NULL, aliases_json TEXT NOT NULL, "
        "profile_json TEXT NOT NULL, source_scene_ids_json TEXT NOT NULL, "
        "confidence REAL NOT NULL, updated_at TEXT NOT NULL, "
        "active INTEGER NOT NULL, name_hit_count INTEGER NOT NULL, "
        "alias_hit_counts_json TEXT NOT NULL)"
    )
    profile = _profile().model_copy(
        update={"npc_id": "legacy", "disclosure_level": "remote"}
    )
    legacy_json = profile.model_dump(mode="json")
    legacy_json.pop("disclosure_level", None)
    conn.execute(
        "INSERT INTO npc_profiles VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy",
            "林澈",
            "[]",
            json.dumps(legacy_json, ensure_ascii=False),
            "[]",
            0.5,
            "",
            1,
            0,
            "{}",
        ),
    )
    conn.execute(
        "CREATE TABLE schema_versions ("
        "component TEXT PRIMARY KEY, version INTEGER NOT NULL, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.execute(
        "INSERT INTO schema_versions (component, version) VALUES "
        "('npc_profile', 3)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_npc_profiles_name "
        "ON npc_profiles(active, name)"
    )
    conn.commit()
    conn.close()

    from app.domain.npc_profile.store import NPCProfileStore

    store = NPCProfileStore(db_path)
    try:
        loaded = store.get_by_id("legacy")
        assert loaded is not None
        assert loaded.disclosure_level == "remote"
    finally:
        store.close()
