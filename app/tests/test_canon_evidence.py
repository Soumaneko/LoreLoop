import json

from app.domain.canon_evidence import (
    CanonEvidenceRetriever,
    project_canon_evidence_for_check,
)
from app.domain.canon_evidence.retriever import _best_trigram_match, _trigrams
from app.domain.confirmed_history import ConfirmedHistoryStore
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
    store = ConfirmedHistoryStore()
    try:
        store.rebuild(scenes)
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
    store = ConfirmedHistoryStore()
    try:
        store.rebuild(scenes)
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
    assert "entity_relation_window" in evidence[0].retrieval_channels


def test_each_lookup_query_is_scored_independently_and_best_match_wins():
    unrelated = _trigrams("王都商人与旧马车的长篇无关交易记录")
    direct_match = _trigrams("第一次得知灰烬图书馆")

    score, matched = _best_trigram_match(
        [unrelated, direct_match],
        "希尔达让埃利奥第一次得知灰烬图书馆。",
    )

    assert score == 1.0
    assert matched == direct_match


def test_retriever_admits_entities_and_relation_cues_across_three_paragraphs():
    store = ConfirmedHistoryStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_003",
                turn_index=3,
                scene_text=(
                    "埃利奥皱眉询问那座废墟究竟叫什么。\n\n"
                    "希尔达第一次说出灰烬图书馆的名字。\n\n"
                    "他把这个陌生地名低声重复了一遍。"
                ),
            )
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert evidence
    evidence_window = "\n".join(filter(None, [
        evidence[0].context_before,
        evidence[0].text,
        evidence[0].context_after,
    ]))
    assert "埃利奥皱眉询问" in evidence_window
    assert "希尔达第一次说出灰烬图书馆" in evidence_window
    assert "陌生地名" in evidence_window
    assert set(evidence[0].matched_entities) == {
        "埃利奥·维瓦切",
        "灰烬图书馆废墟",
    }
    assert "entity_relation_window" in evidence[0].retrieval_channels


def test_strong_candidate_suppresses_entity_only_fallback():
    store = ConfirmedHistoryStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="埃利奥与灰烬图书馆同时印在一张褪色海报的标题上。",
            ),
            ShortTermScene(
                scene_id="scene_002",
                turn_index=2,
                scene_text="希尔达第一次告诉埃利奥灰烬图书馆的名字。",
            ),
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert evidence
    assert {item.turn_index for item in evidence} == {2}
    assert all(
        "entity_window_fallback" not in item.retrieval_channels
        for item in evidence
    )


def test_entity_only_fallback_returns_at_most_one_when_no_strong_candidate():
    store = ConfirmedHistoryStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="埃利奥与灰烬图书馆同时印在一张褪色海报的标题上。",
            ),
            ShortTermScene(
                scene_id="scene_002",
                turn_index=2,
                scene_text="埃利奥和灰烬图书馆被画在两枚并排的徽章上。",
            ),
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(
            _query_set(),
            evidence_per_query=5,
        )
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert len(evidence) == 1
    assert "entity_window_fallback" in evidence[0].retrieval_channels


def test_retriever_keeps_player_input_as_lower_weight_intent_evidence():
    scenes = [ShortTermScene(
        scene_id="scene_005",
        turn_index=5,
        user_input="告诉埃利奥灰烬图书馆的位置。",
        scene_text="莱尔没有找到合适的开口时机。",
    )]
    store = ConfirmedHistoryStore()
    try:
        store.rebuild(scenes)
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert evidence
    assert evidence[0].source_type == "player_input"
    assert evidence[0].text == "告诉埃利奥灰烬图书馆的位置。"


def test_store_uses_fts5_trigram_for_chinese_substrings():
    store = ConfirmedHistoryStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_006",
                turn_index=6,
                scene_text="禁书封存在灰烬图书馆地下的铅衬石匣里。",
            )
        ])
        hits = store.fts_search("灰烬图书馆")
    finally:
        store.close()

    assert "turn_6_p001" in hits


def test_single_entity_does_not_admit_unrelated_mentions():
    store = ConfirmedHistoryStore()
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
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    assert result.results[0].evidence == []


def test_semantic_threshold_can_admit_without_known_entities():
    store = ConfirmedHistoryStore()
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
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    evidence = result.results[0].evidence
    assert len(evidence) == 1
    assert "semantic_trigram_threshold" in evidence[0].retrieval_channels


def test_multiple_entities_must_share_one_evidence_window():
    store = ConfirmedHistoryStore()
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
        ])
        result = CanonEvidenceRetriever(store, _catalog()).retrieve(_query_set())
    finally:
        store.close()

    assert result.results[0].evidence == []


def test_check_projection_omits_queries_without_evidence():
    query_set = NarrativeCanonQuerySet(
        thought_process="调试信息。",
        queries=[
            NarrativeCanonQuery(
                query_id="q1",
                paragraph_ids=["P001"],
                claim="没有召回结果。",
                claim_type="other",
                entities=[],
                lookup_queries=["不会进入检查器"],
                priority="low",
            ),
        ],
    )
    store = ConfirmedHistoryStore()
    try:
        evidence_set = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    assert project_canon_evidence_for_check(query_set, evidence_set) == "[]"


def test_check_projection_orders_each_record_as_before_text_after():
    query_set = _query_set()
    store = ConfirmedHistoryStore()
    try:
        store.rebuild([
            ShortTermScene(
                scene_id="scene_003",
                turn_index=3,
                scene_text=(
                    "埃利奥询问那座废墟叫什么。\n\n"
                    "希尔达第一次说出灰烬图书馆。\n\n"
                    "埃利奥重复这个陌生名字。"
                ),
            )
        ])
        evidence_set = CanonEvidenceRetriever(store, _catalog()).retrieve(query_set)
    finally:
        store.close()

    projected = project_canon_evidence_for_check(query_set, evidence_set)
    assert json.loads(projected)[0]["audit_target"] == query_set.queries[0].claim
    assert projected.index('"context_before"') < projected.index('"text"')
    assert projected.index('"text"') < projected.index('"context_after"')
