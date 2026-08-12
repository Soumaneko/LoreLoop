import pytest
from pydantic import ValidationError

from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.candidate_scope import identity_option_from_profile
from app.domain.npc_profile.changes import (
    NPCTurnValidationError,
    AcceptedNPCAbilityAddChange,
    AcceptedNPCScalarChange,
)
from app.domain.npc_profile.change_admission import admit_npc_turn_changes
from app.domain.npc_profile.schemas import KnownAbility, NPCNewProfileOutput, NPCProfile
from app.domain.npc_profile.turn_contract import (
    AliasMatchProposal,
    ExistingNPCProposal,
    IgnoreMentionProposal,
    NewNPCProposal,
    NPCIdentityOption,
    NPCTurnRequest,
    SceneParagraphRef,
)

_SCENE_FACTS = (
    '{"scene_focus": "沈孤鸿进入醉花楼。", '
    '"facts": [{"fact_id": "f1", "fact": "沈孤鸿让沈二关门。"}]}'
)
_PLAYER = "萧云痕"
_ADMITTED_AT = "2026-08-12T00:00:00Z"


def _profile(
    npc_id,
    name,
    *,
    aliases=None,
    transient=None,
    role=None,
    faction=None,
    appearance=None,
    personality_trait=None,
    public_summary=None,
    known_abilities=None,
    last_known_location=None,
    attitude_to_player=None,
    current_intent=None,
) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id,
        name=name,
        aliases=list(aliases or []),
        transient_aliases=dict(transient or {}),
        role=role,
        faction=faction,
        appearance=appearance,
        personality_trait=personality_trait,
        public_summary=public_summary,
        known_abilities=list(known_abilities or []),
        last_known_location=last_known_location,
        attitude_to_player=attitude_to_player,
        current_intent=current_intent,
    )


def _option(profile: NPCProfile) -> NPCIdentityOption:
    return identity_option_from_profile(profile)


def _request(
    profiles,
    *,
    exact_ids=None,
    evidence_refs=None,
    player=_PLAYER,
) -> NPCTurnRequest:
    options = [_option(profile) for profile in profiles]
    if exact_ids is None:
        exact_ids = [option.npc_id for option in options]
    return NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[option for option in options if option.npc_id in exact_ids],
        identity_options=options,
        protected_player_identity=player,
        scene_paragraph_evidence=[
            SceneParagraphRef(document_id=document_id, text=text)
            for document_id, text in (evidence_refs or [])
        ],
    )


def _evidence_documents(specs) -> dict[str, HistoryDocument]:
    return {
        spec["document_id"]: HistoryDocument(**spec)
        for spec in specs
    }


def _state(*, participants, location="醉花楼大堂", player=_PLAYER) -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location=location,
            participants=list(participants),
        ),
        player=PlayerState(name=player),
    )


def _admit(
    *,
    request,
    proposals,
    profiles,
    evidence_documents=None,
    current_state=None,
    turn_index=0,
    scene_id="scene_001",
    admitted_at=_ADMITTED_AT,
):
    return admit_npc_turn_changes(
        request=request,
        proposals=proposals,
        profiles=profiles,
        evidence_documents=evidence_documents or {},
        current_state=current_state or _state(participants=[]),
        turn_index=turn_index,
        scene_id=scene_id,
        admitted_at=admitted_at,
    )


def _existing(surface, target, *, current_patch=None, stable_changes=None):
    return ExistingNPCProposal(
        resolution="existing",
        surface=surface,
        context_spans=["沈孤鸿走进醉花楼。"],
        target_npc_id=target,
        current_patch=current_patch,
        stable_changes=list(stable_changes or []),
    )


def _alias(surface, target, *, persist_alias=False, **kwargs):
    return AliasMatchProposal(
        resolution="alias_match",
        surface=surface,
        context_spans=["沈二站定。"],
        target_npc_id=target,
        persist_alias=persist_alias,
        **kwargs,
    )


def _new(surface, name=None, **profile_fields):
    data = {"name": name or surface, "known_abilities": [], **profile_fields}
    return NewNPCProposal(
        resolution="new",
        surface=surface,
        context_spans=["一名无名老者叩门。"],
        new_profile=NPCNewProfileOutput.model_validate(data),
    )


def _scalar_field(field, value, evidence_ids, **kwargs):
    change = {"field": field, "value": value, "evidence_ids": evidence_ids}
    change.update(kwargs)
    return change


def _scalar_changes(result, npc_id):
    return [
        change
        for change in result.accepted_profile_changes
        if isinstance(change, AcceptedNPCScalarChange)
        and change.npc_id == npc_id
    ]


# --- accepted mutation 契约（union 禁止非法组合） ---


def test_scalar_change_requires_operation_set_shape():
    with pytest.raises(ValidationError):
        AcceptedNPCAbilityAddChange.model_validate({
            "npc_id": "n1",
            "canonical_name": "x",
            "field": "role",
            "additions": [{"name": "轻功", "description": "d"}],
            "evidence_ids": ["t0"],
        })
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1",
            "canonical_name": "x",
            "field": "known_abilities",
            "new_value": "y",
        })


def test_scalar_change_rejects_invalid_component_combinations():
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1", "canonical_name": "x", "field": "role",
            "new_value": "y", "summary_delta": "z", "evidence_ids": ["t0"],
        })
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1", "canonical_name": "x", "field": "public_summary",
            "new_value": "y", "evidence_ids": ["t0"],
        })
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1", "canonical_name": "x", "field": "last_known_location",
            "new_value": "y", "evidence_ids": ["t0"], "source": "current_state_sync",
        })
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1", "canonical_name": "x", "field": "role",
            "new_value": "y",
        })


def test_scalar_change_rejects_current_intent_with_evidence():
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange.model_validate({
            "npc_id": "n1", "canonical_name": "x", "field": "current_intent",
            "new_value": "y", "evidence_ids": ["t0"],
        })


def test_scalar_change_rejects_proposal_writing_sync_field():
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange(
            npc_id="n1", canonical_name="x", field="last_known_location",
            new_value="y", source="proposal",
        )
    with pytest.raises(ValidationError):
        AcceptedNPCScalarChange(
            npc_id="n1", canonical_name="x", field="attitude_to_player",
            new_value="y", source="proposal",
        )


def test_ability_add_change_rejects_duplicate_abilities():
    with pytest.raises(ValidationError):
        AcceptedNPCAbilityAddChange(
            npc_id="n1",
            canonical_name="x",
            additions=[
                KnownAbility(name="轻功", description="d1"),
                KnownAbility(name="轻功", description="d2"),
            ],
            evidence_ids=("t0",),
        )


def test_valid_scalar_and_ability_changes_parse():
    scalar = AcceptedNPCScalarChange(
        npc_id="n1", canonical_name="x", field="public_summary",
        old_value=None, new_value="v", evidence_ids=("t0",),
        summary_delta="delta",
    )
    ability = AcceptedNPCAbilityAddChange(
        npc_id="n1", canonical_name="x",
        additions=[KnownAbility(name="轻功", description="d")],
        evidence_ids=("t0",),
    )
    assert scalar.summary_delta == "delta"
    assert ability.field == "known_abilities"


# --- 硬校验与防御 ---


def test_admission_raises_on_hard_validation_failure():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    with pytest.raises(NPCTurnValidationError):
        _admit(request=request, proposals=[], profiles=[profile])


def test_admission_rejects_duplicate_profile_ids():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    proposal = _existing("沈孤鸿", "npc_001")
    with pytest.raises(ValueError, match="duplicate npc_ids"):
        _admit(
            request=request,
            proposals=[proposal],
            profiles=[profile, _profile("npc_001", "沈孤鸿")],
        )


def test_admission_rejects_target_not_in_profiles():
    options = [
        NPCIdentityOption(npc_id="npc_001", name="沈孤鸿"),
        NPCIdentityOption(npc_id="npc_999", name="沈孤鸿"),
    ]
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[options[1]],
        identity_options=options,
        protected_player_identity=_PLAYER,
    )
    proposal = _existing("沈孤鸿", "npc_999")
    with pytest.raises(ValueError, match="no matching profile"):
        _admit(
            request=request,
            proposals=[proposal],
            profiles=[_profile("npc_001", "沈孤鸿")],
        )


# --- current_intent ---


def test_current_intent_set_and_noop():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    proposal = _existing(
        "沈孤鸿", "npc_001",
        current_patch={"current_intent": "审问来客"},
    )
    result = _admit(request=request, proposals=[proposal], profiles=[profile])
    changes = _scalar_changes(result, "npc_001")
    assert [change.field for change in changes] == ["current_intent"]
    assert changes[0].new_value == "审问来客"
    assert result.updated_profiles[0].current_intent == "审问来客"

    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", current_patch={"current_intent": "审问来客"})],
        profiles=[_profile("npc_001", "沈孤鸿", current_intent="审问来客")],
    )
    assert _scalar_changes(result, "npc_001") == []


def test_current_intent_blank_is_noop():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", current_patch={"current_intent": "  "})],
        profiles=[profile],
    )
    assert _scalar_changes(result, "npc_001") == []


# --- stable fields + evidence gate ---


@pytest.mark.parametrize("field,current,proposed", [
    ("role", None, "合欢宗首位弟子"),
    ("appearance", None, "左眼有一条旧疤"),
    ("personality_trait", None, "沉稳"),
    ("faction", None, "合欢宗"),
])
def test_stable_scalar_field_set_and_noop(field, current, proposed):
    profile = _profile("npc_001", "沈孤鸿", **{field: current})
    request = _request([profile])
    change = _scalar_field(field, proposed, ["turn_0_p001"])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([{
            "document_id": "turn_0_p001",
            "source_type": "scene_paragraph",
            "turn_index": 0,
            "scene_id": "scene_001",
            "text": "正文。",
        }]),
        current_state=_state(participants=[]),
    )
    # 需要 request 带证据 ref
    result = _admit(
        request=_request([profile], evidence_refs=[("turn_0_p001", "正文。")]),
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([{
            "document_id": "turn_0_p001",
            "source_type": "scene_paragraph",
            "turn_index": 0,
            "scene_id": "scene_001",
            "text": "正文。",
        }]),
    )
    changes = _scalar_changes(result, "npc_001")
    assert [change.field for change in changes] == [field]
    assert changes[0].old_value == current
    assert changes[0].new_value == proposed
    assert getattr(result.updated_profiles[0], field) == proposed

    proposed_profile = _profile("npc_001", "沈孤鸿", **{field: proposed})
    noop = _admit(
        request=_request([proposed_profile], evidence_refs=[("turn_0_p001", "正文。")]),
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[proposed_profile],
        evidence_documents=_evidence_documents([{
            "document_id": "turn_0_p001",
            "source_type": "scene_paragraph",
            "turn_index": 0,
            "scene_id": "scene_001",
            "text": "正文。",
        }]),
    )
    assert _scalar_changes(noop, "npc_001") == []


def test_public_summary_set_carries_delta():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    change = _scalar_field(
        "public_summary", "合欢宗宗主之女。", ["turn_0_p001"],
        summary_delta="身份暴露为合欢宗宗主之女。",
    )
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([{
            "document_id": "turn_0_p001",
            "source_type": "scene_paragraph",
            "turn_index": 0,
            "scene_id": "scene_001",
            "text": "正文。",
        }]),
    )
    changes = _scalar_changes(result, "npc_001")
    assert changes[0].field == "public_summary"
    assert changes[0].summary_delta == "身份暴露为合欢宗宗主之女。"


def _evidence_setup(overrides=None):
    base = {
        "document_id": "turn_0_p001",
        "source_type": "scene_paragraph",
        "turn_index": 0,
        "scene_id": "scene_001",
        "text": "正文。",
    }
    if overrides:
        base.update(overrides)
    return base


@pytest.mark.parametrize("broken,overrides,refs", [
    ("unknown_id", {"document_id": "turn_0_p999"}, [("turn_0_p001", "正文。")]),
    ("wrong_source", {"source_type": "player_input"}, [("turn_0_p001", "正文。")]),
    ("wrong_turn", {"turn_index": 1}, [("turn_0_p001", "正文。")]),
    ("wrong_scene", {"scene_id": "scene_999"}, [("turn_0_p001", "正文。")]),
    ("text_mismatch", {}, [("turn_0_p001", "另一段正文。")]),
    ("missing_request_ref", {}, []),
])
def test_evidence_gate_drops_invalid_references(broken, overrides, refs):
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=refs)
    change = _scalar_field("role", "合欢宗首位弟子", ["turn_0_p001"])
    doc = _evidence_setup(overrides)
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([doc]),
    )
    assert _scalar_changes(result, "npc_001") == []
    assert result.updated_profiles == []
    assert result.warnings
    assert result.warnings[0].code == "change_evidence_dropped"
    assert result.warnings[0].field_name == "role"


def test_evidence_gate_drops_whole_change_on_any_invalid_ref():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    change = _scalar_field("role", "合欢宗首位弟子", ["turn_0_p001", "turn_0_p999"])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([_evidence_setup()]),
    )
    assert _scalar_changes(result, "npc_001") == []
    assert result.warnings[0].evidence_ids == ("turn_0_p999",)


def test_valid_evidence_accepted():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    change = _scalar_field("role", "合欢宗首位弟子", ["turn_0_p001"])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents=_evidence_documents([_evidence_setup()]),
    )
    assert result.warnings == []
    assert [change.field for change in _scalar_changes(result, "npc_001")] == ["role"]


# --- ability append-only ---


def _ability(change):
    return {"field": "known_abilities", "ability": change, "evidence_ids": ["turn_0_p001"]}


def test_ability_add_appends_and_dedupes():
    profile = _profile(
        "npc_001", "沈孤鸿",
        known_abilities=[KnownAbility(name="轻功", description="身法迅捷。")],
    )
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[
            _ability({"name": "轻功", "description": "再次提出。"}),
            _ability({"name": "隐匿身法", "description": "潜行无息。"}),
        ])],
        profiles=[profile],
        evidence_documents=_evidence_documents([_evidence_setup()]),
    )
    adds = [
        change for change in result.accepted_profile_changes
        if isinstance(change, AcceptedNPCAbilityAddChange)
    ]
    assert len(adds) == 1
    assert adds[0].additions[0].name == "隐匿身法"
    updated = result.updated_profiles[0]
    assert [a.name for a in updated.known_abilities] == ["轻功", "隐匿身法"]


def test_ability_add_caps_at_five():
    existing = [
        KnownAbility(name=f"技能{i}", description="d")
        for i in range(4)
    ]
    profile = _profile("npc_001", "沈孤鸿", known_abilities=existing)
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[
            _ability({"name": "新技一", "description": "d"}),
            _ability({"name": "新技二", "description": "d"}),
        ])],
        profiles=[profile],
        evidence_documents=_evidence_documents([_evidence_setup()]),
    )
    adds = [
        change for change in result.accepted_profile_changes
        if isinstance(change, AcceptedNPCAbilityAddChange)
    ]
    assert len(adds) == 1
    assert len(result.updated_profiles[0].known_abilities) == 5


# --- alias ---


def test_alias_persist_applied_and_noop():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], exact_ids=())
    result = _admit(
        request=request,
        proposals=[_alias("沈二爷", "npc_001", persist_alias=True)],
        profiles=[profile],
    )
    assert [a.alias for a in result.accepted_alias_changes] == ["沈二爷"]
    assert result.updated_profiles[0].aliases == ["沈二爷"]

    noop = _admit(
        request=request,
        proposals=[_alias("沈孤鸿", "npc_001", persist_alias=True)],
        profiles=[profile],
    )
    assert noop.accepted_alias_changes == []
    assert noop.updated_profiles == []


def test_alias_persist_false_skipped():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], exact_ids=())
    result = _admit(
        request=request,
        proposals=[_alias("沈二爷", "npc_001", persist_alias=False)],
        profiles=[profile],
    )
    assert result.accepted_alias_changes == []


def test_alias_conflicting_with_other_npc_skipped():
    profiles = [
        _profile("npc_001", "沈孤鸿"),
        _profile("npc_002", "沈二爷"),
    ]
    request = _request(profiles, exact_ids=())
    result = _admit(
        request=request,
        proposals=[_alias("沈二爷", "npc_001", persist_alias=True)],
        profiles=profiles,
    )
    assert result.accepted_alias_changes == []
    assert result.warnings
    assert result.warnings[0].code == "alias_conflicts_with_other_npc"


# --- new candidates ---


def test_new_candidate_uses_deterministic_draft_id():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], exact_ids=())
    result = _admit(
        request=request,
        proposals=[_new("无名老者", name="无名老者")],
        profiles=[profile],
    )
    assert len(result.new_npc_candidates) == 1
    candidate = result.new_npc_candidates[0]
    assert candidate.npc_id == "draft:无名老者"
    assert candidate.source_scene_ids == ["scene_001"]
    assert candidate.updated_at == _ADMITTED_AT
    assert candidate.npc_id not in {p.npc_id for p in result.updated_profiles}


def test_new_candidate_ignores_model_location_and_attitude():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], exact_ids=())
    result = _admit(
        request=request,
        proposals=[_new("无名老者", name="无名老者", last_known_location="城南", attitude_to_player="友好")],
        profiles=[profile],
    )
    candidate = result.new_npc_candidates[0]
    assert candidate.last_known_location is None
    assert candidate.attitude_to_player is None


def test_new_candidate_synced_when_participant():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], exact_ids=())
    state = _state(participants=["无名老者"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_new("无名老者", name="无名老者")],
        profiles=[profile],
        current_state=state,
    )
    candidate = result.new_npc_candidates[0]
    assert candidate.last_known_location == "醉花楼大堂"
    assert result.coverage is not None
    assert result.coverage.synced_npcs == 1


# --- CurrentState 同步 ---


def test_sync_in_scene_location_and_attitude():
    profile = _profile("npc_001", "沈孤鸿", last_known_location="醉花楼")
    request = _request([profile])
    state = _state(
        participants=[{"name": "沈孤鸿", "attitude_to_player": "戒备"}],
        location="醉花楼大堂",
    )
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = _scalar_changes(result, "npc_001")
    assert {(c.field, c.source, c.new_value) for c in changes} == {
        ("last_known_location", "current_state_sync", "醉花楼大堂"),
        ("attitude_to_player", "current_state_sync", "戒备"),
    }
    updated = result.updated_profiles[0]
    assert updated.last_known_location == "醉花楼大堂"
    assert updated.attitude_to_player == "戒备"


def test_sync_noop_when_already_equal():
    profile = _profile("npc_001", "沈孤鸿", last_known_location="醉花楼大堂")
    request = _request([profile])
    state = _state(participants=["沈孤鸿"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    assert _scalar_changes(result, "npc_001") == []
    assert result.updated_profiles == []


def test_sync_attitude_only_when_non_none():
    profile = _profile("npc_001", "沈孤鸿", attitude_to_player="友善")
    request = _request([profile])
    state = _state(participants=["沈孤鸿"])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = _scalar_changes(result, "npc_001")
    assert all(c.field == "last_known_location" for c in changes)
    updated = result.updated_profiles[0]
    assert updated.attitude_to_player == "友善"


def test_sync_ambiguous_participant_fails_closed():
    profiles = [
        _profile("npc_001", "沈孤鸿", aliases=["沈二爷"]),
        _profile("npc_002", "沈青", aliases=["沈二爷"]),
    ]
    request = _request(profiles, exact_ids=("npc_001",))
    state = _state(participants=["沈二爷"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=profiles,
        current_state=state,
    )
    assert _scalar_changes(result, "npc_001") == []
    assert _scalar_changes(result, "npc_002") == []
    assert result.updated_profiles == []


def test_sync_skips_player():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles)
    state = _state(participants=["萧云痕", "沈孤鸿"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=profiles,
        current_state=state,
    )
    assert [c.field for c in _scalar_changes(result, "npc_001")] == ["last_known_location"]


# --- snapshot 与应用 ---


def test_snapshot_only_from_accepted_changes_and_aliases():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=[("turn_0_p001", "正文。")])
    result = _admit(
        request=request,
        proposals=[
            _existing("沈孤鸿", "npc_001", stable_changes=[
                _scalar_field("role", "合欢宗首位弟子", ["turn_0_p001"]),
            ]),
            _alias("沈二爷", "npc_001", persist_alias=True),
        ],
        profiles=[profile],
        evidence_documents=_evidence_documents([_evidence_setup()]),
    )
    updated = result.updated_profiles[0]
    assert updated.role == "合欢宗首位弟子"
    assert updated.aliases == ["沈二爷"]
    # 原输入档案不受影响
    assert profile.role is None
    assert profile.aliases == []
    assert profile.updated_at == ""


def test_updated_profiles_sorted_and_deterministic():
    profiles = [
        _profile("npc_002", "陆川"),
        _profile("npc_001", "沈孤鸿"),
    ]
    request = _request(profiles)
    proposals = [
        _existing("沈孤鸿", "npc_001", current_patch={"current_intent": "审问"}),
        _existing("陆川", "npc_002", current_patch={"current_intent": "留意"}),
    ]
    state = _state(participants=["沈孤鸿", "陆川"], location="醉花楼大堂")
    first = _admit(
        request=request,
        proposals=proposals,
        profiles=profiles,
        current_state=state,
    )
    second = _admit(
        request=request,
        proposals=proposals,
        profiles=profiles,
        current_state=state,
    )
    assert first == second
    assert [p.npc_id for p in first.updated_profiles] == ["npc_001", "npc_002"]
    assert first.coverage is not None
    assert first.coverage.existing_proposals == 2
    assert first.coverage.synced_npcs == 2


def test_admission_rejects_scene_id_mismatch():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    state = _state(participants=["沈孤鸿"], location="醉花楼大堂")
    with pytest.raises(ValueError, match="scene_id mismatch"):
        _admit(
            request=request,
            proposals=[_existing("沈孤鸿", "npc_001")],
            profiles=[profile],
            current_state=state,
            scene_id="scene_999",
        )


def test_current_intent_same_value_deduped_across_proposals():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    result = _admit(
        request=request,
        proposals=[
            _existing("沈孤鸿", "npc_001", current_patch={"current_intent": "审问"}),
            _alias("合欢宗宗主", "npc_001", current_patch={"current_intent": "审问"}),
        ],
        profiles=[profile],
    )
    changes = _scalar_changes(result, "npc_001")
    assert [c.field for c in changes] == ["current_intent"]
    assert len(changes) == 1
    assert result.updated_profiles[0].current_intent == "审问"


def test_sync_dedupes_same_profile_equivalent_variants():
    profile = _profile("npc_001", "沈孤鸿", aliases=["沈 孤鸿"])
    request = _request([profile])
    state = _state(participants=["沈孤鸿"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = _scalar_changes(result, "npc_001")
    assert [c.field for c in changes] == ["last_known_location"]
    assert result.updated_profiles[0].last_known_location == "醉花楼大堂"


def test_candidate_conflicting_alias_stripped_and_existing_synced():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles, exact_ids=("npc_001",))
    state = _state(participants=["沈孤鸿"], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[
            _existing("沈孤鸿", "npc_001"),
            _new("无名老者", name="无名老者", aliases=["沈孤鸿"]),
        ],
        profiles=profiles,
        current_state=state,
    )
    # 冲突 alias 被剥离 + warning；参与者唯一解析到既有档案 → 正常同步
    candidate = result.new_npc_candidates[0]
    assert candidate.aliases == []
    assert result.warnings
    assert result.warnings[0].code == "candidate_alias_conflict_stripped"
    assert [c.field for c in _scalar_changes(result, "npc_001")] == [
        "last_known_location",
    ]
    assert result.updated_profiles[0].last_known_location == "醉花楼大堂"
    assert result.coverage is not None
    assert result.coverage.synced_npcs == 1


def test_candidate_name_collision_fails_closed():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles, exact_ids=())
    with pytest.raises(ValueError, match="collides with an existing identity variant"):
        _admit(
            request=request,
            proposals=[_new("沈孤鸿", name="沈孤鸿")],
            profiles=profiles,
        )


def test_candidate_name_collision_with_existing_alias_fails_closed():
    profiles = [_profile("npc_001", "沈孤鸿", aliases=["沈二爷"])]
    request = _request(profiles, exact_ids=())
    with pytest.raises(ValueError, match="collides with an existing identity variant"):
        _admit(
            request=request,
            proposals=[_new("沈二爷", name="沈二爷")],
            profiles=profiles,
        )


def test_candidate_alias_conflict_between_new_candidates_stripped():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles, exact_ids=("npc_001",))
    result = _admit(
        request=request,
        proposals=[
            _existing("沈孤鸿", "npc_001"),
            _new("无名老者", name="无名老者", aliases=["铁匠"]),
            _new("李铁匠", name="李铁匠", aliases=["铁匠"]),
        ],
        profiles=profiles,
    )
    aliases_by_name = {
        candidate.name: candidate.aliases
        for candidate in result.new_npc_candidates
    }
    assert aliases_by_name["无名老者"] == []
    assert aliases_by_name["李铁匠"] == []
    assert any(
        warning.code == "candidate_alias_conflict_stripped"
        for warning in result.warnings
    )


def test_sync_aggregates_same_attitude_across_surfaces():
    profile = _profile("npc_001", "沈孤鸿", aliases=["沈二爷"])
    request = _request([profile])
    state = _state(participants=[
        {"name": "沈孤鸿", "attitude_to_player": "友好"},
        {"name": "沈二爷", "attitude_to_player": "友好"},
    ], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = _scalar_changes(result, "npc_001")
    assert {(c.field, c.new_value) for c in changes} == {
        ("last_known_location", "醉花楼大堂"),
        ("attitude_to_player", "友好"),
    }
    assert len([c for c in changes if c.field == "attitude_to_player"]) == 1
    assert len([c for c in changes if c.field == "last_known_location"]) == 1


def test_sync_conflicting_attitudes_fail_closed():
    profile = _profile("npc_001", "沈孤鸿", aliases=["沈二爷"])
    request = _request([profile])
    state = _state(participants=[
        {"name": "沈孤鸿", "attitude_to_player": "友好"},
        {"name": "沈二爷", "attitude_to_player": "戒备"},
    ], location="醉花楼大堂")
    with pytest.raises(ValueError, match="conflicting participant attitudes"):
        _admit(
            request=request,
            proposals=[_existing("沈孤鸿", "npc_001")],
            profiles=[profile],
            current_state=state,
        )


def test_sync_attitude_cleaned_before_dedup():
    profile = _profile("npc_001", "沈孤鸿", aliases=["沈二爷"])
    request = _request([profile])
    state = _state(participants=[
        {"name": "沈孤鸿", "attitude_to_player": "友好"},
        {"name": "沈二爷", "attitude_to_player": " 友好 "},
    ], location="醉花楼大堂")
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = [c for c in _scalar_changes(result, "npc_001") if c.field == "attitude_to_player"]
    assert len(changes) == 1
    assert changes[0].new_value == "友好"


def test_sync_attitude_cleaned_before_write():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile])
    state = _state(participants=[{"name": "沈孤鸿", "attitude_to_player": " 友好 "}])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[profile],
        current_state=state,
    )
    changes = [c for c in _scalar_changes(result, "npc_001") if c.field == "attitude_to_player"]
    assert len(changes) == 1
    assert changes[0].new_value == "友好"
    assert result.updated_profiles[0].attitude_to_player == "友好"

    noop = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001")],
        profiles=[_profile("npc_001", "沈孤鸿", attitude_to_player="友好")],
        current_state=state,
    )
    changes = [c for c in _scalar_changes(noop, "npc_001") if c.field == "attitude_to_player"]
    assert changes == []


def test_candidate_aliases_deterministically_cleaned():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles, exact_ids=())
    result = _admit(
        request=request,
        proposals=[_new("无名老者", name="无名老者", aliases=["老者", "老 者", "无名老者"])],
        profiles=profiles,
    )
    candidate = result.new_npc_candidates[0]
    assert candidate.aliases == ["老者"]
    assert all(
        warning.code != "candidate_alias_conflict_stripped"
        for warning in result.warnings
    )


def test_alias_conflict_is_order_independent():
    profiles = [_profile("npc_001", "沈孤鸿")]
    request = _request(profiles, exact_ids=())
    proposals = [
        _alias("沈二爷", "npc_001", persist_alias=True),
        _new("无名老者", name="无名老者", aliases=["沈二爷"]),
    ]
    result = _admit(request=request, proposals=proposals, profiles=profiles)
    assert result.accepted_alias_changes == []
    assert result.warnings
    assert result.warnings[0].code == "alias_conflicts_with_other_npc"


def test_evidence_gate_rejects_key_document_id_mismatch():
    profile = _profile("npc_001", "沈孤鸿")
    request = _request([profile], evidence_refs=[("shown-id", "正文。")])
    change = _scalar_field("role", "合欢宗首位弟子", ["shown-id"])
    result = _admit(
        request=request,
        proposals=[_existing("沈孤鸿", "npc_001", stable_changes=[change])],
        profiles=[profile],
        evidence_documents={
            "shown-id": HistoryDocument(
                document_id="different-id",
                source_type="scene_paragraph",
                turn_index=0,
                scene_id="scene_001",
                text="正文。",
            )
        },
    )
    assert _scalar_changes(result, "npc_001") == []
    assert result.warnings
    assert result.warnings[0].code == "change_evidence_dropped"


def test_admission_rejects_stale_identity_projection():
    option = NPCIdentityOption(npc_id="npc_001", name="陆川")
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[option],
        identity_options=[option],
        protected_player_identity=_PLAYER,
    )
    actual = _profile("npc_001", "沈孤鸿")
    with pytest.raises(ValueError, match="does not match the actual profile projection"):
        _admit(
            request=request,
            proposals=[_existing("陆川", "npc_001")],
            profiles=[actual],
        )


def test_admission_rejects_unreferenced_stale_identity_option():
    good = identity_option_from_profile(_profile("npc_001", "沈孤鸿"))
    stale = NPCIdentityOption(npc_id="npc_999", name="查无此人")
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[],
        identity_options=[good, stale],
        protected_player_identity=_PLAYER,
    )
    with pytest.raises(ValueError, match="no matching profile"):
        _admit(
            request=request,
            proposals=[],
            profiles=[_profile("npc_001", "沈孤鸿")],
        )


def test_coverage_counts_resolutions():
    profiles = [
        _profile("npc_001", "沈孤鸿"),
        _profile("npc_002", "陆川"),
    ]
    request = _request(profiles, exact_ids=("npc_001",))
    proposals = [
        _existing("沈孤鸿", "npc_001"),
        _alias("沈二爷", "npc_002", persist_alias=False),
        _new("无名老者", name="无名老者"),
        IgnoreMentionProposal(
            resolution="ignore", surface="醉花楼", context_spans=["醉花楼是酒楼。"],
        ),
    ]
    result = _admit(request=request, proposals=proposals, profiles=profiles)
    assert result.coverage is not None
    assert result.coverage.exact_targets == 1
    assert result.coverage.existing_proposals == 1
    assert result.coverage.alias_proposals == 1
    assert result.coverage.new_proposals == 1
    assert result.coverage.ignore_proposals == 1
    assert result.coverage.unresolved_surfaces == ["沈二爷"]
