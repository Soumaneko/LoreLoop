import threading

import pytest

from app.domain.core_memory import CoreMemory, InitConfig, build_initial_current_state
from app.domain.current_state import PlayerState, make_default_state
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.runtime.app_paths import APP_PATHS
from app.runtime.paths import (
    ACTIVE_SAVE_FILE,
    APP_CONFIG_DIR,
    CampaignPaths,
    SAVES_DIR,
    validate_save_name,
)
from app.runtime.saves import SaveManager
from app.runtime.session import CampaignRuntime, PendingOpening, PendingTurn
from app.runtime.turn_history import CampaignHistory


def _pending_turn(
    *,
    turn_id: str,
    group_id: str,
    version_index: int,
    state_before,
) -> PendingTurn:
    context = NarrativeComposerContext(
        user_input="推开门",
        current_state=state_before,
        turn_resolution=TurnResolutionResult(),
        core_memory=CoreMemory(items=[]),
    )
    return PendingTurn(
        turn_id=turn_id,
        group_id=group_id,
        version_index=version_index,
        user_input="推开门",
        difficulty="普通",
        max_chars=900,
        experience_mode="player",
        state_before=state_before,
        context=context,
        outline=PlotOutlineResult(
            planning_reasoning="推进当前行动。",
            scene_stopping_point="门被推开。",
        ),
        scene_result=SceneWriterResult(scene_text=f"待确认版本 {version_index}"),
        turn_index=1,
    )


def test_pending_scene_edit_survives_runtime_restart(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "pending-edit")
    runtime = CampaignRuntime("pending-edit", paths=paths, max_workers=1)
    try:
        pending = _pending_turn(
            turn_id="turn-1",
            group_id="group-1",
            version_index=1,
            state_before=make_default_state(),
        )
        runtime.pending_turns.add(pending)

        edited = runtime.save_pending_scene_edit("turn-1", "玩家保存的正文")

        assert edited == {
            "turn_id": "turn-1",
            "scene_text": "待确认版本 1",
            "saved_scene_text": "玩家保存的正文",
        }
    finally:
        runtime.close()

    restarted = CampaignRuntime("pending-edit", paths=paths, max_workers=1)
    try:
        restored = restarted.pending_turns.get("turn-1")
        assert restored is not None
        assert restored.original_scene_text == "待确认版本 1"
        assert restored.scene_result.scene_text == "玩家保存的正文"

        original = restarted.save_pending_scene_edit("turn-1", None)

        assert original["scene_text"] == "待确认版本 1"
        assert original["saved_scene_text"] is None
        assert restored.original_scene_text is None
        assert restored.scene_result.scene_text == "待确认版本 1"
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "name",
    ["", " ", ".", "..", ".hidden", "../escape", "a/b", "a\\b", "bad\0name"],
)
def test_validate_save_name_rejects_unsafe_display_names(name):
    with pytest.raises(ValueError):
        validate_save_name(name)


def test_default_runtime_paths_are_under_user_data_root():
    assert SAVES_DIR == APP_PATHS.data_root / "saves"
    assert APP_CONFIG_DIR == APP_PATHS.data_root / "config"
    assert ACTIVE_SAVE_FILE == APP_PATHS.data_root / "config" / "active_save"


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
        with runtime.stores.current_state as store:
            store.save(
                make_default_state().model_copy(
                    update={"player": PlayerState(name="Alpha")}
                )
            )

        beta = manager.create("beta")
        assert beta.active is True
        with runtime.stores.current_state as store:
            store.save(
                make_default_state().model_copy(
                    update={"player": PlayerState(name="Beta")}
                )
            )

        manager.switch(alpha.save_id)
        with runtime.stores.current_state as store:
            assert store.load().player.name == "Alpha"

        assert manager.active_file.read_text(encoding="utf-8") == alpha.save_id
        assert [save.name for save in manager.list()] == ["alpha", "beta", "默认存档"]
        assert manager.payload()["active"] == alpha.save_id
    finally:
        runtime.close()

def test_switch_preserves_and_restores_pending_turn_variants(tmp_path):
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
        state_before = make_default_state()
        runtime.pending_turns.add(_pending_turn(
            turn_id="turn-v1",
            group_id="group-1",
            version_index=1,
            state_before=state_before,
        ))
        runtime.pending_turns.add(_pending_turn(
            turn_id="turn-v2",
            group_id="group-1",
            version_index=2,
            state_before=state_before,
        ))

        manager.switch(other.save_id)
        assert runtime.campaign_id == other.save_id
        assert runtime.pending_turns.values() == []
        source_history = CampaignHistory(root / original_id)
        assert [
            record["status"] for record in source_history.pending_records()
        ] == ["awaiting_confirm", "awaiting_confirm"]
        with pytest.raises(RuntimeError, match="pending content"):
            manager.copy(original_id)
        with pytest.raises(RuntimeError, match="pending content"):
            manager.export_to(original_id, tmp_path / "pending.zip")

        manager.switch(original_id)

        restored = runtime.pending_turns.variants("group-1")
        assert [pending.turn_id for pending in restored] == ["turn-v1", "turn-v2"]
        assert [pending.version_index for pending in restored] == [1, 2]
        assert [pending.scene_result.scene_text for pending in restored] == [
            "待确认版本 1",
            "待确认版本 2",
        ]
        assert restored[0].context.user_input == "推开门"
    finally:
        runtime.close()


def test_create_and_activate_preserves_pending_opening_in_source_save(tmp_path):
    root = tmp_path / "saves"
    runtime = CampaignRuntime(
        "default",
        paths=CampaignPaths.from_data_dir(root / "default"),
        max_workers=1,
    )
    manager = SaveManager(root, runtime=runtime)
    try:
        original_id = manager.active_id
        config = InitConfig(
            world_name="泰瑞大陆",
            world_style="奇幻",
            ability_system="魔法",
            protagonist_name="莱尔",
            story_expectation="探索世界。",
        )
        runtime.pending_openings.add(PendingOpening(
            turn_id="opening-1",
            group_id="opening-1",
            version_index=1,
            initialization_fingerprint="fingerprint-1",
            config=config,
            experience_mode="player",
            current_state=build_initial_current_state(
                config,
                location="泰瑞大陆",
                currency_unit="金币",
            ),
            scene_result=SceneWriterResult(scene_text="待确认的开场。"),
        ))

        created = manager.create("另一段旅程")

        assert created.active is True
        assert runtime.pending_openings.values() == []
        manager.switch(original_id)
        restored = runtime.pending_openings.get("opening-1")
        assert restored is not None
        assert restored.scene_result.scene_text == "待确认的开场。"
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
        with runtime.stores.current_state as store:
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
        with runtime.stores.current_state as store:
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
