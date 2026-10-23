import pytest
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
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
    with CampaignUnitOfWork(paths, write=True) as uow:
        uow.current_state.save(CurrentState(
            scene=SceneState(scene_id="fresh", location="新房"),
            player=PlayerState(name="旅人"),
        ))
    assert ensure_campaign_database(paths) == "validated"
    with CampaignUnitOfWork(paths) as uow:
        assert uow.current_state.load().scene.location == "新房"


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
