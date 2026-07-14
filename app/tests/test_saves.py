from types import SimpleNamespace

import pytest

from app.domain.current_state import PlayerState, make_default_state
from app.runtime.paths import CampaignPaths, PROJECT_ROOT, SAVES_DIR, validate_save_name
from app.runtime.saves import SaveManager
from app.runtime.session import CampaignRuntime


@pytest.mark.parametrize(
    "name",
    ["", " ", ".", "..", ".hidden", "../escape", "a/b", "a\\b", "bad\0name"],
)
def test_validate_save_name_rejects_unsafe_folder_names(name):
    with pytest.raises(ValueError):
        validate_save_name(name)


def test_default_saves_directory_is_at_project_root():
    assert SAVES_DIR == PROJECT_ROOT / "saves"


def test_save_manager_creates_lists_switches_and_isolates_state(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        alpha = manager.create("alpha")
        assert alpha.active is True
        with runtime.stores.current_state() as store:
            store.save(
                make_default_state().model_copy(
                    update={"player": PlayerState(name="Alpha")}
                )
            )

        beta = manager.create("beta")
        assert beta.active is True
        with runtime.stores.current_state() as store:
            store.save(
                make_default_state().model_copy(
                    update={"player": PlayerState(name="Beta")}
                )
            )

        manager.switch("alpha")
        with runtime.stores.current_state() as store:
            assert store.load().player.name == "Alpha"

        assert manager.active_file.read_text(encoding="utf-8") == "alpha"
        assert [save.name for save in manager.list()] == ["alpha", "beta", "default"]
        assert manager.payload()["active"] == "alpha"
    finally:
        runtime.close()


def test_switch_requires_explicitly_discarding_pending_content(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        manager.create("other", activate=False)
        runtime.pending_turns.add(
            SimpleNamespace(turn_id="pending", group_id="g", version_index=1)
        )

        with pytest.raises(RuntimeError, match="pending content"):
            manager.switch("other")

        assert runtime.campaign_id == "default"
        assert runtime.pending_turns.get("pending") is not None

        manager.switch("other", discard_pending=True)

        assert runtime.campaign_id == "other"
        assert runtime.pending_turns.get("pending") is None
    finally:
        runtime.close()


def test_save_manager_ignores_hidden_directories_and_symlinks(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        (root / ".internal").mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / "linked").symlink_to(outside, target_is_directory=True)

        assert [save.name for save in manager.list()] == ["default"]
        with pytest.raises(ValueError, match="not found"):
            manager.switch("linked")
    finally:
        runtime.close()


def test_default_save_imports_legacy_data_once_without_removing_source(tmp_path):
    root = tmp_path / "saves"
    legacy = tmp_path / "data"
    legacy.mkdir()
    (legacy / "core_memory.json").write_text('{"items": []}', encoding="utf-8")
    (legacy / "settings.json").write_text('{"difficulty": "普通"}', encoding="utf-8")
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    try:
        SaveManager(root, runtime=runtime, legacy_data_dir=legacy)

        assert (root / "default" / "core_memory.json").read_text(encoding="utf-8") == '{"items": []}'
        assert (root / "default" / "settings.json").is_file()
        assert (root / "default" / ".legacy_imported").is_file()
        assert (legacy / "core_memory.json").is_file()

        (legacy / "settings.json").write_text('{"difficulty": "简单"}', encoding="utf-8")
        SaveManager(root, runtime=runtime, legacy_data_dir=legacy)
        assert "普通" in (root / "default" / "settings.json").read_text(encoding="utf-8")
    finally:
        runtime.close()


def test_save_manager_imports_missing_app_saves_without_overwriting(tmp_path):
    root = tmp_path / "saves"
    legacy_saves = tmp_path / "app" / "saves"
    (legacy_saves / "default").mkdir(parents=True)
    (legacy_saves / "default" / "core_memory.json").write_text(
        '{"source": "legacy"}', encoding="utf-8"
    )
    (legacy_saves / "another").mkdir()
    (legacy_saves / "another" / "settings.json").write_text(
        '{"difficulty": "普通"}', encoding="utf-8"
    )
    (root / "default").mkdir(parents=True)
    (root / "default" / "core_memory.json").write_text(
        '{"source": "root"}', encoding="utf-8"
    )
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    try:
        SaveManager(
            root,
            runtime=runtime,
            legacy_saves_dir=legacy_saves,
            legacy_data_dir=tmp_path / "missing-data",
        )

        assert "root" in (root / "default" / "core_memory.json").read_text(
            encoding="utf-8"
        )
        assert (root / "another" / "settings.json").is_file()
        assert (root / ".app_saves_imported").is_file()
        assert (legacy_saves / "another" / "settings.json").is_file()
        assert (root / ".active_save").read_text(encoding="utf-8") == "default"
    finally:
        runtime.close()
