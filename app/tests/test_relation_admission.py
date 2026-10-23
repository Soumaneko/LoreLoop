import pytest

from app.domain.confirmed_history import history_document_text_hash
from app.domain.continuity.schemas import ContinuityHistoryLink
from app.domain.context_facts.relation_admission import (
    continuity_link_to_evidence,
    judge_relation_links,
    npc_field_link_to_evidence,
)
from app.domain.context_facts.retriever import ContextHistoryRetriever
from app.domain.context_facts.schemas import ContextSearchEntity, ContextSearchIntent, ContextSearchPlan
from app.domain.npc_profile.history_bridge import NPCFieldHistoryLink
from app.tests.factories.stores import open_confirmed_history_store


def _scene_context(text):
    with open_confirmed_history_store() as store:
        store.upsert_turn(scene_id="scene_001", turn_index=1, user_input="", scene_text=text)
        documents = store.list_documents()
    by_id = {document.document_id: document for document in documents}
    paragraph = next(d for d in documents if d.source_type == "scene_paragraph")
    return by_id, {1: documents}, paragraph


def _continuity_link(paragraph, basis, *, intent="i1", item="item"):
    return ContinuityHistoryLink(
        continuity_id=item, continuity_status="active", operation="create",
        evidence_id=paragraph.document_id, evidence_hash=history_document_text_hash(paragraph),
        triggering_intent_ids=[intent], match_reasons=["exact_entity_match"],
        evidence_position=0, evidence_basis_primary=basis, basis_complete=True,
    )


def test_shared_evidence_preserves_each_sources_provenance():
    text = "她接过铜印，正式成为北门巡检。"
    documents, by_turn, paragraph = _scene_context(text)
    continuity = continuity_link_to_evidence(_continuity_link(paragraph, text, intent="a"))
    npc = npc_field_link_to_evidence(NPCFieldHistoryLink(
        npc_id="npc-a", field_name="role", log_id=7, turn_index=1,
        evidence_id=paragraph.document_id, evidence_hash=history_document_text_hash(paragraph),
        evidence_position=0, triggering_intent_ids=["b"], match_reasons=["npc_name_match"],
        evidence_basis_primary="北门巡检", basis_complete=True,
    ))
    admissions, _ = judge_relation_links([continuity, npc], documents_by_id=documents, by_turn=by_turn)
    admission = admissions[paragraph.document_id]
    assert admission.matched_continuity_ids == {"item"}
    assert admission.linked_operations == {"create"}
    assert admission.linked_npc_changes == {("npc-a", "role", 7)}
    assert admission.intent_owner_keys["a"] == {"continuity:item"}
    assert admission.intent_owner_keys["b"] == {"npc_profile:npc-a"}


@pytest.mark.parametrize("basis,admitted", [
    ("确认三日后在青岩镇以铜牌暗记接头。", True),
    ("对岸灯火在薄雾里明灭。", False),
], ids=["supported", "unrelated"])
def test_relation_requires_support_from_confirmed_prose(basis, admitted):
    documents, by_turn, paragraph = _scene_context("阿九确认三日后在青岩镇以铜牌暗记接头。")
    link = continuity_link_to_evidence(_continuity_link(paragraph, basis))
    admissions, _ = judge_relation_links([link], documents_by_id=documents, by_turn=by_turn)
    assert (paragraph.document_id in admissions) == admitted


def test_clustered_direct_hit_keeps_relation_evidence_from_another_paragraph():
    base = "艾琳与那女人约定在青岩镇以铜牌接头"
    with open_confirmed_history_store() as store:
        store.upsert_turn(scene_id="scene_001", turn_index=1, user_input="", scene_text=f"{base}。\n\n{base}！")
        paragraphs = sorted(
            (d for d in store.list_documents() if d.source_type == "scene_paragraph"),
            key=lambda d: d.paragraph_index,
        )
        plan = ContextSearchPlan(intents=[ContextSearchIntent(
            intent_id="A", focus="entity", origin="explicit_input", entities=[ContextSearchEntity(name="艾琳")],
        )])
        result = ContextHistoryRetriever(store).retrieve(
            plan, current_turn_index=2,
            continuity_links=[_continuity_link(paragraphs[1], "约定在青岩镇以铜牌接头。", intent="A", item="agreement")],
        )
    assert any(hit.matched_intent_ids and "agreement" in hit.linked_continuity_ids for hit in result.hits)
    assert any(hit.supporting_evidence_ids for hit in result.hits)
