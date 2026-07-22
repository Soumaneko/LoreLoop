import threading
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
def test_validate_save_name_rejects_unsafe_display_names(name):
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

        manager.switch(alpha.save_id)
        with runtime.stores.current_state() as store:
            assert store.load().player.name == "Alpha"

        assert manager.active_file.read_text(encoding="utf-8") == alpha.save_id
        assert [save.name for save in manager.list()] == ["alpha", "beta", "默认存档"]
        assert manager.payload()["active"] == alpha.save_id
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
        other = manager.create("other", activate=False)
        original_id = manager.active_id
        runtime.pending_turns.add(
            SimpleNamespace(turn_id="pending", group_id="g", version_index=1)
        )

        with pytest.raises(RuntimeError, match="pending content"):
            manager.switch(other.save_id)

        assert runtime.campaign_id == original_id
        assert runtime.pending_turns.get("pending") is not None

        manager.switch(other.save_id, discard_pending=True)

        assert runtime.campaign_id == other.save_id
        assert runtime.pending_turns.get("pending") is None
    finally:
        runtime.close()


def test_create_does_not_leave_directory_when_operation_is_active(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    started = threading.Event()
    release = threading.Event()

    def stream():
        started.set()
        assert release.wait(timeout=2)
        yield {"step": "finished", "data": {}}

    try:
        operation_id = runtime.operations.submit_stream("generation", stream)
        assert started.wait(timeout=2)
        with pytest.raises(RuntimeError, match="active operation"):
            manager.create("should-not-exist")
        assert not (root / "should-not-exist").exists()
        release.set()
        runtime.operations.wait(operation_id, timeout=2)
    finally:
        release.set()
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

        assert [save.name for save in manager.list()] == ["默认存档"]
        with pytest.raises(ValueError, match="canonical UUID"):
            manager.switch("linked")
    finally:
        runtime.close()


def test_rename_copy_export_import_and_delete_are_identity_safe(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "bootstrap",
        paths=CampaignPaths.from_data_dir(tmp_path / "bootstrap"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        original = manager.rename(manager.active_id, "原始旅程")
        original_dir = root / original.save_id
        with runtime.stores.current_state() as store:
            store.save(
                make_default_state().model_copy(
                    update={"player": PlayerState(name="甲")}
                )
            )

        copied = manager.copy(original.save_id)
        assert copied.name == "原始旅程 - 副本"
        assert copied.save_id != original.save_id
        assert (root / copied.save_id).is_dir()
        manager.switch(copied.save_id)
        with runtime.stores.current_state() as store:
            assert store.load().player.name == "甲"

        archive = tmp_path / "journey.zip"
        manager.export_to(copied.save_id, archive)
        imported = manager.import_from(archive, "导入旅程")
        assert imported.save_id not in {original.save_id, copied.save_id}
        assert imported.protagonist_name == "甲"

        manager.delete(
            copied.save_id,
            confirmation_name=copied.name,
        )
        assert not (root / copied.save_id).exists()
        assert original_dir.is_dir()
    finally:
        runtime.close()


def test_delete_active_switches_to_explicit_replacement(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "bootstrap",
        paths=CampaignPaths.from_data_dir(tmp_path / "bootstrap"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        first = manager.rename(manager.active_id, "第一份")
        second = manager.create("第二份", activate=False)

        manager.delete(
            first.save_id,
            confirmation_name="第一份",
            replacement_save_id=second.save_id,
        )

        assert manager.active_id == second.save_id
        assert manager.active_file.read_text(encoding="utf-8") == second.save_id
        assert [item.name for item in manager.list()] == ["第二份"]
    finally:
        runtime.close()
