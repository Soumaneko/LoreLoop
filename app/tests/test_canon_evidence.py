from app.domain.canon_evidence import CanonEvidenceRetriever, CanonEvidenceStore
from app.domain.narrative_canon_query.schemas import (
    CanonEntityReference,
    NarrativeCanonQuery,
    NarrativeCanonQuerySet,
)
from app.domain.short_term_memory.schemas import ShortTermScene


def _catalog():
    return [
        CanonEntityReference(name="莱尔", kind="player"),
        CanonEntityReference(
            name="埃利奥·维瓦切",
            aliases=["埃利奥", "木偶师"],
            kind="npc",
        ),
        CanonEntityReference(name="希尔达·布拉斯科特", aliases=["希尔达"], kind="npc"),
        CanonEntityReference(
            name="灰烬图书馆废墟",
            aliases=["灰烬图书馆"],
            kind="location",
        ),
    ]


def _query_set():
    return NarrativeCanonQuerySet(
        thought_process="P022-P023 建立了埃利奥过去探索灰烬图书馆的经历。",
        queries=[NarrativeCanonQuery(
            query_id="q12",
            paragraph_ids=["P022", "P023"],
            claim="埃利奥此前独自探索过灰烬图书馆废墟。",
            claim_type="past_event",
            entities=["埃利奥·维瓦切", "灰烬图书馆废墟"],
            lookup_queries=[
                "埃利奥此前是否到访灰烬图书馆",
                "埃利奥如何得知灰烬图书馆",
            ],
            priority="high",
        )],
    )


def test_rebuild_indexes_only_raw_confirmed_inputs_and_paragraphs():
    scenes = [ShortTermScene(
        scene_id="scene_003",
        turn_index=3,
        user_input="询问希尔达是否了解灰烬图书馆。",
        scene_text=(
            "希尔达第一次向埃利奥说明灰烬图书馆的位置。\n\n"
            "埃利奥把这个名字低声念了一遍。"
        ),
    )]
    store = CanonEvidenceStore()
    try:
        store.rebuild(scenes, _catalog())
        documents = store.list_documents()
    finally:
        store.close()

    assert [document.document_id for document in documents] == [
        "turn_3_input",
        "turn_3_p001",
        "turn_3_p002",
    ]
    assert {document.source_type for document in documents} == {
        "player_input",
        "scene_paragraph",
    }
    serialized = "\n".join(document.text for document in documents)
    assert "Scene Fact" not in serialized
    assert "Summary" not in serialized


def test_retriever_finds_prior_entity_cooccurrence_and_returns_raw_context():
    scenes = [
        ShortTermScene(
            scene_id="scene_003",
            turn_index=3,
            user_input="询问希尔达是否了解这种禁术。",
            scene_text=(
                "希尔达翻开了自己的修复笔记。\n\n"
                "她告诉埃利奥，相关禁书封存在灰烬图书馆地下。\n\n"
                "埃利奥把灰烬图书馆这个名字低声念了一遍。"
            ),
        ),
        ShortTermScene(
            scene_id="scene_004",
            turn_index=4,
            user_input="讨论前往废墟的报酬。",
            scene_text="埃利奥同意支付预付款，并借出马车。",
        ),
    ]
    store = CanonEvidenceStore()
    try:
        store.rebuild(scenes, _catalog())
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert evidence
    assert evidence[0].turn_index == 3
    assert evidence[0].source_type == "scene_paragraph"
    evidence_window = "\n".join(filter(None, [
        evidence[0].context_before,
        evidence[0].text,
        evidence[0].context_after,
    ]))
    assert "告诉埃利奥" in evidence_window
    assert "埃利奥把灰烬图书馆这个名字低声念了一遍" in evidence_window
    assert "埃利奥·维瓦切" in evidence[0].matched_entities
    assert "灰烬图书馆废墟" in evidence[0].matched_entities
    assert "entity_all_in_document" in evidence[0].retrieval_channels


def test_retriever_keeps_player_input_as_lower_weight_intent_evidence():
    scenes = [ShortTermScene(
        scene_id="scene_005",
        turn_index=5,
        user_input="告诉埃利奥灰烬图书馆的位置。",
        scene_text="莱尔没有找到合适的开口时机。",
    )]
    store = CanonEvidenceStore()
    try:
        store.rebuild(scenes, _catalog())
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert evidence
    assert evidence[0].source_type == "player_input"
    assert evidence[0].text == "告诉埃利奥灰烬图书馆的位置。"


def test_store_uses_fts5_trigram_for_chinese_substrings():
    store = CanonEvidenceStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_006",
                turn_index=6,
                scene_text="禁书封存在灰烬图书馆地下的铅衬石匣里。",
            )
        ], _catalog())
        hits = store.fts_search("灰烬图书馆")
    finally:
        store.close()

    assert "turn_6_p001" in hits


def test_single_entity_does_not_admit_unrelated_mentions():
    store = CanonEvidenceStore()
    query_set = NarrativeCanonQuerySet(
        thought_process="P008 建立了埃利奥过去的旅行经历。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P008"],
            claim="埃利奥五年前从王都去了霜痕城。",
            claim_type="past_event",
            entities=["埃利奥·维瓦切"],
            lookup_queries=["埃利奥是否去过霜痕城"],
            priority="medium",
        )],
    )
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_007",
                turn_index=7,
                scene_text="埃利奥问门外的脚印是靴子印还是赤脚。",
            )
        ], _catalog())
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    assert result.results[0].evidence == []


def test_semantic_threshold_can_admit_without_known_entities():
    store = CanonEvidenceStore()
    query_set = NarrativeCanonQuerySet(
        thought_process="P008 建立了霜痕城旧神殿的既有状态。",
        queries=[NarrativeCanonQuery(
            query_id="q2",
            paragraph_ids=["P008"],
            claim="霜痕城的旧神殿地下室常年结冰。",
            claim_type="world_rule_or_history",
            entities=[],
            lookup_queries=["霜痕城旧神殿地下室结冰"],
            priority="low",
        )],
    )
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_008",
                turn_index=8,
                scene_text="当地人说霜痕城旧神殿的地下室终年结冰。",
            )
        ], _catalog())
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert len(evidence) == 1
    assert "semantic_trigram_threshold" in evidence[0].retrieval_channels


def test_multiple_entities_must_share_one_evidence_window():
    store = CanonEvidenceStore()
    distant_paragraphs = ["没有相关内容。"] * 5
    scene_text = "\n\n".join([
        "埃利奥正在整理木偶。",
        *distant_paragraphs,
        "灰烬图书馆位于旧商道尽头。",
    ])
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_009",
                turn_index=9,
                scene_text=scene_text,
            )
        ], _catalog())
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    assert result.results[0].evidence == []
