import pytest

from types import SimpleNamespace
from pydantic import ValidationError
from app.domain.core_memory import CoreMemory
from app.domain.director_npc import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork

from app.tests.factories.director import (
    ScriptedDirectorGateway,
    make_current_state,
    make_director_npc,
)


def test_director_npc_output_rejects_blank_name():
    from app.domain.director_npc.schemas import DirectorNPCCreateOutput

    with pytest.raises(ValidationError):
        DirectorNPCCreateOutput(
            creation_reason="事件需要现场演员。",
            identity={
                "core_motivation": "完成受托调查。",
                "hidden_secret": "隐瞒过去的失败。",
                "role": "调查者",
            },
            behavior={
                "conflict_style": "先观察后行动。",
                "social_style": "谨慎寡言。",
                "personality_trait": "先听后问，从不先表态",
            },
            shell={
                "known_abilities": [],
                "appearance": "穿着防水短衣。",
                "name": "   ",
            },
        )


def test_apply_staged_disclosure_upgrades_persists_and_never_downgrades(
    tmp_path,
):
    from app.domain.director.schemas import DirectorStageMaterial
    from app.graph.confirmation_helpers import apply_staged_disclosure_upgrades

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile as store:
        upgrade_id = store.upsert(NPCProfile(
            name="林澈",
            role="社区医生",
            disclosure_level="remote",
        ))
        downgrade_id = store.upsert(NPCProfile(
            name="顾晚棠",
            role="阁主",
            disclosure_level="intimate",
        ))

    pending = SimpleNamespace(director_stage=SimpleNamespace(materials=[
        DirectorStageMaterial(
            kind="npc",
            resource_id=upgrade_id,
            guidance="交心后披露核心动机。",
            payload={
                "source": "npc_profile",
                "disclosure_upgrade": True,
                "new_disclosure_level": "intimate",
                "name": "林澈",
            },
        ),
        DirectorStageMaterial(
            kind="npc",
            resource_id=downgrade_id,
            guidance="尝试回退。",
            payload={
                "source": "npc_profile",
                "disclosure_upgrade": True,
                "new_disclosure_level": "face_to_face",
                "name": "顾晚棠",
            },
        ),
    ]))

    apply_staged_disclosure_upgrades(pending, stores=stores)

    with stores.npc_profile as store:
        assert store.get_by_id(upgrade_id).disclosure_level == "intimate"
        assert store.get_by_id(downgrade_id).disclosure_level == "intimate"


def test_promote_staged_director_npc_profile_writes_disclosure_level(tmp_path):
    from app.domain.director.schemas import DirectorStageMaterial
    from app.graph.confirmation_helpers import promote_staged_director_npc_profile

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc().model_copy(
        update={"source_setting_ids": ["setting-town"]}
    )
    with stores.director_npc as store:
        store.save(draft)

    pending = SimpleNamespace(director_stage=SimpleNamespace(materials=[
        DirectorStageMaterial(
            kind="npc",
            resource_id=draft.draft_id,
            guidance="以当面身份登场。",
            payload={
                "source": "director_npc",
                "view_mode": "face_to_face",
                "name": "林澈",
            },
        ),
    ]))

    confirmations = promote_staged_director_npc_profile(
        pending,
        turn_index=3,
        stores=stores,
        used_names={"林澈"},
    )

    assert len(confirmations) == 1
    with stores.npc_profile as store:
        profile = store.get_by_id(confirmations[0].npc_id)
        assert profile.disclosure_level == "face_to_face"
        assert profile.source_setting_ids == ["setting-town"]


def test_promote_staged_director_npc_profile_promotes_all_introduced_drafts(
    tmp_path,
):
    from app.domain.director.schemas import DirectorStageMaterial
    from app.graph.confirmation_helpers import promote_staged_director_npc_profile

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft_a = make_director_npc().model_copy(
        update={"draft_id": "director_npc_a", "name": "林澈"}
    )
    draft_b = make_director_npc().model_copy(
        update={"draft_id": "director_npc_b", "name": "顾晚棠"}
    )
    with stores.director_npc as store:
        store.save(draft_a)
        store.save(draft_b)

    pending = SimpleNamespace(director_stage=SimpleNamespace(materials=[
        DirectorStageMaterial(
            kind="npc",
            resource_id=draft_a.draft_id,
            guidance="登场。",
            payload={"source": "director_npc", "view_mode": "face_to_face", "name": "林澈"},
        ),
        DirectorStageMaterial(
            kind="npc",
            resource_id=draft_b.draft_id,
            guidance="登场。",
            payload={"source": "director_npc", "view_mode": "remote", "name": "顾晚棠"},
        ),
    ]))

    confirmations = promote_staged_director_npc_profile(
        pending,
        turn_index=3,
        stores=stores,
        used_names={"林澈", "顾晚棠"},
    )

    assert {item.draft.draft_id for item in confirmations} == {
        "director_npc_a",
        "director_npc_b",
    }
    with stores.npc_profile as store:
        by_source = {
            profile.name: profile
            for profile in store.list_all()
        }
        assert by_source["林澈"].disclosure_level == "face_to_face"
        assert by_source["顾晚棠"].disclosure_level == "remote"

    assert promote_staged_director_npc_profile(
        pending, turn_index=4, stores=stores, used_names={"林澈", "顾晚棠"},
    ) == []
    with stores.npc_profile as store:
        assert {profile.name: profile for profile in store.list_all()} == by_source


@pytest.mark.parametrize("match_by", ["name", "source_id"])
def test_promotion_preserves_existing_profile_and_draft(tmp_path, match_by):
    from app.graph.confirmation_helpers import ensure_director_npc_profile

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc()
    existing = NPCProfile(
        name=draft.name if match_by == "name" else "已修改的姓名",
        role="已确认的职业",
        disclosure_level="intimate",
        source_director_npc_id=draft.draft_id if match_by == "source_id" else None,
        source_setting_ids=["setting-old"],
    )
    with stores.director_npc as store:
        store.save(draft)
        saved_draft = store.get_by_id(draft.draft_id)
    with stores.npc_profile as store:
        store.upsert(existing)

    assert ensure_director_npc_profile(
        draft.draft_id, turn_index=4, stores=stores, disclosure_level="remote",
    ) is None
    with stores.npc_profile as store:
        assert store.list_all() == [existing]
    with stores.director_npc as store:
        assert store.get_by_id(draft.draft_id) == saved_draft
