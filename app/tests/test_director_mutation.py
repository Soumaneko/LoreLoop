import pytest

from app.domain.current_state import WorldClock
from app.domain.director import DirectorResourceRef
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_event.store import DirectorEventStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.world_setting import WorldSetting
from app.graph.director_agent import (
    _DirectorMutationPlan,
    _PendingActorBinding,
    _commit_director_mutations,
)
from app.runtime.director_mutation import (
    DirectorMutationJournal,
    DirectorMutationJournalStore,
    DirectorProfileBeforeImage,
    recover_pending_director_mutation,
)
from app.runtime.paths import CampaignPaths
from app.runtime.stores import StoreBundle


def _event() -> DirectorEvent:
    return DirectorEvent(
        role="ambient",
        title="旧桥封锁",
        subject="河务署",
        intent="控制渡河通道",
        method="封锁旧桥",
        macro_intent="改变城镇交通格局",
        deadline_clock=WorldClock().advance_phase(3),
        created_turn=1,
        updated_turn=1,
    )


def test_commit_failure_restores_existing_npc_profile(tmp_path, monkeypatch):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        npc_id = store.upsert(NPCProfile(name="陆远舟", role="镖师"))
        before = store.get_by_id(npc_id)
    assert before is not None
    updated = before.model_copy(update={"name": "陆行舟"})

    original_upsert = NPCProfileStore.upsert
    calls = {"count": 0}

    def write_then_fail_once(self, profile):
        calls["count"] += 1
        result = original_upsert(self, profile)
        if calls["count"] == 1:
            raise RuntimeError("injected profile commit failure")
        return result

    monkeypatch.setattr(NPCProfileStore, "upsert", write_then_fail_once)

    with pytest.raises(RuntimeError, match="injected profile commit failure"):
        _commit_director_mutations(
            _DirectorMutationPlan(profile_upserts=[updated]),
            stores,
        )

    with stores.npc_profile() as store:
        assert store.get_by_id(npc_id) == before
    assert not stores.paths.director_mutation_journal.exists()


def test_commit_failure_restores_existing_event_actor_bindings(
    tmp_path, monkeypatch
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    event = _event()
    with stores.director_event() as store:
        store.save(event)
        before = store.get_by_id(event.event_id)
    assert before is not None

    original_bind_actor = DirectorEventStore.bind_actor
    calls = {"count": 0}

    def bind_then_fail_once(self, *args, **kwargs):
        calls["count"] += 1
        result = original_bind_actor(self, *args, **kwargs)
        if calls["count"] == 1:
            raise RuntimeError("injected actor commit failure")
        return result

    monkeypatch.setattr(DirectorEventStore, "bind_actor", bind_then_fail_once)
    binding = _PendingActorBinding(
        event_id=event.event_id,
        actor_name="陆远舟",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
        turn_index=2,
    )

    with pytest.raises(RuntimeError, match="injected actor commit failure"):
        _commit_director_mutations(
            _DirectorMutationPlan(actor_bindings=[binding]),
            stores,
        )

    with stores.director_event() as store:
        assert store.get_by_id(event.event_id) == before
    assert not stores.paths.director_mutation_journal.exists()


def test_recovery_rolls_back_interrupted_cross_database_commit(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        npc_id = store.upsert(NPCProfile(name="陆远舟", role="镖师"))
        before = store.get_by_id(npc_id)
    assert before is not None
    setting = WorldSetting(
        kind="location",
        name="旧渡口",
        summary="连接河道两岸的小型渡口。",
        public_facts=["白日有渡船往返。"],
        hidden_facts=["夜间有人偷运货物。"],
        created_turn=2,
    )
    journal_store = DirectorMutationJournalStore(
        stores.paths.director_mutation_journal
    )
    journal_store.save(
        DirectorMutationJournal(
            new_setting_ids=[setting.setting_id],
            profile_before_images=[
                DirectorProfileBeforeImage(npc_id=npc_id, profile=before)
            ],
        )
    )
    with stores.world_setting() as store:
        store.save(setting)
    with stores.npc_profile() as store:
        store.upsert(before.model_copy(update={"name": "陆行舟"}))

    assert recover_pending_director_mutation(stores) is True

    with stores.world_setting() as store:
        assert store.get_by_id(setting.setting_id) is None
    with stores.npc_profile() as store:
        assert store.get_by_id(npc_id) == before
    assert not stores.paths.director_mutation_journal.exists()
