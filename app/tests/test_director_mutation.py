import pytest
from app.domain.director import DirectorResourceRef
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.world_setting import WorldSetting
from app.runtime.director_mutation import (
    DirectorPendingActorBinding,
    DirectorPendingMutationPlan,
    apply_director_mutations,
)
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork


def test_apply_rejects_overwrite_existing_setting(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="旧渡口",
        summary="连接河道两岸的小型渡口。",
        public_facts=["白日有渡船往返。"],
        hidden_facts=["夜间有人偷运货物。"],
        created_turn=2,
    )
    with stores.world_setting as s:
        s.save(setting)
    # Second plan with same ID should be rejected
    duplicate = WorldSetting(
        kind="location",
        name="旧渡口2",
        summary="重复。",
        public_facts=["a"],
        hidden_facts=["b"],
        created_turn=3,
        setting_id=setting.setting_id,
    )
    with pytest.raises(RuntimeError, match="overwrite an existing Setting"):
        apply_director_mutations(DirectorPendingMutationPlan(settings=[duplicate]), stores)


def test_apply_rejects_unknown_event_binding(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    binding = DirectorPendingActorBinding(
        event_id="missing-event",
        actor_name="陆远舟",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="npc_1"),
        turn_index=2,
    )
    with pytest.raises(ValueError, match="unknown DirectorEvent"):
        apply_director_mutations(DirectorPendingMutationPlan(actor_bindings=[binding]), stores)


def test_apply_rolls_back_after_first_write_fails(tmp_path, monkeypatch):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="旧渡口",
        summary="连接河道两岸的小型渡口。",
        public_facts=["白日有渡船往返。"],
        hidden_facts=["夜间有人偷运货物。"],
        created_turn=2,
    )
    draft = DirectorNPCDraft(
        draft_id="d-fail",
        name="失败草稿",
        role="npc",
        status="prepared",
        core_motivation="测试动机",
        hidden_secret="测试秘密",
        conflict_style="回避",
        social_style="冷淡",
        personality_trait="沉默",
        appearance="灰袍",
        created_turn=2,
    )
    # Simulate failure while persisting the second item (NPC draft), after the
    # first item (setting) has already been written.
    from app.domain.director_npc.store import DirectorNPCStore
    original_save_draft = DirectorNPCStore.save

    def fail_draft_save(self, draft):
        raise RuntimeError("injected draft write failure")

    monkeypatch.setattr(DirectorNPCStore, "save", fail_draft_save)

    with pytest.raises(RuntimeError, match="injected draft write failure"):
        with stores.transaction():
            apply_director_mutations(
                DirectorPendingMutationPlan(
                    settings=[setting],
                    npc_drafts=[draft],
                ),
                stores,
            )

    # Outer transaction rollback: the first write (setting) must be undone.
    with stores.world_setting as s:
        assert s.get_by_id(setting.setting_id) is None
    with stores.director_npc as d:
        assert d.get_by_id(draft.draft_id) is None


def test_invalid_binding_leaves_setting_unwritten(tmp_path):
    # Verify that a failed batch does not leave partial writes; atomicity is
    # provided by the outer campaign transaction, not by a journal.
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="旧渡口",
        summary="连接河道两岸的小型渡口。",
        public_facts=["白日有渡船往返。"],
        hidden_facts=["夜间有人偷运货物。"],
        created_turn=2,
    )
    # Plan with invalid binding should fail before any write
    bad_binding = DirectorPendingActorBinding(
        event_id="no-event",
        actor_name="x",
        actor_ref=DirectorResourceRef(kind="npc", resource_id="y"),
        turn_index=1,
    )
    with pytest.raises(ValueError):
        apply_director_mutations(
            DirectorPendingMutationPlan(settings=[setting], actor_bindings=[bad_binding]),
            stores,
        )
    # Setting must not have been persisted (validation failed before apply)
    with stores.world_setting as s:
        assert s.get_by_id(setting.setting_id) is None
