import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from app.core.llm.prompts import PromptLoader
from app.domain.confirmed_history import build_turn_documents
from app.domain.current_state.schemas import (
    CurrentState,
    ParticipantState,
    PlayerState,
    SceneState,
)
from app.domain.npc_profile.batch_updater import (
    NPCBatchUpdater,
    apply_npc_batch_update,
    build_npc_batch_input,
)
from app.domain.npc_profile.change_admission import admit_npc_batch_update
from app.domain.npc_profile.changes import AcceptedNPCAliasChange, NPCBatchCoverage
from app.domain.npc_profile.identity_resolver import ResolvedNpc
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.turn_contract import (
    NPCBatchUpdate,
    NPCBatchUpdateDisplay,
    NPCBatchUpdateEntry,
    NPCSceneSummaryDraft,
    ScalarStableChange,
)
from app.domain.scene_facts.schemas import (
    AliasRelation,
    NpcPresence,
    SceneFact,
    SceneFactSet,
    SceneNpcMention,
)

_SCENE_ID = "scene_001"
_TURN_INDEX = 3
_ADMITTED_AT = "2026-08-12T00:00:00Z"
_PLAYER = "萧云痕"
_SCENE_TEXT = "沈孤鸿走进醉花楼。\n\n沈二在门口守着。"


class StubGateway:
    def __init__(self, response=None):
        self._response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append({
            "task": kwargs["task"],
            "input_data": kwargs["input_data"],
        })
        return kwargs["output_schema"].model_validate(self._response)


def _profile(npc_id="npc_001", name="沈孤鸿", *, aliases=None, **kwargs) -> NPCProfile:
    return NPCProfile(
        npc_id=npc_id,
        name=name,
        aliases=list(aliases or []),
        **kwargs,
    )


def _state(*, participants=("沈孤鸿",), location="醉花楼") -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id=_SCENE_ID,
            location=location,
            participants=[
                ParticipantState(name=name) for name in participants
            ],
        ),
        player=PlayerState(name=_PLAYER),
    )


def _evidence(scene_text=_SCENE_TEXT) -> dict:
    return {
        document.document_id: document
        for document in build_turn_documents(
            scene_id=_SCENE_ID,
            turn_index=_TURN_INDEX,
            user_input="",
            scene_text=scene_text,
        )
    }


def _facts(*, mentions=None, fact_text="沈孤鸿让沈二关门。") -> SceneFactSet:
    return SceneFactSet(
        scene_focus="沈孤鸿在醉花楼。",
        facts=[SceneFact(fact_id="f1", fact=fact_text)],
        npc_mentions=list(mentions or []),
    )


def _relation(surface, basis, evidence=None, paragraph_evidence=None) -> AliasRelation:
    return AliasRelation(
        surface=surface,
        basis=basis,
        evidence_fact_ids=list(evidence or []),
        evidence_paragraph_ids=list(paragraph_evidence or []),
    )


def _mention(primary, *, relations=None, presence: NpcPresence = "appeared") -> SceneNpcMention:
    relations = list(relations or [])
    return SceneNpcMention(
        primary_surface=primary,
        presence=presence,
        alias_relations=relations,
        evidence_fact_ids=["f1"],
    )


def _entry(npc_id, *, stable_changes=None, scene_summary=None) -> NPCBatchUpdateEntry:
    return NPCBatchUpdateEntry(
        npc_id=npc_id,
        scene_summary=scene_summary,
        stable_changes=list(stable_changes or []),
    )


def _summary(text=None, evidence=None) -> NPCSceneSummaryDraft:
    return NPCSceneSummaryDraft(
        summary=text or "沈孤鸿让沈二关上醉花楼大门，随后在大堂来回踱步。",
        evidence_ids=list(evidence or ["turn_3_p001"]),
    )


def _role_change(value="醉花楼主", evidence=None, basis="成为醉花楼主") -> ScalarStableChange:
    return ScalarStableChange(
        field="role",
        value=value,
        change_basis=basis,
        evidence_ids=list(evidence or ["turn_3_p001"]),
    )


def _admit(
    updates,
    *,
    entry_warnings=None,
    resolved=("npc_001",),
    mutable=None,
    alias_changes=None,
    profiles=None,
    evidence_documents=None,
    current_state=None,
):
    return admit_npc_batch_update(
        updates=updates,
        entry_warnings=list(entry_warnings or []),
        resolved_npc_ids=list(resolved),
        mutable_npc_ids=(
            list(resolved) if mutable is None else list(mutable)
        ),
        alias_changes=list(alias_changes or []),
        profiles=profiles or [_profile()],
        evidence_documents=evidence_documents or _evidence(),
        current_state=current_state or _state(),
        turn_index=_TURN_INDEX,
        scene_id=_SCENE_ID,
        admitted_at=_ADMITTED_AT,
    )


def test_batch_schema_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        NPCBatchUpdateEntry.model_validate(
            {"npc_id": "npc_001", "resolution": "existing"}
        )


def test_admission_applies_valid_stable_change_and_sync():
    result = _admit(
        [_entry("npc_001", scene_summary=_summary(), stable_changes=[_role_change()])],
        current_state=_state(participants=["沈孤鸿"]),
    )

    updated = {p.npc_id: p for p in result.updated_profiles}
    profile = updated["npc_001"]
    assert profile.role == "醉花楼主"
    assert profile.last_known_location == "醉花楼"
    assert result.warnings == []
    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.updated_npcs == 1
    assert result.coverage.synced_npcs == 1

def test_admission_drops_unknown_npc_id_entry_only():
    result = _admit(
        [
            _entry("npc_999", stable_changes=[_role_change()]),
            _entry("npc_001", scene_summary=_summary(), stable_changes=[_role_change()]),
        ]
    )

    assert [p.npc_id for p in result.updated_profiles] == ["npc_001"]
    codes = {warning.code for warning in result.warnings}
    assert codes == {"unknown_npc_id"}
    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.dropped_entries == 1


def test_admission_drops_unresolved_npc_id_entry_only():
    result = _admit(
        [_entry("npc_002", stable_changes=[_role_change()])],
        profiles=[_profile(), _profile("npc_002", "林川")],
        resolved=(),
        current_state=_state(participants=()),
    )

    assert [p.npc_id for p in result.updated_profiles] == []
    codes = {warning.code for warning in result.warnings}
    assert codes == {"unresolved_npc_id"}


def test_admission_drops_field_with_invalid_evidence_only():
    result = _admit(
        [
            _entry(
                "npc_001",
                stable_changes=[
                    _role_change(evidence=["turn_3_p099"]),
                    ScalarStableChange(
                        field="faction",
                        value="合欢宗",
                        change_basis="加入合欢宗",
                        evidence_ids=["turn_3_p001"],
                    ),
                ],
            )
        ]
    )

    updated = {p.npc_id: p for p in result.updated_profiles}
    assert updated["npc_001"].role is None
    assert updated["npc_001"].faction == "合欢宗"
    assert "change_evidence_dropped" in {w.code for w in result.warnings}


def test_admission_keeps_first_duplicate_stable_field():
    result = _admit(
        [
            _entry(
                "npc_001",
                stable_changes=[
                    _role_change(value="醉花楼主"),
                    _role_change(value="合欢宗宗主"),
                ],
            )
        ]
    )

    updated = {p.npc_id: p for p in result.updated_profiles}
    assert updated["npc_001"].role == "醉花楼主"
    assert "duplicate_stable_field" in {w.code for w in result.warnings}


def test_parse_batch_updates_rejects_known_abilities_but_keeps_other_fields():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, warnings = parse_batch_updates([{
        "npc_id": "npc_001",
        "stable_changes": [{
            "field": "known_abilities",
            "ability": {"name": "情报打探", "description": "擅长打听消息"},
            "evidence_ids": ["turn_3_p001"],
        }],
    }])

    assert entries[0].stable_changes == []
    assert [warning.code for warning in warnings] == ["invalid_stable_change"]


def test_admission_bad_evidence_first_does_not_poison_valid_same_field():
    result = _admit(
        [
            _entry(
                "npc_001",
                stable_changes=[
                    _role_change(value="初值", evidence=["turn_3_p099"]),
                    _role_change(value="正确值", evidence=["turn_3_p001"]),
                ],
            )
        ]
    )

    updated = {p.npc_id: p for p in result.updated_profiles}
    assert updated["npc_001"].role == "正确值"
    assert "change_evidence_dropped" in {w.code for w in result.warnings}
    assert "duplicate_stable_field" not in {w.code for w in result.warnings}


def test_parse_batch_updates_skips_invalid_entries_only():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, warnings = parse_batch_updates([
        {"npc_id": "npc_001"},
        {"bogus": True},
        {"npc_id": "npc_002", "stable_changes": [{"field": "role", "value": "x", "change_basis": "x", "evidence_ids": ["e"]}]},
    ])

    assert [entry.npc_id for entry in entries] == ["npc_001", "npc_002"]
    assert len(warnings) == 1
    assert warnings[0].code == "invalid_update_entry"


def test_parse_batch_updates_salvages_valid_fields_when_one_change_invalid():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, warnings = parse_batch_updates([
        {
            "npc_id": "npc_001",
            "stable_changes": [
                {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
                {"field": "bogus_field", "value": "x"},
            ],
        },
    ])

    assert len(entries) == 1
    entry = entries[0]
    assert entry.npc_id == "npc_001"
    assert len(entry.stable_changes) == 1
    assert entry.stable_changes[0].field == "role"
    assert {warning.code for warning in warnings} == {"invalid_stable_change"}


def test_parse_batch_updates_rejects_trait_update_but_keeps_role_change():
    """真实回归：逐回合 batch 无权更新 personality_trait。"""
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, warnings = parse_batch_updates([{
        "npc_id": "npc_001",
        "stable_changes": [
            {
                "field": "personality_trait",
                "value": "沉稳重情",
                "evidence_ids": ["turn_3_p001"],
            },
            {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
        ],
    }])

    assert len(entries) == 1
    assert len(entries[0].stable_changes) == 1
    assert entries[0].stable_changes[0].field == "role"
    assert [warning.code for warning in warnings] == ["invalid_stable_change"]


def test_parse_batch_updates_tolerates_null_entries():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, warnings = parse_batch_updates([None, {"npc_id": "npc_001"}, "not a dict"])

    assert [entry.npc_id for entry in entries] == ["npc_001"]
    assert len(warnings) == 2
    assert all(warning.code == "invalid_update_entry" for warning in warnings)


def test_batch_display_schema_requires_scene_summary_and_shapes_stable_changes():
    import json

    from app.core.llm.schema import schema_json

    schema = json.loads(schema_json(NPCBatchUpdateDisplay))
    entry_def = schema["$defs"]["NPCBatchUpdateDisplayEntry"]
    assert "scene_summary" in entry_def["required"]
    items = entry_def["properties"]["stable_changes"]["items"]
    assert "oneOf" in items
    mapping = items["discriminator"]["mapping"]
    assert "personality_trait" not in mapping
    assert "known_abilities" not in mapping


def test_store_rejects_mutated_invalid_profile_before_write():
    profile = _profile(personality_trait="稳重")
    profile.personality_trait = "过" * 41

    with NPCProfileStore(":memory:") as store:
        with pytest.raises(ValidationError, match="personality_trait"):
            store.upsert(profile)
        assert store.get_by_id(profile.npc_id) is None


def test_batch_input_uses_mention_evidence_for_pronoun_facts():
    from app.domain.npc_profile.identity_resolver import ResolvedNpc

    resolved = [
        ResolvedNpc(
            npc_id="npc_001",
            canonical_name="沈孤鸿",
            presence="appeared",
            primary_surfaces=("沈孤鸿",),
            evidence_fact_ids=("f1",),
            evidence_paragraph_ids=("turn_3_p001",),
        )
    ]
    facts = SceneFactSet(
        scene_focus="x",
        facts=[SceneFact(fact_id="f1", fact="他正式成为醉花楼主。")],
    )
    evidence = _evidence("众人散去后，他接过印信，正式成为醉花楼主。")

    input_data = build_npc_batch_input(
        resolved=resolved,
        profiles=[_profile()],
        scene_facts=facts,
        evidence_documents=evidence,
    )
    entries = json.loads(input_data["npc_batch_json"])
    assert entries[0]["facts"] == [{"fact_id": "f1", "fact": "他正式成为醉花楼主。"}]
    assert entries[0]["evidence"][0]["document_id"] == "turn_3_p001"


def test_alias_change_carries_evidence():
    mentions = [
        _mention(
            "沈孤鸿",
            relations=[
                _relation("沈二爷", "explicit_fixed_alias", evidence=["f1"]),
            ],
        )
    ]
    response = {"updates": [{"npc_id": "npc_001"}]}
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert len(result.accepted_alias_changes) == 1
        assert result.accepted_alias_changes[0].evidence_fact_ids == ("f1",)


def test_alias_change_carries_paragraph_only_evidence():
    mentions = [
        _mention(
            "沈孤鸿",
            relations=[
                _relation(
                    "沈二爷",
                    "explicit_fixed_alias",
                    paragraph_evidence=["turn_3_p001"],
                ),
            ],
        )
    ]
    response = {"updates": [{"npc_id": "npc_001"}]}
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert len(result.accepted_alias_changes) == 1
        change = result.accepted_alias_changes[0]
        assert change.evidence_fact_ids == ()
        assert change.evidence_paragraph_ids == ("turn_3_p001",)


def test_parse_batch_updates_caps_stable_changes_at_eight():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    changes = [
        {"field": "role", "value": f"值{i}", "change_basis": f"成为值{i}", "evidence_ids": ["turn_3_p001"]}
        for i in range(10)
    ]
    entries, warnings = parse_batch_updates([
        {"npc_id": "npc_001", "stable_changes": changes},
    ])

    assert len(entries) == 1
    assert len(entries[0].stable_changes) == 8
    assert sum(1 for w in warnings if w.code == "stable_change_limit_exceeded") == 2


def test_admission_dropped_entries_counts_only_entry_drops():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, entry_warnings = parse_batch_updates([
        {
            "npc_id": "npc_001",
            "stable_changes": [
                {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]}
            ],
        },
        None,
    ])
    result = _admit(entries, entry_warnings=entry_warnings, current_state=_state(participants=()))

    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.dropped_entries == 1
    assert result.coverage.updated_npcs == 1


def test_admission_alias_only_update_counts_as_updated():
    alias = AcceptedNPCAliasChange(
        npc_id="npc_001",
        canonical_name="沈孤鸿",
        alias="沈二爷",
        turn_index=_TURN_INDEX,
    )
    result = _admit([], alias_changes=[alias], current_state=_state(participants=()))

    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.updated_npcs == 1
    assert result.coverage.no_op_npcs == 0


def test_admission_rejects_alias_outside_resolved_set():
    with pytest.raises(ValueError, match="outside the resolved set"):
        _admit(
            [],
            alias_changes=[
                AcceptedNPCAliasChange(
                    npc_id="npc_999",
                    canonical_name="x",
                    alias="y",
                    turn_index=_TURN_INDEX,
                )
            ],
            current_state=_state(participants=()),
        )

    with pytest.raises(ValueError, match="outside the resolved set"):
        _admit(
            [],
            profiles=[_profile(), _profile("npc_002", "林川")],
            resolved=("npc_001",),
            alias_changes=[
                AcceptedNPCAliasChange(
                    npc_id="npc_002",
                    canonical_name="林川",
                    alias="y",
                    turn_index=_TURN_INDEX,
                )
            ],
            current_state=_state(participants=()),
        )


def test_admission_rejects_alias_with_wrong_canonical_name():
    with pytest.raises(ValueError, match="does not match profile name"):
        _admit(
            [],
            alias_changes=[
                AcceptedNPCAliasChange(
                    npc_id="npc_001",
                    canonical_name="林川",
                    alias="沈二爷",
                    turn_index=_TURN_INDEX,
                )
            ],
            current_state=_state(participants=()),
        )


def test_admission_rejects_blank_alias():
    with pytest.raises(ValueError, match="blank alias"):
        _admit(
            [],
            alias_changes=[
                AcceptedNPCAliasChange(
                    npc_id="npc_001",
                    canonical_name="沈孤鸿",
                    alias="   ",
                    turn_index=_TURN_INDEX,
                )
            ],
            current_state=_state(participants=()),
        )


def test_admission_filters_existing_alias_as_noop():
    alias = AcceptedNPCAliasChange(
        npc_id="npc_001",
        canonical_name="沈孤鸿",
        alias="沈二",
        turn_index=_TURN_INDEX,
    )
    result = _admit(
        [],
        alias_changes=[alias],
        profiles=[_profile(aliases=["沈二"])],
        current_state=_state(participants=()),
    )

    assert result.accepted_alias_changes == []
    assert result.updated_profiles == []
    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.updated_npcs == 0
    assert result.coverage.no_op_npcs == 1


def test_workflow_loose_envelope_applies_valid_and_drops_invalid():
    mentions = [_mention("沈孤鸿")]
    response = {
        "updates": [
            {
                "npc_id": "npc_001",
                "stable_changes": [
                    {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]}
                ],
            },
            {"bogus": True},
        ]
    }
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.role == "醉花楼主"
        assert {"invalid_update_entry"} <= {w.code for w in result.warnings}


def test_workflow_rejects_trait_update_and_keeps_role_change():
    mentions = [_mention("沈孤鸿")]
    original_trait = "遇事沉稳"
    response = {
        "updates": [{
            "npc_id": "npc_001",
            "stable_changes": [
                {
                    "field": "personality_trait",
                    "value": "沉稳重情",
                    "evidence_ids": ["turn_3_p001"],
                },
                {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
            ],
        }],
    }
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile(personality_trait=original_trait))

        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        persisted = store.get_by_id("npc_001")
        assert persisted is not None
        assert persisted.personality_trait == original_trait
        assert persisted.role == "醉花楼主"
        assert "invalid_stable_change" in {w.code for w in result.warnings}


def test_admission_no_op_for_resolved_npc_without_output():
    result = _admit([], current_state=_state(participants=()))

    assert result.updated_profiles == []
    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.resolved_npcs == 1
    assert result.coverage.no_op_npcs == 1
    assert result.coverage.synced_npcs == 0


def test_admission_sync_runs_independently_of_llm_output():
    result = _admit([], current_state=_state(participants=["沈孤鸿"]))

    updated = {p.npc_id: p for p in result.updated_profiles}
    assert updated["npc_001"].last_known_location == "醉花楼"
    assert isinstance(result.coverage, NPCBatchCoverage)
    assert result.coverage.synced_npcs == 1


def test_admission_applies_deterministic_alias_changes():
    alias = AcceptedNPCAliasChange(
        npc_id="npc_001",
        canonical_name="沈孤鸿",
        alias="沈二爷",
        turn_index=_TURN_INDEX,
    )
    result = _admit([], alias_changes=[alias])

    updated = {p.npc_id: p for p in result.updated_profiles}
    assert "沈二爷" in updated["npc_001"].aliases
    assert result.accepted_alias_changes == [alias]


def test_processor_makes_single_batch_call_and_projects_input():
    resolved = [ResolvedNpc(npc_id="npc_001", canonical_name="沈孤鸿", presence="appeared", primary_surfaces=("沈孤鸿",))]
    gateway = StubGateway({"updates": [{"npc_id": "npc_001"}]})
    batch = NPCBatchUpdater(gateway).process(  # type: ignore[arg-type]
        resolved=resolved,
        profiles=[_profile()],
        scene_facts=_facts(),
        evidence_documents=_evidence(),
        turn_index=_TURN_INDEX,
        scene_id=_SCENE_ID,
    )

    assert [call["task"] for call in gateway.calls] == ["npc_batch_update"]
    entries = json.loads(gateway.calls[0]["input_data"]["npc_batch_json"])
    assert entries[0]["npc_id"] == "npc_001"
    assert "personality_trait" not in entries[0]["current"]
    assert "known_abilities" not in entries[0]["current"]
    assert entries[0]["facts"] == [{"fact_id": "f1", "fact": "沈孤鸿让沈二关门。"}]
    assert entries[0]["evidence"][0]["document_id"] == "turn_3_p001"
    assert batch.entries[0].npc_id == "npc_001"
    assert batch.entry_warnings == []


def test_processor_zero_calls_without_resolved_npcs():
    gateway = StubGateway()
    batch = NPCBatchUpdater(gateway).process(  # type: ignore[arg-type]
        resolved=[],
        profiles=[_profile()],
        scene_facts=_facts(),
        evidence_documents=_evidence(),
        turn_index=_TURN_INDEX,
        scene_id=_SCENE_ID,
    )

    assert gateway.calls == []
    assert batch.entries == []
    assert batch.entry_warnings == []


def test_batch_prompt_loads_and_renders():
    template, version = PromptLoader().load("npc_batch_update")
    assert version == "v1"
    rendered = template.format(
        npc_batch_json="[]",
        output_schema_json="{}",
    )
    assert "NPC Batch" in rendered
    assert "npc_id" in rendered
    assert "# 字段语义" in rendered
    assert "可同时更新 `role` 和 `faction`" in rendered
    assert "规范化、简洁归纳" in rendered


def test_workflow_resolves_and_applies_to_store():
    mentions = [
        _mention(
            "沈孤鸿",
            relations=[
                _relation("沈二爷", "explicit_fixed_alias", evidence=["f1"]),
            ],
        )
    ]
    gateway = StubGateway({
        "updates": [
            {
                "npc_id": "npc_001",
                "stable_changes": [
                    {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
                ],
            }
        ]
    })
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile(aliases=["沈二"]))
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=gateway,  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert [call["task"] for call in gateway.calls] == ["npc_batch_update"]
        assert result.saved_ids == ["npc_001"]
        assert result.accepted_alias_changes[0].alias == "沈二爷"
        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.role == "醉花楼主"
        assert profile.last_known_location == "醉花楼"
        assert "沈二爷" in profile.aliases
        assert profile.name_hit_count == 1
        assert profile.alias_hit_counts.get("沈二爷") == 1
        assert result.new_npc_candidates == []


def test_workflow_is_deterministic():
    mentions = [_mention("沈孤鸿")]
    response = {"updates": [{"npc_id": "npc_001"}]}

    def run_once():
        with NPCProfileStore(":memory:") as store:
            store.upsert(_profile())
            return apply_npc_batch_update(
                npc_store=store,
                gateway=StubGateway(json.loads(json.dumps(response))),  # type: ignore[arg-type]
                scene_facts=_facts(mentions=mentions),
                evidence_documents=_evidence(),
                current_state=_state(),
                turn_index=_TURN_INDEX,
                admitted_at=_ADMITTED_AT,
            )

    first = run_once()
    second = run_once()
    assert asdict(first) == asdict(second)


def test_mentioned_only_npc_excluded_from_batch_and_field_output():
    mentions = [_mention("沈孤鸿", presence="mentioned_only")]
    response = {
        "updates": [
            {
                "npc_id": "npc_001",
                "stable_changes": [
                    {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
                ],
            }
        ]
    }
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        gateway = StubGateway(response)  # type: ignore[arg-type]
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=gateway,  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert gateway.calls == []
        assert all(
            change.field != "role" for change in result.accepted_changes
        )
        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.role is None


def test_mentioned_only_alias_still_admitted():
    mentions = [
        _mention(
            "沈孤鸿",
            presence="mentioned_only",
            relations=[
                _relation("沈二爷", "explicit_fixed_alias", evidence=["f1"]),
            ],
        )
    ]
    response = {"updates": []}
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert len(result.accepted_alias_changes) == 1
        assert result.accepted_alias_changes[0].alias == "沈二爷"


def test_scan_union_records_mention_hit_but_never_enters_batch():
    response = {
        "updates": [
            {
                "npc_id": "npc_001",
                "stable_changes": [
                    {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
                ],
            }
        ]
    }
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        gateway = StubGateway(response)  # type: ignore[arg-type]
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=gateway,  # type: ignore[arg-type]
            scene_facts=_facts(mentions=[]),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert result.resolution.diagnostics is not None
        assert result.resolution.diagnostics.scan_union_added == 1
        assert result.resolution.resolved[0].presence == "mentioned_only"
        assert gateway.calls == []
        assert all(
            change.field != "role" for change in result.accepted_changes
        )
        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.name_hit_count == 1
        assert profile.role is None


def test_admission_rejects_mentioned_only_field_output():
    result = _admit(
        [_entry("npc_001", stable_changes=[_role_change()])],
        resolved=("npc_001",),
        mutable=(),
    )
    assert all(
        change.field != "role" for change in result.accepted_profile_changes
    )
    assert any(
        warning.code == "mentioned_only_field_output"
        for warning in result.warnings
    )


def test_admission_mutable_must_be_subset_of_resolved():
    with pytest.raises(ValueError, match="subset of resolved"):
        _admit(
            [],
            resolved=("npc_001",),
            mutable=("npc_999",),
        )


def test_admission_requires_mutable_npc_ids():
    with pytest.raises(TypeError):
        admit_npc_batch_update(  # type: ignore[call-arg]
            updates=[],
            resolved_npc_ids=["npc_001"],
            alias_changes=[],
            profiles=[_profile()],
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            scene_id=_SCENE_ID,
            admitted_at=_ADMITTED_AT,
        )


def test_admission_helper_defaults_mutable_to_resolved():
    result = _admit(
        [_entry("npc_001", stable_changes=[_role_change()])],
        resolved=("npc_001",),
    )
    assert any(
        change.field == "role" for change in result.accepted_profile_changes
    )
    assert any(
        profile.role == "醉花楼主" for profile in result.updated_profiles
    )


def test_scene_summary_atomic_write_applied_profile_turn_matches():
    result = _admit(
        [_entry("npc_001", scene_summary=_summary())],
    )
    summary_change = next(
        change
        for change in result.accepted_profile_changes
        if change.field == "last_scene_summary"
    )
    assert summary_change.turn_index == _TURN_INDEX
    assert summary_change.evidence_ids == ("turn_3_p001",)
    (profile,) = result.updated_profiles
    assert profile.last_scene_summary == _summary().summary
    assert profile.last_scene_turn == _TURN_INDEX


def test_scene_summary_mentioned_only_not_written():
    result = _admit(
        [_entry("npc_001", scene_summary=_summary())],
        resolved=("npc_001",),
        mutable=(),  # presence != appeared
        current_state=_state(participants=()),
    )
    assert result.accepted_profile_changes == []
    assert result.updated_profiles == []
    assert {warning.code for warning in result.warnings} == {
        "mentioned_only_field_output"
    }


def test_scene_summary_bad_evidence_salvage_keeps_stable_changes():
    result = _admit(
        [
            _entry(
                "npc_001",
                scene_summary=_summary(evidence=["turn_3_p099"]),
                stable_changes=[_role_change()],
            )
        ]
    )
    assert "scene_summary_evidence_dropped" in {
        warning.code for warning in result.warnings
    }
    assert any(
        change.field == "role" for change in result.accepted_profile_changes
    )
    (profile,) = result.updated_profiles
    assert profile.last_scene_summary is None
    assert profile.last_scene_turn is None
    assert profile.role == "醉花楼主"


def test_scene_summary_single_npc_failure_is_isolated():
    from app.domain.npc_profile.batch_updater import parse_batch_updates

    entries, entry_warnings = parse_batch_updates([
        {
            "npc_id": "npc_001",
            "scene_summary": {"summary": "太短", "evidence_ids": ["turn_3_p001"]},
        },
        {
            "npc_id": "npc_002",
            "scene_summary": {
                "summary": "林川接过库房钥匙，当场答应留守到天亮，随后在门边坐下。",
                "evidence_ids": ["turn_3_p001"],
            },
        },
    ])
    first = _profile("npc_001", "沈孤鸿")
    second = _profile("npc_002", "林川")
    result = _admit(
        entries,
        entry_warnings=entry_warnings,
        resolved=("npc_001", "npc_002"),
        profiles=[first, second],
    )
    assert {warning.code for warning in result.warnings} == {
        "invalid_scene_summary"
    }
    by_id = {profile.npc_id: profile for profile in result.updated_profiles}
    assert by_id["npc_001"].last_scene_summary is None
    assert by_id["npc_001"].last_scene_turn is None
    assert by_id["npc_002"].last_scene_summary == (
        "林川接过库房钥匙，当场答应留守到天亮，随后在门边坐下。"
    )
    assert by_id["npc_002"].last_scene_turn == _TURN_INDEX


def test_scene_summary_appeared_missing_emits_warning():
    result = _admit(
        [],
        resolved=("npc_001",),
        current_state=_state(participants=()),
    )
    assert {warning.code for warning in result.warnings} == {
        "missing_scene_summary"
    }
    assert result.accepted_profile_changes == []
    assert result.updated_profiles == []


def test_scene_summary_same_text_new_turn_still_applies():
    first = _profile()
    first.last_scene_summary = "沈孤鸿让沈二关上醉花楼大门，随后在大堂来回踱步。"
    first.last_scene_turn = _TURN_INDEX - 1
    result = _admit(
        [_entry("npc_001", scene_summary=_summary())],
        profiles=[first],
    )
    (profile,) = result.updated_profiles
    assert profile.last_scene_summary == _summary().summary
    assert profile.last_scene_turn == _TURN_INDEX


def test_scene_summary_full_pipeline_records_field_log_and_consistent_snapshot():
    mentions = [_mention("沈孤鸿")]
    response = {
        "updates": [{
            "npc_id": "npc_001",
            "scene_summary": {
                "summary": "沈孤鸿让沈二关上醉花楼大门，随后在大堂来回踱步。",
                "evidence_ids": ["turn_3_p001"],
            },
            "stable_changes": [],
        }],
    }
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=StubGateway(response),  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        saved = store.get_by_id("npc_001")
        assert saved is not None
        assert saved.last_scene_summary == (
            "沈孤鸿让沈二关上醉花楼大门，随后在大堂来回踱步。"
        )
        assert saved.last_scene_turn == _TURN_INDEX

        summary_change = next(
            change
            for change in result.accepted_changes
            if change.field == "last_scene_summary"
        )
        documents = list(_evidence().values())
        store.record_accepted_field_changes(
            changes=[summary_change],
            evidence_documents=documents,
            turn_index=_TURN_INDEX,
            scene_id=_SCENE_ID,
        )
        logs = store.list_field_change_logs()
        assert len(logs) == 1
        assert logs[0].field_name == "last_scene_summary"
        assert logs[0].turn_index == _TURN_INDEX
        assert logs[0].evidence[0].evidence_id == "turn_3_p001"
