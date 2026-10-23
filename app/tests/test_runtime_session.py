from app.runtime.campaign_migration import ensure_campaign_database
import json
from app.tests.factories.domains import make_pending_turn
from app.core.client_error import ClientValidationError
import threading
from types import SimpleNamespace
import pytest
from app.domain.current_state import PlayerState, make_default_state
from app.runtime import campaign_open as campaign_open_module
from app.runtime.campaign_open import open_campaign_resources
from app.runtime.paths import CampaignPaths
from app.runtime.session import (
    CampaignRuntime,
    PendingOpeningRepository,
    PendingTurnRepository,
    get_campaign_runtime,
)


def test_open_campaign_resources_recovers_before_uow_and_closes_on_failure(
    tmp_path,
    monkeypatch,
):
    events = []

    class History:
        def __init__(self, data_dir):
            events.append(("history", data_dir))

        def recover_interrupted(self):
            events.append(("recover", None))

    class Stores:
        def __init__(self, paths):
            events.append(("stores", paths))
            self.is_closed = False

        def close(self):
            self.is_closed = True
            events.append(("close", None))

    def fail_initialization(_path):
        events.append(("initialization", None))
        raise RuntimeError("init failed")

    paths = CampaignPaths.from_data_dir(tmp_path / "campaign")
    monkeypatch.setattr(
        campaign_open_module,
        "ensure_campaign_database",
        lambda actual: events.append(("ensure", actual)),
    )
    monkeypatch.setattr(campaign_open_module, "CampaignHistory", History)
    monkeypatch.setattr(campaign_open_module, "CampaignUnitOfWork", Stores)
    monkeypatch.setattr(
        campaign_open_module,
        "InitializationStore",
        fail_initialization,
    )

    with pytest.raises(RuntimeError, match="init failed"):
        open_campaign_resources(paths)

    assert events == [
        ("ensure", paths),
        ("history", paths.data_dir),
        ("recover", None),
        ("stores", paths),
        ("initialization", None),
        ("close", None),
    ]


def test_pytest_default_runtime_is_always_scoped_to_tmp_path(campaign_runtime, tmp_path):
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
        runtime.pending_turns.add(make_pending_turn(
            turn_id="v1",
            group_id="g",
            version_index=1,
            user_input="调查钟楼。",
            difficulty="困难",
            max_chars=1500,
        ))
        runtime.pending_turns.add(make_pending_turn(
            turn_id="v2",
            group_id="g",
            version_index=2,
            user_input="调查钟楼。",
            difficulty="困难",
            max_chars=1500,
        ))
        runtime.pending_turns.add(make_pending_turn(
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
            make_pending_turn(turn_id="turn", group_id="g", version_index=1)
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

        with pytest.raises(ClientValidationError, match="active operation"):
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


def test_reconfigure_reopens_old_campaign_after_late_target_failure(
    tmp_path,
    monkeypatch,
):
    old_paths = CampaignPaths.from_data_dir(tmp_path / "old")
    runtime = CampaignRuntime("old", paths=old_paths, max_workers=1)
    target_paths = CampaignPaths.from_data_dir(tmp_path / "target")
    with runtime.stores.current_state as store:
        store.save(make_default_state().model_copy(update={"player": PlayerState(name="旧存档")}))
    target = open_campaign_resources(target_paths)
    with target.stores.current_state as store:
        store.save(make_default_state().model_copy(update={"player": PlayerState(name="目标存档")}))
    target.stores.close()
    restore_pending = runtime._restore_pending_transactions
    attempts = 0

    def fail_target_pending_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("target pending invalid")
        return restore_pending()

    monkeypatch.setattr(
        runtime,
        "_restore_pending_transactions",
        fail_target_pending_once,
    )
    try:
        with pytest.raises(RuntimeError, match="target pending invalid"):
            runtime.reconfigure("target", target_paths)

        assert runtime.campaign_id == "old"
        assert runtime.paths == old_paths
        assert runtime.stores.is_closed is False
        with runtime.uow() as uow:
            with uow.current_state as store:
                assert store.load().player.name == "旧存档"
        runtime.reconfigure("target", target_paths)
        with runtime.uow() as uow:
            with uow.current_state as store:
                assert store.load().player.name == "目标存档"
    finally:
        runtime.close()


def test_save_task_blocks_new_pipeline_submission(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    runtime = CampaignRuntime(
        "save-task",
        paths=CampaignPaths.from_data_dir(tmp_path / "save-task"),
        max_workers=1,
    )
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            with runtime.save_task():
                with pytest.raises(ClientValidationError, match="save management task"):
                    with runtime.operation_submission():
                        pass
                with pytest.raises(ClientValidationError, match="save management task"):
                    executor.submit(runtime.reconfigure, "other", runtime.paths).result(timeout=2)
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


@pytest.mark.parametrize("damage", ["invalid_database", "missing_campaign"])
def test_invalid_snapshot_preserves_progress_and_live_json(tmp_path, damage):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    ensure_campaign_database(paths)
    core = paths.data_dir / "core_memory.json"
    core.write_text('{"items":[]}', encoding="utf-8")
    rt = CampaignRuntime("test", paths=paths, max_workers=1)
    rt.history.save_pending(turn_id="t1", kind="turn", group_id="g1", turn_index=1, payload={"turn_id": "t1"})
    rt.history.begin_confirmation("t1", write_db=True, scene_text=None)
    with rt.history._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        rt.history.record_confirmation(campaign, "t1", scene_text="场景正文")
    rt.history.complete_confirmation("t1", "g1")
    core.write_text(json.dumps({"items": [{
        "id": "current", "kind": "world_invariant", "strength": "hard_constraint",
        "source": "player", "content": "当前世界设定",
    }]}), encoding="utf-8")
    with rt.history._connect() as c:
        row = c.execute("SELECT path FROM turn_snapshots WHERE turn_id='t1'").fetchone()
        snap_path = rt.history._snapshot_path(row["path"])
    if damage == "invalid_database":
        (snap_path / "campaign.db").write_bytes(b"not a db")
    else:
        (snap_path / "manifest.json").write_text(json.dumps({"files": ["core_memory.json"]}))
    before = {path: path.read_bytes() for path in (paths.campaign_db, core)}
    with pytest.raises(RuntimeError):
        rt.undo()
    assert {path: path.read_bytes() for path in before} == before
    assert rt.history.get_transaction("t1")["status"] == "completed"
    with rt.uow() as uow:
        assert uow.current_state.load() is None
    rt.close()
    reopened = CampaignRuntime("reopened", paths=paths)
    reopened.close()


def test_reconfigure_failure_keeps_old_runtime_usable(tmp_path):
    paths = CampaignPaths.from_data_dir(tmp_path / "save")
    ensure_campaign_database(paths)
    rt = CampaignRuntime("sw-fail", paths=paths, max_workers=1)
    old_campaign_id = rt.campaign_id
    old_paths = rt.paths
    # Target directory has an invalid campaign.db; ensure_campaign_database
    # will fail while building the new runtime.
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    (bad_dir / "campaign.db").write_bytes(b"not a database")
    with pytest.raises(Exception):
        rt.reconfigure("sw-bad", CampaignPaths.from_data_dir(bad_dir))
    # Old runtime identity remains intact and gate is open.
    assert rt.campaign_id == old_campaign_id
    assert rt.paths == old_paths
    assert rt.stores.is_closed is False
    # A new UoW can still be created after the failed switch.
    u = rt.uow(write=False)
    u.close()
    # Operations/jobs must be freshly usable, not the shutdown instances.
    with rt.operation_submission():
        op_id = rt.operations.submit_callable("probe", lambda: 42)
        snapshot = rt.operations.wait(op_id, timeout=5)
        assert snapshot.status == "succeeded"
    rt.close()
