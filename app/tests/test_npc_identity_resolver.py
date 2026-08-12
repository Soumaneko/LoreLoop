from dataclasses import asdict

import pytest

from app.domain.npc_profile.identity_resolver import resolve_npc_identities
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import AliasRelation, SceneNpcMention

_PLAYER = "萧云痕"


def _profile(npc_id, name, *, aliases=None, transient=None, **kwargs) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id,
        name=name,
        aliases=list(aliases or []),
        transient_aliases=dict(transient or {}),
        **kwargs,
    )


def _relation(
    surface,
    basis,
    evidence=None,
    paragraph_evidence=None,
) -> AliasRelation:
    return AliasRelation(
        surface=surface,
        basis=basis,
        evidence_fact_ids=list(evidence or []),
        evidence_paragraph_ids=list(paragraph_evidence or []),
    )


def _mention(
    primary,
    *,
    relations=None,
    evidence=None,
    paragraph_evidence=None,
) -> SceneNpcMention:
    if evidence is None:
        evidence = ["f1"]  # schema requires at least one evidence
    relations = list(relations or [])
    return SceneNpcMention(
        primary_surface=primary,
        alias_relations=relations,
        evidence_fact_ids=list(evidence or []),
        evidence_paragraph_ids=list(paragraph_evidence or []),
    )


def _resolve(profiles, mentions, *, texts=None, player=_PLAYER):
    resolution = resolve_npc_identities(
        profiles=profiles,
        npc_mentions=mentions,
        paragraph_texts=texts or [],
        player_name=player,
    )
    assert resolution.diagnostics is not None
    return resolution


def _resolved_ids(resolution):
    return {item.npc_id for item in resolution.resolved}


def test_group_resolves_to_unique_profile():
    profiles = [_profile("npc_001", "沈孤鸿", aliases=["沈二"])]
    resolution = _resolve(
        profiles,
        [_mention("沈孤鸿")],
    )

    assert _resolved_ids(resolution) == {"npc_001"}
    resolved = resolution.resolved[0]
    assert resolved.canonical_name == "沈孤鸿"
    assert resolved.primary_surfaces == ("沈孤鸿",)


def test_alias_relation_connects_new_surface_to_existing():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("沈二爷", "same_scene_coreference", evidence=["f2"]),
                ],
            )
        ],
    )

    assert _resolved_ids(resolution) == {"npc_001"}
    assert resolution.pending_alias_changes == ()


def test_explicit_fixed_alias_produces_pending_alias_change():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("沈二爷", "explicit_fixed_alias", evidence=["f2"]),
                ],
            )
        ],
    )

    assert _resolved_ids(resolution) == {"npc_001"}
    assert len(resolution.pending_alias_changes) == 1
    change = resolution.pending_alias_changes[0]
    assert change.npc_id == "npc_001"
    assert change.surface == "沈二爷"
    assert change.evidence_fact_ids == ("f2",)
    assert resolution.diagnostics.pending_aliases == 1
    assert resolution.diagnostics.alias_conflicts == 0


def test_temporary_address_and_coreference_not_persisted():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("宗主", "temporary_address", evidence=["f3"]),
                    _relation("持剑客", "same_scene_coreference", evidence=["f3"]),
                ],
            )
        ],
    )

    assert _resolved_ids(resolution) == {"npc_001"}
    assert resolution.pending_alias_changes == ()


def test_ambiguous_group_is_skipped():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["掌柜"]),
        _profile("npc_002", "林川", aliases=["掌柜"]),
    ]
    resolution = _resolve(
        profiles,
        [_mention("掌柜")],
    )

    assert _resolved_ids(resolution) == set()
    assert resolution.candidates == ()
    assert resolution.diagnostics.ambiguous_groups == 1


def test_unmatched_named_person_becomes_candidate():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "江晚晴",
                relations=[
                    _relation("晚晴姑娘", "temporary_address", evidence=["f1"]),
                ],
            )
        ],
    )

    assert _resolved_ids(resolution) == set()
    assert len(resolution.candidates) == 1
    candidate = resolution.candidates[0]
    assert candidate.primary_surface == "江晚晴"
    assert {relation.surface for relation in candidate.alias_relations} == {
        "晚晴姑娘"
    }
    assert candidate.evidence_fact_ids == ("f1",)
    assert resolution.diagnostics.unmatched_groups == 1


def test_player_protected_group_is_skipped():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [_mention(_PLAYER)],
    )

    assert _resolved_ids(resolution) == set()
    assert resolution.candidates == ()
    assert resolution.diagnostics.groups == 1
    assert resolution.diagnostics.unmatched_groups == 0


def test_scan_union_keeps_missed_existing_npc():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二"]),
        _profile("npc_002", "林川"),
    ]
    resolution = _resolve(
        profiles,
        [],
        texts=["沈孤鸿走进醉花楼，沈二在门口守着。"],
    )
    assert resolution.diagnostics is not None

    assert _resolved_ids(resolution) == {"npc_001"}
    assert resolution.diagnostics.scan_union_added == 1
    assert resolution.resolved[0].npc_id == "npc_001"


def test_pending_alias_conflict_with_another_profile_skipped():
    profiles = [
        _profile("npc_001", "沈孤鸿"),
        _profile("npc_002", "林川", aliases=["沈二爷"]),
    ]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("沈二爷", "explicit_fixed_alias", evidence=["f2"]),
                ],
            )
        ],
    )

    assert _resolved_ids(resolution) == {"npc_001"}
    assert resolution.pending_alias_changes == ()
    assert resolution.diagnostics.alias_conflicts == 1


def test_resolution_is_deterministic():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二"]),
        _profile("npc_002", "林川"),
    ]
    mentions = [
        _mention(
            "沈孤鸿",
            relations=[
                _relation("沈二爷", "explicit_fixed_alias", evidence=["f2"]),
            ],
        ),
        _mention("江晚晴"),
    ]
    texts = ["沈孤鸿在醉花楼，林川稍后离开。"]

    first = _resolve(profiles, mentions, texts=texts)
    second = _resolve(profiles, mentions, texts=texts)

    assert asdict(first) == asdict(second)


def test_npc_mentions_schema_validates_shape():
    with pytest.raises(Exception):
        SceneNpcMention(primary_surface="沈孤鸿")
    with pytest.raises(Exception):
        AliasRelation(surface="沈二爷", basis="explicit_fixed_alias")
    mention = SceneNpcMention(
        primary_surface="沈孤鸿",
        evidence_fact_ids=["f1"],
    )
    assert mention.primary_surface == "沈孤鸿"


def test_same_npc_groups_are_merged():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("沈二爷", "explicit_fixed_alias", evidence=["f1"]),
                ],
            ),
            _mention(
                "沈孤鸿",
                relations=[
                    _relation("沈四爷", "explicit_fixed_alias", evidence=["f2"]),
                ],
            ),
        ],
    )

    assert len(resolution.resolved) == 1
    resolved = resolution.resolved[0]
    assert resolved.primary_surfaces == ("沈孤鸿",)
    assert {relation.surface for relation in resolved.alias_relations} == {
        "沈二爷",
        "沈四爷",
    }
    assert len(resolution.pending_alias_changes) == 2


def test_global_alias_conflict_drops_all_assignments():
    profiles = [_profile("npc_001", "甲"), _profile("npc_002", "乙")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "甲",
                relations=[_relation("掌柜", "explicit_fixed_alias", evidence=["f1"])],
            ),
            _mention(
                "乙",
                relations=[_relation("掌柜", "explicit_fixed_alias", evidence=["f1"])],
            ),
        ],
    )

    assert resolution.pending_alias_changes == ()
    assert resolution.diagnostics.alias_conflicts == 2


def test_scan_skips_ambiguous_shared_alias():
    profiles = [
        _profile("npc_001", "甲", aliases=["掌柜"]),
        _profile("npc_002", "乙", aliases=["掌柜"]),
    ]
    resolution = _resolve(profiles, [], texts=["掌柜走了进来。"])

    assert resolution.resolved == ()
    assert resolution.diagnostics.scan_union_added == 0


def test_candidate_carries_mention_level_evidence():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "江晚晴",
                evidence=["f1"],
                relations=[
                    _relation("晚晴姑娘", "temporary_address", evidence=["f2"]),
                ],
            )
        ],
    )

    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].evidence_fact_ids == ("f1", "f2")


def test_unmatched_candidate_carries_alias_relations():
    """有意义的其它称呼通过 alias relation 进入新候选。"""
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "甲",
                relations=[
                    _relation("掌柜", "explicit_fixed_alias", evidence=["f1"]),
                ],
            )
        ],
    )

    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].primary_surface == "甲"
    assert [
        relation.surface for relation in resolution.candidates[0].alias_relations
    ] == ["掌柜"]


def test_player_protection_covers_whole_cluster():
    resolution = _resolve(
        [],
        [
            _mention(
                "萧宗主",
                relations=[
                    _relation(_PLAYER, "same_scene_coreference", evidence=["f1"]),
                ],
            )
        ],
    )

    assert resolution.candidates == ()
    assert resolution.diagnostics.unmatched_groups == 0


def test_duplicate_unmatched_groups_produce_single_candidate():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention("江晚晴", evidence=["f1"]),
            _mention("江晚晴", evidence=["f2"]),
        ],
    )

    assert len(resolution.candidates) == 1
    assert resolution.candidates[0].evidence_fact_ids == ("f1", "f2")


def test_pending_alias_evidence_is_merged():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation(
                        "沈二爷",
                        "explicit_fixed_alias",
                        evidence=["f1"],
                        paragraph_evidence=["turn_0_p001"],
                    ),
                ],
            ),
            _mention(
                "沈孤鸿",
                relations=[
                    _relation(
                        "沈二爷",
                        "explicit_fixed_alias",
                        evidence=["f2"],
                        paragraph_evidence=["turn_0_p002"],
                    ),
                ],
            ),
        ],
    )

    assert len(resolution.pending_alias_changes) == 1
    assert resolution.pending_alias_changes[0].evidence_fact_ids == ("f1", "f2")
    assert resolution.pending_alias_changes[0].evidence_paragraph_ids == (
        "turn_0_p001",
        "turn_0_p002",
    )


def test_pending_alias_paragraph_only_evidence_is_preserved():
    profiles = [_profile("npc_001", "沈孤鸿")]
    resolution = _resolve(
        profiles,
        [
            _mention(
                "沈孤鸿",
                relations=[
                    _relation(
                        "沈二爷",
                        "explicit_fixed_alias",
                        paragraph_evidence=["turn_0_p001"],
                    ),
                ],
            ),
        ],
    )

    assert len(resolution.pending_alias_changes) == 1
    change = resolution.pending_alias_changes[0]
    assert change.evidence_fact_ids == ()
    assert change.evidence_paragraph_ids == ("turn_0_p001",)
