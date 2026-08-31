from app.tests.factories.stores import open_npc_profile_store
import json
from dataclasses import asdict

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM

def _gateway(response=None):
    mock = MockLLM()
    if response is not None:
        mock.set_responses([json.dumps(response, ensure_ascii=False)])
    return mock.build_gateway()

from app.core.llm.errors import LLMOutputValidationError, LLMProviderError
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


















































def test_workflow_resolves_and_applies_to_store():
    mentions = [
        _mention(
            "沈孤鸿",
            relations=[
                _relation("沈二爷", "explicit_fixed_alias", evidence=["f1"]),
            ],
        )
    ]
    gateway = _gateway({
        "updates": [
            {
                "npc_id": "npc_001",
                "stable_changes": [
                    {"field": "role", "value": "醉花楼主", "change_basis": "醉花楼主", "evidence_ids": ["turn_3_p001"]},
                ],
            }
        ]
    })
    with open_npc_profile_store(":memory:") as store:
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
    with open_npc_profile_store(":memory:") as store:
        store.upsert(_profile())
        gateway = _gateway(response)  # type: ignore[arg-type]
        result = apply_npc_batch_update(
            npc_store=store,
            gateway=gateway,  # type: ignore[arg-type]
            scene_facts=_facts(mentions=mentions),
            evidence_documents=_evidence(),
            current_state=_state(),
            turn_index=_TURN_INDEX,
            admitted_at=_ADMITTED_AT,
        )

        assert all(
            change.field != "role" for change in result.accepted_changes
        )
        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.role is None




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
    with open_npc_profile_store(":memory:") as store:
        store.upsert(_profile())
        gateway = _gateway(response)  # type: ignore[arg-type]
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
        assert all(
            change.field != "role" for change in result.accepted_changes
        )
        profile = store.get_by_id("npc_001")
        assert profile is not None
        assert profile.name_hit_count == 1
        assert profile.role is None






















def _run_processor(mock):
    return NPCBatchUpdater(mock.build_gateway()).process(
        resolved=[
            ResolvedNpc(
                npc_id="npc_001",
                canonical_name="沈孤鸿",
                presence="appeared",
                primary_surfaces=("沈孤鸿",),
            )
        ],
        profiles=[_profile()],
        scene_facts=_facts(),
        evidence_documents=_evidence(),
        turn_index=_TURN_INDEX,
        scene_id=_SCENE_ID,
    )


def test_processor_provider_failure_propagates():
    mock = MockLLM()
    mock.set_fail_provider(True)

    with pytest.raises(LLMProviderError):
        _run_processor(mock)


def test_processor_retries_invalid_entry_and_uses_second():
    first = {
        "updates": [
            {"npc_id": "npc_001"},
            {"bogus": True},
        ]
    }
    second = {
        "updates": [{
            "npc_id": "npc_001",
            "scene_summary": _summary().model_dump(),
        }]
    }
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    batch = _run_processor(mock)

    assert [entry.npc_id for entry in batch.entries] == ["npc_001"]
    assert batch.entry_warnings == []
