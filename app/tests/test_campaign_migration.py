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
    ensure_campaign_database,
)
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork


def _paths(tmp_path, name="save"):
    return CampaignPaths.from_data_dir(tmp_path / name)


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
    # Even with a stray legacy file, validated campaign wins and no migration is attempted
    (paths.data_dir / "current_state.db").write_text("legacy", encoding="utf-8")
    (paths.data_dir / ".runtime").mkdir(exist_ok=True)
    journal = paths.data_dir / ".runtime" / "director_mutation.json"
    journal.write_text("{}")
    assert ensure_campaign_database(paths) == "validated"
    assert journal.read_text() == "{}"
    with CampaignUnitOfWork(paths) as uow:
        assert uow.current_state.load().scene.location == "新房"


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


def test_initialization_failure_cleans_temp_database(tmp_path, monkeypatch):
    paths = _paths(tmp_path)
    paths.data_dir.mkdir(parents=True)

    from app.runtime import campaign_migration

    def boom(conn):
        raise RuntimeError("injected bootstrap failure")

    monkeypatch.setattr(campaign_migration, "_build_campaign_schema", boom)
    with pytest.raises(RuntimeError, match="injected bootstrap failure"):
        ensure_campaign_database(paths)

    assert list(paths.data_dir.glob("campaign.db.*.tmp")) == []


@pytest.mark.parametrize("module_name", ["campaign_migration", "snapshots"])
def test_file_sync_uses_writable_handle_without_changing_contents(tmp_path, monkeypatch, module_name):
    import importlib
    import os

    module = importlib.import_module(f"app.runtime.{module_name}")
    target = tmp_path / "staged.db"
    original = b"staged database content"
    target.write_bytes(original)
    real_fsync = os.fsync
    synced = []

    def require_writable_descriptor(fd):
        # Windows fsync requires write access; a zero-byte write checks that portably.
        os.write(fd, b"")
        real_fsync(fd)
        synced.append(fd)

    monkeypatch.setattr(os, "fsync", require_writable_descriptor)
    module._fsync_file(target)
    assert len(synced) == 1
    assert target.read_bytes() == original


def test_directory_sync_skips_platform_without_directory_open_flag(tmp_path, monkeypatch):
    import os
    from app.runtime import snapshots

    monkeypatch.delattr(os, "O_DIRECTORY", raising=False)
    snapshots._fsync_directory(tmp_path)
