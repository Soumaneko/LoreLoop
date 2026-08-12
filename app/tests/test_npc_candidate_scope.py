import json

import pytest

from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.candidate_scope import (
    OPTION_CAP,
    build_candidate_scope,
)
from app.domain.npc_profile.schemas import NPCProfile


def _state(*, participants=None, player="萧云痕") -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="醉花楼",
            participants=list(participants or []),
        ),
        player=PlayerState(name=player),
    )


def _profile(
    npc_id,
    name,
    *,
    aliases=None,
    transient=None,
    role=None,
    faction=None,
    appearance=None,
    public_summary=None,
) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id,
        name=name,
        aliases=list(aliases or []),
        transient_aliases=dict(transient or {}),
        role=role,
        faction=faction,
        appearance=appearance,
        public_summary=public_summary,
    )


def _facts(focus, *facts) -> str:
    items = [
        {"fact_id": f"f{index}", "fact": fact}
        for index, fact in enumerate(facts, start=1)
    ]
    return json.dumps({"scene_focus": focus, "facts": items}, ensure_ascii=False)


def _ids(scope) -> list[str]:
    return [option.npc_id for option in scope.identity_options]


# --- exact: participant ---


def test_participant_exact_hit():
    state = _state(participants=["沈孤鸿"])
    scope = build_candidate_scope(
        scene_facts=_facts("沈孤鸿进入醉花楼。"),
        current_state=state,
        profiles=[_profile("npc_001", "沈孤鸿")],
    )
    assert [o.npc_id for o in scope.exact_targets] == ["npc_001"]
    assert _ids(scope) == ["npc_001"]
    assert scope.diagnostics.participant_exact == 1
    assert scope.diagnostics.text_exact == 0


def test_participant_exact_via_alias_and_transient():
    state = _state(participants=["沈二爷", "陆大爷"])
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "陆川", transient={"陆大爷": 5}),
    ]
    scope = build_candidate_scope(
        scene_facts=_facts("宴席开场。"),
        current_state=state,
        profiles=profiles,
    )
    assert _ids(scope) == ["npc_001", "npc_002"]
    assert scope.diagnostics.participant_exact == 2


# --- exact: text substring ---


def test_scene_facts_substring_exact():
    state = _state()
    profiles = [_profile("npc_001", "陆川", aliases=["陆大爷"])]
    scope = build_candidate_scope(
        scene_facts=_facts("陆大爷推门而入。", "陆大爷招呼众人。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == ["npc_001"]
    assert scope.diagnostics.text_exact == 1


def test_paragraph_text_substring_exact():
    state = _state()
    profiles = [_profile("npc_001", "陆川")]
    scope = build_candidate_scope(
        scene_facts=_facts("众人落座。"),
        current_state=state,
        profiles=profiles,
        paragraph_texts=["陆川在角落擦拭长剑。"],
    )
    assert [o.npc_id for o in scope.exact_targets] == ["npc_001"]
    assert scope.diagnostics.text_exact == 1


def test_per_segment_matching_prevents_cross_boundary_hits():
    state = _state()
    profiles = [_profile("npc_001", "合欢宗")]
    scope = build_candidate_scope(
        scene_facts=_facts("合欢", "宗主"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == []


def test_single_char_name_not_text_exact():
    state = _state()
    profiles = [_profile("npc_001", "道")]
    scope = build_candidate_scope(
        scene_facts=_facts("知道大道通何方。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == []


# --- contextual ---


def test_contextual_relevance_via_descriptor_fields():
    state = _state()
    profiles = [
        _profile("npc_002", "沈二", role="醉花楼掌柜"),
        _profile("npc_003", "翠娘", role="醉花楼歌女"),
    ]
    scope = build_candidate_scope(
        scene_facts=_facts("醉花楼歌女低声应酬。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == ["npc_003"]
    assert scope.diagnostics.contextual_found == 1
    assert scope.diagnostics.contextual_included == 1
    assert scope.diagnostics.overflow == 0


def test_contextual_tiebreak_prefers_more_signals():
    state = _state()
    profiles = [
        _profile("npc_004", "赵四", role="铁拳帮帮主", faction="铁拳帮"),
        _profile("npc_005", "钱五", role="铁拳帮长老"),
    ]
    scope = build_candidate_scope(
        scene_facts=_facts("铁拳帮帮主率众赴宴。", "铁拳帮长老随行。" ),
        current_state=state,
        profiles=profiles,
    )
    assert _ids(scope) == ["npc_004", "npc_005"]
    assert scope.diagnostics.contextual_found == 2


def test_cap_bounds_options_but_never_trims_exact():
    participants = ["npc000"]
    profiles = [
        _profile("npc000", "npc000"),
        *[
            _profile(f"npc{i:03d}", f"npc{i:03d}", role="醉花楼伙计")
            for i in range(1, 30)
        ],
    ]
    state = _state(participants=participants)
    scope = build_candidate_scope(
        scene_facts=_facts("醉花楼伙计端酒上菜。", "npc000招呼伙计。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == ["npc000"]
    assert scope.identity_options[0].npc_id == "npc000"
    assert len(scope.identity_options) == OPTION_CAP
    assert scope.diagnostics.contextual_found == 29
    assert scope.diagnostics.contextual_included == OPTION_CAP - 1
    assert scope.diagnostics.overflow == 29 - (OPTION_CAP - 1)


def test_exact_targets_exceed_cap_still_all_included():
    participants = [f"npc{i:03d}" for i in range(25)]
    profiles = [_profile(f"npc{i:03d}", f"npc{i:03d}") for i in range(25)]
    state = _state(participants=participants)
    scope = build_candidate_scope(
        scene_facts=_facts("众人齐聚。"),
        current_state=state,
        profiles=profiles,
        option_cap=20,
    )
    assert len(scope.exact_targets) == 25
    assert len(scope.identity_options) == 25
    assert scope.diagnostics.contextual_included == 0


# --- player protection ---


def test_player_name_never_matched_as_npc():
    state = _state(participants=["萧云痕"])
    profiles = [
        _profile("npc_001", "萧云痕"),
        _profile("npc_002", "陆川", aliases=["萧云痕"]),
    ]
    scope = build_candidate_scope(
        scene_facts=_facts("萧云痕推门而入。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == []


# --- structure ---


def test_identity_options_include_all_exact_and_dedupe():
    state = _state(participants=["沈孤鸿"])
    profiles = [_profile("npc_001", "沈孤鸿", aliases=["沈二爷"])]
    scope = build_candidate_scope(
        scene_facts=_facts("沈孤鸿与沈二爷共进晚膳。"),
        current_state=state,
        profiles=profiles,
    )
    assert _ids(scope) == ["npc_001"]
    assert [o.npc_id for o in scope.exact_targets] == ["npc_001"]
    assert len(scope.identity_options) == 1


def test_identity_option_carries_public_fields():
    state = _state(participants=["沈孤鸿"])
    profile = _profile(
        "npc_001",
        "沈孤鸿",
        role="醉花楼掌柜",
        public_summary="早年闯荡江湖。",
        faction="无",
    )
    scope = build_candidate_scope(
        scene_facts=_facts("沈孤鸿现身。"),
        current_state=state,
        profiles=[profile],
    )
    option = scope.identity_options[0]
    assert option.name == "沈孤鸿"
    assert option.role == "醉花楼掌柜"
    assert option.public_summary == "早年闯荡江湖。"


def test_alias_ordering_and_normalized_dedup():
    state = _state(participants=["沈孤鸿"])
    profile = _profile(
        "npc_001",
        "沈孤鸿",
        aliases=["乙", "甲"],
        transient={"丙": 5, "甲": 6},
    )
    scope = build_candidate_scope(
        scene_facts=_facts("沈孤鸿现身。"),
        current_state=state,
        profiles=[profile],
    )
    assert scope.identity_options[0].aliases == ["乙", "甲", "丙"]


def test_deterministic_across_profile_order():
    profiles = [
        _profile("npc_003", "翠娘", role="醉花楼歌女"),
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "陆川", role="醉花楼伙计"),
    ]
    state = _state(participants=["沈二爷"])
    scene_facts = _facts("醉花楼歌女低声应酬。", "沈二爷坐于主位。")

    def _build(ordered_profiles):
        return build_candidate_scope(
            scene_facts=scene_facts,
            current_state=state,
            profiles=ordered_profiles,
        )

    first = _build(profiles)
    second = _build(profiles)
    third = _build(list(reversed(profiles)))
    assert first == second
    assert first == third


# --- ambiguous shared variants ---


def test_ambiguous_alias_participant_not_exact():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "沈青", aliases=["沈二爷"]),
    ]
    state = _state(participants=["沈二爷"])
    scope = build_candidate_scope(
        scene_facts=_facts("宴席开场。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == ["npc_001", "npc_002"]
    assert scope.diagnostics.ambiguous_variants == 1
    assert scope.diagnostics.ambiguous_profiles == 2


def test_ambiguous_alias_text_not_exact():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "沈青", aliases=["沈二爷"]),
    ]
    state = _state()
    scope = build_candidate_scope(
        scene_facts=_facts("沈二爷坐于主位。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert set(_ids(scope)) == {"npc_001", "npc_002"}
    assert scope.diagnostics.ambiguous_variants == 1
    assert scope.diagnostics.ambiguous_profiles == 2


def test_ambiguous_alias_keeps_unambiguous_sibling_exact():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "沈青", aliases=["沈二爷"]),
        _profile("npc_003", "陆川"),
    ]
    state = _state(participants=["陆川"])
    scope = build_candidate_scope(
        scene_facts=_facts("沈二爷坐于主位。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == ["npc_003"]
    assert _ids(scope) == ["npc_003", "npc_001", "npc_002"]


def test_internal_space_alias_ambiguous():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈 二爷"]),
        _profile("npc_002", "沈青", aliases=["沈二爷"]),
    ]
    state = _state()
    scope = build_candidate_scope(
        scene_facts=_facts("沈二爷坐于主位。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert set(_ids(scope)) == {"npc_001", "npc_002"}
    assert scope.diagnostics.ambiguous_variants == 1
    assert scope.diagnostics.ambiguous_profiles == 2


def test_player_internal_space_name_excluded():
    state = _state(participants=["萧 云痕"], player="萧 云痕")
    profiles = [
        _profile("npc_001", "萧云痕"),
        _profile("npc_002", "陆川", aliases=["萧 云痕"]),
    ]
    scope = build_candidate_scope(
        scene_facts=_facts("萧云痕推门而入。"),
        current_state=state,
        profiles=profiles,
    )
    assert [o.npc_id for o in scope.exact_targets] == []
    assert _ids(scope) == []


def test_option_cap_rejects_above_ceiling():
    profiles = [_profile("npc_001", "沈孤鸿", role="醉花楼掌柜")]
    state = _state()
    with pytest.raises(ValueError, match="option_cap"):
        build_candidate_scope(
            scene_facts=_facts("醉花楼掌柜现身。"),
            current_state=state,
            profiles=profiles,
            option_cap=201,
        )


def test_exact_overflow_fails_fast():
    participants = [f"npc{i:03d}" for i in range(201)]
    profiles = [_profile(f"npc{i:03d}", f"npc{i:03d}") for i in range(201)]
    state = _state(participants=participants)
    with pytest.raises(ValueError, match="exact targets exceed"):
        build_candidate_scope(
            scene_facts=_facts("众人齐聚。"),
            current_state=state,
            profiles=profiles,
        )
