import pytest

from app.domain.continuity.schemas import ContinuityItem
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.current_state.schemas import WorldClock
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.schemas import WorldSetting
from app.runtime.campaign_migration import (
    LEGACY_DB_FILES,
    ensure_campaign_database,
)
from app.runtime.paths import CampaignPaths
from app.runtime.stores import (
    open_confirmed_history_store,
    open_continuity_store,
    open_current_state_store,
    open_director_event_store,
    open_director_log_store,
    open_director_npc_store,
    open_npc_profile_store,
    open_scene_summary_store,
    open_short_term_store,
    open_world_setting_store,
)
from app.runtime.uow import CampaignUnitOfWork


def _paths(tmp_path, name="save"):
    return CampaignPaths.from_data_dir(tmp_path / name)


def _seed_legacy(paths: CampaignPaths) -> None:
    store = open_current_state_store(paths.data_dir / "current_state.db")
    store.save(
        CurrentState(
            scene=SceneState(scene_id="sc1", location="北境"),
            player=PlayerState(name="旅人"),
        )
    )
    store.close()

    store = open_continuity_store(paths.data_dir / "continuity.db")
    store.insert(
        ContinuityItem(
            kind="commitment",
            subject="找回失落的古籍",
            premise="古籍被埋在北境废墟之下",
            created_turn=1,
            updated_turn=1,
        )
    )
    store.close()

    store = open_short_term_store(paths.data_dir / "short_term.db")
    store.add_turn(
        scene_id="sc1",
        turn_index=1,
        user_input="北境风沙很大",
        scene_text="沙尘蔽日。",
    )
    store.close()

    store = open_scene_summary_store(paths.data_dir / "scene_summaries.db")
    store.save_one(
        SceneSummary(
            level=1,
            content="初遇",
            turn_start=1,
            turn_end=2,
        )
    )
    store.close()

    store = open_confirmed_history_store(paths.data_dir / "confirmed_history.db")
    store.upsert_turn(
        scene_id="sc1",
        turn_index=1,
        user_input="我向南走",
        scene_text="一段场景。\n\n第二段。",
    )
    store.close()

    store = open_npc_profile_store(paths.data_dir / "npc_profiles.db")
    store.upsert(
        NPCProfile(
            npc_id="n1",
            name="林霜",
            aliases=["林姑娘"],
            last_known_location="北境客栈",
        )
    )
    store.close()

    store = open_director_event_store(paths.data_dir / "director_events.db")
    store.save(
        DirectorEvent(
            event_id="e1",
            role="arc",
            title="废墟异动",
            subject="发现废墟入口",
            intent="引出主线",
            method="场景线索",
            macro_intent="推进探索",
            deadline_clock=WorldClock(day=1, phase="黄昏"),
            created_turn=1,
        )
    )
    store.close()

    store = open_director_npc_store(paths.data_dir / "director_npcs.db")
    store.save(
        DirectorNPCDraft(
            draft_id="d1",
            core_motivation="守护古籍",
            hidden_secret="古籍是空的",
            conflict_style="回避",
            social_style="冷淡",
            role="守墓人",
            personality_trait="沉默",
            appearance="灰袍老者",
            name="无名守墓人",
            status="prepared",
            created_turn=1,
        )
    )
    store.close()

    store = open_director_log_store(paths.data_dir / "director_log.db")
    store.save_entries(
        [{"turn_index": 1, "phase": "confirmation", "reasoning": "引向废墟"}]
    )
    store.close()

    store = open_world_setting_store(paths.data_dir / "world_settings.db")
    store.save(
        WorldSetting(
            setting_id="w1",
            kind="location",
            name="北境",
            summary="终年风沙的边境之地",
            public_facts=["风沙"],
            created_turn=1,
        )
    )
    store.close()


def test_created_empty_save_has_all_schemas(tmp_path):
    paths = _paths(tmp_path)
    assert ensure_campaign_database(paths) == "created"
    assert paths.campaign_db.is_file()
    with CampaignUnitOfWork(paths) as uow:
        assert uow.current_state.load() is None
        assert uow.continuity.list_active() == []
        assert uow.confirmed_history.turn_count() == 0
        assert uow.npc_profile.list_all() == []
        assert uow.world_setting.list_all() == []
        assert uow.director_event.list_all() == []
        assert uow.director_npc.list_all() == []
    assert ensure_campaign_database(paths) == "validated"


def test_legacy_save_migrates_atomically(tmp_path):
    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)
    _seed_legacy(paths)
    legacy_present = {
        name
        for name in LEGACY_DB_FILES
        if (paths.data_dir / name).is_file()
    }
    assert legacy_present == set(LEGACY_DB_FILES)

    assert ensure_campaign_database(paths) == "migrated"

    with CampaignUnitOfWork(paths) as uow:
        assert uow.current_state.load().scene.location == "北境"
        assert uow.continuity.list_active()[0].subject == "找回失落的古籍"
        assert uow.scene_summary.list_active()[0].content == "初遇"
        assert uow.confirmed_history.turn_count() == 1
        assert uow.confirmed_history.fts_search("我向南走") != {}
        assert uow.npc_profile.get_by_name("林霜").name == "林霜"
        assert uow.director_event.get_by_id("e1").title == "废墟异动"
        assert uow.director_npc.get_by_id("d1").name == "无名守墓人"
        assert uow.director_log.count() == 1
        assert uow.world_setting.get_by_id("w1").name == "北境"
        assert uow.continuity.fts_search(["失落的古籍"]) == {
            uow.continuity.list_active()[0].id
        }

    for name in LEGACY_DB_FILES:
        assert not (paths.data_dir / name).exists()
    archive = paths.data_dir / ".runtime" / "legacy-db-v1"
    assert archive.is_dir()
    assert len(list(archive.iterdir())) >= len(LEGACY_DB_FILES)

    assert ensure_campaign_database(paths) == "validated"


def test_existing_campaign_db_wins(tmp_path):
    paths = _paths(tmp_path)
    assert ensure_campaign_database(paths) == "created"
    with CampaignUnitOfWork(paths, write=True) as uow:
        uow.current_state.save(
            CurrentState(
                scene=SceneState(scene_id="fresh", location="新房"),
                player=PlayerState(name="旅人"),
            )
        )
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    _seed_legacy(paths)
    assert ensure_campaign_database(paths) == "validated"
    with CampaignUnitOfWork(paths) as uow:
        assert uow.current_state.load().scene.location == "新房"


def test_partial_legacy_archive_still_migrates(tmp_path):
    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)
    store = open_world_setting_store(paths.data_dir / "world_settings.db")
    store.save(
        WorldSetting(
            setting_id="w1",
            kind="location",
            name="北境",
            summary="终年风沙",
            public_facts=["风沙"],
            created_turn=1,
        )
    )
    store.close()
    assert ensure_campaign_database(paths) == "migrated"
    with CampaignUnitOfWork(paths) as uow:
        assert uow.world_setting.get_by_id("w1").name == "北境"
        assert uow.continuity.list_active() == []

def test_validate_rejects_incomplete_schema_only_db(tmp_path):
    import sqlite3

    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)
    db_path = paths.campaign_db
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE schema_versions (component TEXT, version INTEGER)")
        for component, version in [
            ("current_state", 4),
            ("continuity", 3),
            ("short_term_memory", 5),
            ("scene_summary", 1),
            ("confirmed_history", 1),
            ("npc_profile", 9),
            ("director_event", 11),
            ("director_log", 1),
            ("director_npc", 5),
            ("world_setting", 12),
        ]:
            conn.execute(
                "INSERT INTO schema_versions(component, version) VALUES (?, ?)",
                (component, version),
            )
        conn.commit()
    with pytest.raises(RuntimeError, match="missing required business tables"):
        ensure_campaign_database(paths)


def test_migration_failure_cleans_temp_database(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)
    store = open_world_setting_store(paths.data_dir / "world_settings.db")
    store.save(
        WorldSetting(
            setting_id="w1",
            kind="location",
            name="北境",
            summary="终年风沙",
            public_facts=["风沙"],
            created_turn=1,
        )
    )
    store.close()

    from app.runtime import campaign_migration

    original = campaign_migration._build_campaign_schema

    def boom(conn):
        raise RuntimeError("injected bootstrap failure")

    monkeypatch.setattr(campaign_migration, "_build_campaign_schema", boom)
    with pytest.raises(RuntimeError, match="injected bootstrap failure"):
        ensure_campaign_database(paths)

    assert list(paths.data_dir.glob("campaign.db.*.tmp")) == []
