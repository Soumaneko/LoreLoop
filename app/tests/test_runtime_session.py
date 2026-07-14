from types import SimpleNamespace

from app.domain.current_state import PlayerState, make_default_state
from app.runtime.paths import CampaignPaths
from app.runtime.session import (
    CampaignRuntime,
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

        first_state = make_default_state().model_copy(
            update={"player": PlayerState(name="甲")}
        )
        second_state = make_default_state().model_copy(
            update={"player": PlayerState(name="乙")}
        )
        with first.stores.current_state() as store:
            store.save(first_state)
        with second.stores.current_state() as store:
            store.save(second_state)

        with first.stores.current_state() as store:
            assert store.load().player.name == "甲"
        with second.stores.current_state() as store:
            assert store.load().player.name == "乙"
    finally:
        first.close()
        second.close()
