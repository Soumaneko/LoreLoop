import json
from typing import cast

import pytest
from pydantic import ValidationError

from app.core.llm.schema import schema_json
from app.domain.npc_profile.schemas import LLMKnownAbility, NPCNewProfileOutput
from app.domain.npc_profile.turn_contract import (
    MAX_EXACT_TARGETS,
    MAX_IDENTITY_OPTIONS,
    MAX_PROPOSALS,
    NPCTurnProposal,
    NPCTurnProposalBatch,
    NPCTurnRequest,
    PublicSummaryChange,
    ScalarStableChange,
    validate_npc_turn_proposals,
)
from app.domain.npc_profile.turn_contract import (
    AbilityAddChange,
    AliasMatchProposal,
    ExistingNPCProposal,
    IgnoreMentionProposal,
    NewNPCProposal,
)
from app.domain.npc_profile.turn_contract import NPCIdentityOption

_SCENE_FACTS = (
    '{"scene_focus": "沈孤鸿进入醉花楼。", '
    '"facts": [{"fact_id": "f1", "fact": "沈孤鸿让沈二关门。"}]}'
)

_NAMES = {
    "npc_001": "沈孤鸿",
    "npc_002": "陆川",
}


def _option(npc_id: str, **kwargs) -> NPCIdentityOption:
    return NPCIdentityOption(
        npc_id=npc_id,
        name=_NAMES[npc_id],
        **kwargs,
    )


def _request(
    *,
    exact_ids=("npc_001",),
    option_ids=("npc_001", "npc_002"),
    player="萧云痕",
    **kwargs,
) -> NPCTurnRequest:
    options = [_option(npc_id) for npc_id in option_ids]
    data = {
        "scene_facts": _SCENE_FACTS,
        "exact_targets": [option for option in options if option.npc_id in exact_ids],
        "identity_options": options,
        "protected_player_identity": player,
    }
    data.update(kwargs)
    return NPCTurnRequest(**data)


def _existing(**kwargs) -> NPCTurnProposal:
    data = {
        "surface": "沈孤鸿",
        "context_spans": ["沈孤鸿走进醉花楼。"],
        "target_npc_id": "npc_001",
    }
    data.update(kwargs)
    return ExistingNPCProposal(resolution="existing", **data)


def _alias(**kwargs) -> NPCTurnProposal:
    data = {
        "surface": "沈二",
        "context_spans": ["沈二站定。"],
        "target_npc_id": "npc_001",
    }
    data.update(kwargs)
    return AliasMatchProposal(resolution="alias_match", **data)


def _new(**kwargs) -> NPCTurnProposal:
    data = {
        "surface": "无名老者",
        "context_spans": ["一名无名老者叩门。"],
        "name": "无名老者",
    }
    data.update(kwargs)
    return NewNPCProposal(
        resolution="new",
        new_profile=NPCNewProfileOutput(name=data.pop("name")),
        **data,
    )


def _ignore(**kwargs) -> NPCTurnProposal:
    data = {
        "surface": "醉花楼",
        "context_spans": ["醉花楼是酒楼。"],
    }
    data.update(kwargs)
    return IgnoreMentionProposal(resolution="ignore", **data)


def _as_existing(proposal: NPCTurnProposal) -> ExistingNPCProposal:
    return cast(ExistingNPCProposal, proposal)


def _as_alias(proposal: NPCTurnProposal) -> AliasMatchProposal:
    return cast(AliasMatchProposal, proposal)


def _proposals(*items: NPCTurnProposal) -> list[NPCTurnProposal]:
    return list(items)


def _batch(proposals: list[NPCTurnProposal]) -> NPCTurnProposalBatch:
    return NPCTurnProposalBatch(proposals=proposals)


# --- schema: discriminated union ---


def test_proposal_batch_schema_uses_discriminated_union():
    schema = schema_json(NPCTurnProposalBatch)
    assert '"oneOf"' in schema
    assert '"propertyName":"resolution"' in schema.replace(" ", "")
    assert '"propertyName":"field"' in schema.replace(" ", "")


def test_all_four_proposal_variants_parse():
    batch = _batch(_proposals(_existing(), _alias(), _new(), _ignore()))
    assert [p.resolution for p in batch.proposals] == [
        "existing",
        "alias_match",
        "new",
        "ignore",
    ]


def test_ignore_cannot_carry_target_npc_id():
    with pytest.raises(ValidationError):
        _batch(_proposals(_ignore(target_npc_id="npc_001")))


def test_existing_requires_target_npc_id():
    with pytest.raises(ValidationError):
        _batch(_proposals(_existing(target_npc_id=None)))


def test_new_requires_new_profile():
    with pytest.raises(ValidationError):
        NewNPCProposal.model_validate({
            "resolution": "new",
            "surface": "无名老者",
            "context_spans": ["叩门。"],
        })


def test_variant_rejects_unexpected_fields():
    with pytest.raises(ValidationError):
        _ignore(new_profile={"name": "x"})


def test_alias_persist_alias_defaults_false():
    assert _as_alias(_alias()).persist_alias is False
    assert _as_alias(_alias(persist_alias=True)).persist_alias is True


def test_existing_carries_current_patch_and_stable_changes():
    proposal = _as_existing(_existing(
        current_patch={"current_intent": "审问来客"},
        stable_changes=[
            {"field": "role", "value": "合欢宗首位弟子", "evidence_ids": ["turn_0_p001"]}
        ],
    ))
    assert proposal.current_patch is not None
    assert proposal.current_patch.current_intent == "审问来客"
    assert proposal.stable_changes[0].field == "role"


# --- schema: field changes ---


def test_public_summary_change_requires_summary_delta():
    with pytest.raises(ValidationError):
        PublicSummaryChange.model_validate({
            "field": "public_summary",
            "value": "v",
            "evidence_ids": ["t0"],
        })


def test_public_summary_change_carries_delta():
    change = PublicSummaryChange(
        field="public_summary",
        value="合欢宗宗主之女。",
        summary_delta="身份暴露为合欢宗宗主之女。",
        evidence_ids=["turn_0_p001"],
    )
    assert change.summary_delta == "身份暴露为合欢宗宗主之女。"


def test_ability_add_change_is_append_only():
    change = AbilityAddChange(
        field="known_abilities",
        ability=LLMKnownAbility(name="轻功", description="身法迅捷。"),
        evidence_ids=["turn_0_p001"],
    )
    assert change.ability.name == "轻功"


def test_scalar_stable_change_field_is_constrained():
    with pytest.raises(ValidationError):
        ScalarStableChange.model_validate({
            "field": "known_abilities",
            "value": "v",
            "evidence_ids": ["t0"],
        })


@pytest.mark.parametrize("evidence_ids", [
    [],
    ["a", "a"],
    ["a", "b", "c", "d", "e"],
])
def test_evidence_ids_rejects_empty_duplicate_or_overflow(evidence_ids):
    with pytest.raises(ValidationError):
        ScalarStableChange(
            field="role",
            value="x",
            evidence_ids=evidence_ids,
        )


def test_evidence_ids_order_preserved():
    change = ScalarStableChange(
        field="role",
        value="x",
        evidence_ids=["turn_0_p002", "turn_0_p001"],
    )
    assert change.evidence_ids == ["turn_0_p002", "turn_0_p001"]


def test_proposal_defensive_limits():
    with pytest.raises(ValidationError):
        _existing(context_spans=[])
    with pytest.raises(ValidationError):
        _existing(context_spans=["a"] * 9)
    with pytest.raises(ValidationError):
        _existing(
            stable_changes=[
                {"field": "role", "value": f"v{i}", "evidence_ids": ["t0"]}
                for i in range(9)
            ]
        )
    with pytest.raises(ValidationError):
        _batch(_proposals(*([_ignore()] * (MAX_PROPOSALS + 1))))


def test_proposal_cap_consistent_with_exact_cap():
    assert MAX_PROPOSALS >= MAX_EXACT_TARGETS
    assert MAX_EXACT_TARGETS <= MAX_IDENTITY_OPTIONS


# --- request invariants ---


def test_valid_request_parses():
    request = _request()
    assert request.exact_targets[0].npc_id == "npc_001"


def test_request_rejects_invalid_scene_facts():
    with pytest.raises(ValidationError):
        _request(scene_facts="{not json")


def test_request_rejects_duplicate_exact_npc_ids():
    data = {
        "scene_facts": _SCENE_FACTS,
        "exact_targets": [_option("npc_001"), _option("npc_001")],
        "identity_options": [_option("npc_001"), _option("npc_002")],
        "protected_player_identity": "萧云痕",
    }
    with pytest.raises(ValidationError, match="exact_targets npc_id values must be unique"):
        NPCTurnRequest(**data)


def test_request_rejects_duplicate_identity_npc_ids():
    data = {
        "scene_facts": _SCENE_FACTS,
        "exact_targets": [],
        "identity_options": [_option("npc_001"), _option("npc_001")],
        "protected_player_identity": "萧云痕",
    }
    with pytest.raises(ValidationError, match="identity_options npc_id values must be unique"):
        NPCTurnRequest(**data)


def test_request_rejects_exact_target_outside_identity_options():
    data = {
        "scene_facts": _SCENE_FACTS,
        "exact_targets": [
            NPCIdentityOption(npc_id="npc_999", name="查无此人"),
        ],
        "identity_options": [_option("npc_001"), _option("npc_002")],
        "protected_player_identity": "萧云痕",
    }
    with pytest.raises(ValidationError, match="missing from identity_options"):
        NPCTurnRequest(**data)


def test_request_rejects_mismatched_projection():
    request_data = {
        "scene_facts": _SCENE_FACTS,
        "exact_targets": [
            NPCIdentityOption(
                npc_id="npc_001",
                name="沈孤鸿",
                role="醉花楼掌柜",
            )
        ],
        "identity_options": [_option("npc_001")],
        "protected_player_identity": "萧云痕",
    }
    with pytest.raises(ValidationError, match="projection differs"):
        NPCTurnRequest(**request_data)


def test_request_rejects_player_identity_option():
    with pytest.raises(ValidationError, match="protected player identity"):
        _request(player="陆川")


# --- hard validation ---


def test_validation_happy_path():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(), _alias(), _ignore()),
    )
    assert errors == []


def test_validation_unknown_target_npc_id():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(target_npc_id="npc_999")),
    )
    assert [e.code for e in errors] == [
        "exact_target_missing",
        "existing_without_exact_target",
        "unknown_target_npc_id",
    ]


def test_validation_existing_without_exact_target():
    request = _request(exact_ids=("npc_001",))
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(target_npc_id="npc_002", surface="陆川")),
    )
    assert "unknown_target_npc_id" not in [e.code for e in errors]
    assert [e.code for e in errors] == [
        "exact_target_missing",
        "existing_without_exact_target",
    ]


def test_validation_exact_target_missing():
    request = _request()
    errors = validate_npc_turn_proposals(request, _proposals(_ignore()))
    assert [e.code for e in errors] == ["exact_target_missing"]
    assert errors[0].npc_id == "npc_001"


def test_validation_duplicate_surface():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(), _existing(surface="沈孤鸿 ")),
    )
    assert [e.code for e in errors] == ["duplicate_surface"]


def test_validation_duplicate_stable_field_across_proposals():
    request = _request()
    role_change = {
        "field": "role",
        "value": "合欢宗首位弟子",
        "evidence_ids": ["turn_0_p001"],
    }
    errors = validate_npc_turn_proposals(
        request,
        _proposals(
            _existing(surface="沈孤鸿", stable_changes=[role_change]),
            _alias(surface="合欢宗宗主", stable_changes=[role_change]),
        ),
    )
    assert [e.code for e in errors] == ["duplicate_stable_field"]
    assert errors[0].npc_id == "npc_001"
    assert errors[0].field_name == "role"


def test_validation_duplicate_ability_add():
    request = _request()
    ability_change = {
        "field": "known_abilities",
        "ability": {"name": "轻功", "description": "身法迅捷。"},
        "evidence_ids": ["turn_0_p001"],
    }
    errors = validate_npc_turn_proposals(
        request,
        _proposals(
            _existing(stable_changes=[ability_change]),
            _alias(surface="沈二", stable_changes=[ability_change]),
        ),
    )
    assert [e.code for e in errors] == ["duplicate_ability_add"]


def test_validation_player_surface():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(surface="萧云痕")),
    )
    assert [e.code for e in errors] == ["player_surface"]


def test_validation_player_new_profile():
    request = _request(player="萧云痕")
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_new(surface="萧云痕", name="萧云痕")),
    )
    assert "player_new_profile" in [e.code for e in errors]


def test_validation_new_profile_name_mismatch():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_new(name="青袍老者")),
    )
    assert [e.code for e in errors] == [
        "exact_target_missing",
        "new_profile_name_mismatch",
    ]


def test_validation_error_order_is_deterministic():
    request = _request()
    role_change = {
        "field": "role",
        "value": "x",
        "evidence_ids": ["turn_0_p001"],
    }
    proposals = _proposals(
        _existing(surface="萧云痕", stable_changes=[role_change]),
        _existing(surface="萧云痕 ", stable_changes=[role_change]),
        _alias(surface="合欢宗宗主", stable_changes=[role_change]),
    )
    first = validate_npc_turn_proposals(request, proposals)
    second = validate_npc_turn_proposals(request, proposals)
    assert first == second
    assert [error.code for error in first] == [
        "duplicate_stable_field",
        "duplicate_surface",
        "player_surface",
        "player_surface",
    ]
    indexes = [
        error.proposal_index for error in first if error.proposal_index is not None
    ]
    assert indexes == sorted(indexes)


def test_validation_accepts_multiple_distinct_ability_adds():
    request = _request()
    changes = [
        {
            "field": "known_abilities",
            "ability": {"name": f"技能{i}", "description": "描述。"},
            "evidence_ids": ["turn_0_p001"],
        }
        for i in range(2)
    ]
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(stable_changes=changes)),
    )
    assert errors == []


def test_validation_existing_surface_mismatch():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(surface="完全无关的人")),
    )
    assert [e.code for e in errors] == ["existing_surface_mismatch"]
    assert errors[0].npc_id == "npc_001"


def test_validation_existing_surface_matches_alias():
    options = [
        NPCIdentityOption(npc_id="npc_001", name="沈孤鸿", aliases=["沈二爷"]),
        NPCIdentityOption(npc_id="npc_002", name="陆川"),
    ]
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[options[0]],
        identity_options=list(options),
        protected_player_identity="萧云痕",
    )
    errors = validate_npc_turn_proposals(
        request,
        _proposals(ExistingNPCProposal(
            resolution="existing",
            surface="沈二爷",
            context_spans=["沈二站定。"],
            target_npc_id="npc_001",
        )),
    )
    assert errors == []


def test_validation_existing_surface_matches_internal_space_alias():
    options = [
        NPCIdentityOption(npc_id="npc_001", name="沈孤鸿", aliases=["沈 二爷"]),
        NPCIdentityOption(npc_id="npc_002", name="陆川"),
    ]
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=[options[0]],
        identity_options=list(options),
        protected_player_identity="萧云痕",
    )
    errors = validate_npc_turn_proposals(
        request,
        _proposals(ExistingNPCProposal(
            resolution="existing",
            surface="沈二爷",
            context_spans=["沈二站定。"],
            target_npc_id="npc_001",
        )),
    )
    assert errors == []


def test_validation_duplicate_surface_collapses_internal_space():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(
            _existing(surface="沈孤鸿"),
            _existing(surface="沈 孤鸿"),
        ),
    )
    assert [e.code for e in errors] == ["duplicate_surface"]


def test_validation_player_new_profile_internal_space_alias():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(NewNPCProposal(
            resolution="new",
            surface="路人",
            context_spans=["一名路人经过。"],
            new_profile=NPCNewProfileOutput(
                name="路人",
                aliases=["萧 云痕"],
                known_abilities=[],
            ),
        )),
    )
    assert "player_new_profile" in [e.code for e in errors]


def test_validation_new_profile_name_mismatch_collapses_internal_space():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(NewNPCProposal(
            resolution="new",
            surface="无名 老者",
            context_spans=["叩门。"],
            new_profile=NPCNewProfileOutput(
                name="无名老者",
                known_abilities=[],
            ),
        )),
    )
    assert [e.code for e in errors] == [
        "exact_target_missing",
    ]


def test_validation_player_surface_collapses_internal_space():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(surface="萧 云痕")),
    )
    assert [e.code for e in errors] == ["player_surface"]


def test_validation_player_surface_skips_mismatch_noise():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(_existing(surface="萧云痕")),
    )
    assert [e.code for e in errors] == ["player_surface"]


def test_validation_player_new_profile_alias():
    request = _request()
    errors = validate_npc_turn_proposals(
        request,
        _proposals(NewNPCProposal(
            resolution="new",
            surface="路人",
            context_spans=["一名路人经过。"],
            new_profile=NPCNewProfileOutput(
                name="路人",
                aliases=["萧云痕"],
                known_abilities=[],
            ),
        )),
    )
    assert [e.code for e in errors] == [
        "exact_target_missing",
        "player_new_profile",
    ]


def test_exact_coverage_satisfiable_at_max():
    count = MAX_IDENTITY_OPTIONS
    options = [
        NPCIdentityOption(npc_id=f"npc_{index:03d}", name=f"npc_{index:03d}")
        for index in range(count)
    ]
    request = NPCTurnRequest(
        scene_facts=_SCENE_FACTS,
        exact_targets=list(options),
        identity_options=list(options),
        protected_player_identity="萧云痕",
    )
    proposals: list[NPCTurnProposal] = [
        ExistingNPCProposal(
            resolution="existing",
            surface=option.name,
            context_spans=["沈孤鸿进入醉花楼。"],
            target_npc_id=option.npc_id,
        )
        for option in options
    ]
    assert len(proposals) <= MAX_PROPOSALS
    errors = validate_npc_turn_proposals(request, proposals)
    assert errors == []


def test_known_ability_roundtrip():
    ability = LLMKnownAbility(name="轻功", description="身法迅捷。")
    change = AbilityAddChange(
        field="known_abilities",
        ability=ability,
        evidence_ids=["turn_0_p001"],
    )
    dumped = json.loads(change.model_dump_json())
    assert dumped["ability"]["name"] == "轻功"
