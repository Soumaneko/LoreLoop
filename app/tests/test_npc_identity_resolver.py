import pytest
from app.domain.npc_profile.identity_resolver import resolve_npc_identities
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import SceneNpcMention
from app.domain.scene_writer.schemas import NPCUsage


@pytest.mark.parametrize("presence", ["appeared", "mentioned_only"])
def test_used_archive_name_supplies_identity_and_presence(presence):
    result = resolve_npc_identities(
        profiles=[NPCProfile(npc_id="zhao", name="赵七")], player_name="主角",
        npc_usages=[NPCUsage(name="赵七", surfaces=["中年汉子"], presence=presence)],
        npc_mentions=[SceneNpcMention(primary_surface="赵七", presence="appeared",
                                     evidence_fact_ids=["f1"], evidence_paragraph_ids=["p2"])],
    )
    npc = result.resolved[0]
    assert npc.npc_id == "zhao"
    assert npc.presence == presence
    assert npc.primary_surfaces == ("中年汉子",)
    assert npc.evidence_paragraph_ids == ("p2",)
    assert npc.evidence_fact_ids == ("f1",)


def test_unused_and_unknown_names_do_not_resolve_from_aliases_or_mentions():
    result = resolve_npc_identities(
        profiles=[NPCProfile(name="林欢", aliases=["张欢"])], player_name="主角",
        npc_usages=[NPCUsage(name="张欢", surfaces=["林欢"], presence="appeared")],
        npc_mentions=[SceneNpcMention(primary_surface="林欢", presence="appeared", evidence_fact_ids=["f1"])],
    )
    assert result.resolved == ()
    assert result.diagnostics.unmatched_groups == 1


def test_receipt_not_evidence_controls_membership_and_merges_duplicates():
    result = resolve_npc_identities(
        profiles=[NPCProfile(npc_id="zhao", name="赵七")], player_name="主角", npc_mentions=[],
        npc_usages=[NPCUsage(name="赵七", surfaces=["汉子"], presence="mentioned_only"),
                    NPCUsage(name="赵七", surfaces=["老赵"], presence="appeared"),
                    NPCUsage(name="主角", surfaces=["你"], presence="appeared")],
    )
    assert len(result.resolved) == 1
    assert result.resolved[0].presence == "appeared"
    assert result.resolved[0].primary_surfaces == ("汉子", "老赵")
    assert result.resolved[0].evidence_paragraph_ids == ()
