import threading
from types import SimpleNamespace

import pytest

from app.domain.current_state import PlayerState, make_default_state
from app.runtime.paths import CampaignPaths
from app.runtime.session import (
    CampaignRuntime,
    PendingOpeningRepository,
    PendingTurnRepository,
    get_campaign_runtime,
)


def test_pytest_default_runtime_is_always_scoped_to_tmp_path(tmp_path):
    runtime = get_campaign_runtime()

    assert runtime.campaign_id == "pytest"
    assert runtime.paths.data_dir == tmp_path / "campaign-runtime"


def test_pending_turn_repository_orders_and_discards_variants():
    repository = PendingTurnRepository()
    repository.add(SimpleNamespace(turn_id="v2", group_id="g", version_index=2))
    repository.add(SimpleNamespace(turn_id="other", group_id="other", version_index=1))
    repository.add(SimpleNamespace(turn_id="v1", group_id="g", version_index=1))

    assert [pending.turn_id for pending in repository.variants("g")] == ["v1", "v2"]

    repository.discard_group("g")

    assert repository.get("v1") is None
    assert repository.get("v2") is None
    assert repository.get("other") is not None


def test_runtime_discards_pending_group_and_returns_original_input(tmp_path):
    runtime = CampaignRuntime(
        "discard-pending",
        paths=CampaignPaths.from_data_dir(tmp_path / "discard-pending"),
        max_workers=1,
    )
    try:
        runtime.pending_turns.add(SimpleNamespace(
            turn_id="v1",
            group_id="g",
            version_index=1,
            user_input="调查钟楼。",
            difficulty="困难",
            max_chars=1500,
        ))
        runtime.pending_turns.add(SimpleNamespace(
            turn_id="v2",
            group_id="g",
            version_index=2,
            user_input="调查钟楼。",
            difficulty="困难",
            max_chars=1500,
        ))
        runtime.pending_turns.add(SimpleNamespace(
            turn_id="other",
            group_id="other",
            version_index=1,
            user_input="等待。",
            difficulty="普通",
            max_chars=1000,
        ))

        result = runtime.discard_pending_turn("v2")

        assert result["user_input"] == "调查钟楼。"
        assert result["discarded_turn_ids"] == ["v1", "v2"]
        assert runtime.pending_turns.get("v1") is None
        assert runtime.pending_turns.get("v2") is None
        assert runtime.pending_turns.get("other") is not None
    finally:
        runtime.close()


def test_campaign_runtimes_isolate_paths_stores_pending_and_locks(tmp_path):
    first = CampaignRuntime(
        "first",
        paths=CampaignPaths.from_data_dir(tmp_path / "first"),
        max_workers=1,
    )
    second = CampaignRuntime(
        "second",
        paths=CampaignPaths.from_data_dir(tmp_path / "second"),
        max_workers=1,
    )
    try:
        first.pending_turns.add(
            SimpleNamespace(turn_id="turn", group_id="g", version_index=1)
        )
        assert first.pending_turns.get("turn") is not None
        assert second.pending_turns.get("turn") is None
        assert first.confirmation_lock is not second.confirmation_lock
        assert first.operations is not second.operations
        assert first.operations.campaign_id == "first"
        assert second.operations.campaign_id == "second"

        first_state = make_default_state().model_copy(
            update={"player": PlayerState(name="甲")}
        )
        second_state = make_default_state().model_copy(
            update={"player": PlayerState(name="乙")}
        )
        with first.stores.current_state as store:
            store.save(first_state)
        with second.stores.current_state as store:
            store.save(second_state)

        with first.stores.current_state as store:
            assert store.load().player.name == "甲"
        with second.stores.current_state as store:
            assert store.load().player.name == "乙"
    finally:
        first.close()
        second.close()


def test_runtime_rejects_save_switch_while_operation_is_active(tmp_path):
    runtime = CampaignRuntime(
        "first",
        paths=CampaignPaths.from_data_dir(tmp_path / "first"),
        max_workers=1,
    )
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
            runtime.reconfigure(
                "second",
                CampaignPaths.from_data_dir(tmp_path / "second"),
            )

        release.set()
        assert runtime.operations.wait(operation_id, timeout=2).status == "succeeded"
        runtime.reconfigure(
            "second",
            CampaignPaths.from_data_dir(tmp_path / "second"),
        )
        assert runtime.operations.campaign_id == "second"
    finally:
        release.set()
        runtime.close()


def test_save_task_blocks_new_pipeline_submission(tmp_path):
    runtime = CampaignRuntime(
        "save-task",
        paths=CampaignPaths.from_data_dir(tmp_path / "save-task"),
        max_workers=1,
    )
    try:
        with runtime.save_task():
            with pytest.raises(RuntimeError, match="save management task"):
                with runtime.operation_submission():
                    pass
    finally:
        runtime.close()


def _pending_item(turn_id, group_id="g", version_index=1):
    return SimpleNamespace(
        turn_id=turn_id,
        group_id=group_id,
        version_index=version_index,
    )


def test_pending_turn_repository_notifies_remove_only_when_present():
    repository = PendingTurnRepository()
    removed = []
    repository.configure_callbacks(on_remove=removed.append)
    repository.add(_pending_item("x"))
    repository.remove("x")
    repository.remove("missing")
    assert removed == [["x"]]


def test_pending_opening_repository_notifies_remove_even_when_missing():
    repository = PendingOpeningRepository()
    removed = []
    repository.configure_callbacks(on_remove=removed.append)
    repository.remove("never-added")
    assert removed == [["never-added"]]


def test_pending_turn_repository_rolls_back_add_on_callback_failure():
    repository = PendingTurnRepository()

    def boom(pending):
        raise RuntimeError("boom")

    repository.configure_callbacks(on_add=boom)
    with pytest.raises(RuntimeError, match="boom"):
        repository.add(_pending_item("t"))
    assert repository.get("t") is None


def test_pending_repositories_discard_group_notifies_removed_turn_ids():
    for repository_cls in (PendingTurnRepository, PendingOpeningRepository):
        repository = repository_cls()
        removed = []
        repository.configure_callbacks(on_remove=removed.append)
        repository.add(_pending_item("a", group_id="g"))
        repository.add(_pending_item("b", group_id="g"))
        repository.add(_pending_item("c", group_id="other"))
        repository.discard_group("g")
        assert removed == [["a", "b"]]
        assert repository.get("c") is not None
