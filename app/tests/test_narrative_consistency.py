import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.domain.canon_evidence.schemas import CanonEvidenceHit, CanonEvidenceSet, CanonQueryEvidence
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.narrative_canon_query.schemas import NarrativeCanonQuery, NarrativeCanonQuerySet
from app.domain.narrative_composer.schemas import NarrativeComposerResult
from app.domain.narrative_consistency import (
    NarrativeConsistencyAgent,
    NarrativeConsistencyAssertion,
    NarrativeConsistencyReview,
    render_revision_instruction,
)
from app.domain.narrative_consistency.agent import _segment_paragraphs
from app.domain.plot_outline.schemas import PlannedEvent
from app.domain.turn_resolution.schemas import ActionResolutionFrame, TurnResolutionResult
from app.domain.turn_resolution.views import project_turn_resolution
from app.graph import generation_nodes


class _SpyGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


def _current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_1", location="广场", participants=["莱尔", "埃利奥"]),
        player=PlayerState(name="莱尔"),
    )


def _turn_resolution() -> TurnResolutionResult:
    return TurnResolutionResult(action_resolutions=[ActionResolutionFrame(
        action_id="A1",
        sequence=1,
        actor=["莱尔"],
        action_type="speak",
        outcome_band="success",
        effect_reasoning="埃利奥接受付款条件。",
        resolved_fact="埃利奥向莱尔支付三枚金币。",
    )])


def _composition(scene_text: str) -> NarrativeComposerResult:
    return NarrativeComposerResult(
        planning_reasoning="莱尔提出条件，埃利奥接受并付款。",
        must_establish_facts=["埃利奥向莱尔支付三枚金币。"],
        must_not_establish_facts=["莱尔向埃利奥支付三枚金币。"],
        information_boundaries=[],
        scene_stopping_point="莱尔收到三枚金币后仍在广场。",
        planned_events=[PlannedEvent(
            event_id="e1",
            event_fact="埃利奥向莱尔支付三枚金币。",
            linked_outcome_ids=["A1"],
            involved_entities=["埃利奥"],
        )],
        scene_reasoning="正文落实付款结果。",
        scene_text=scene_text,
        closing_prompt="交易已经完成，你会怎么做？",
    )


def _check(
    gateway: _SpyGateway,
    scene_text: str,
    *,
    query_set: NarrativeCanonQuerySet | None = None,
    evidence_set: CanonEvidenceSet | None = None,
    turn_resolution_json: str | None = None,
):
    return NarrativeConsistencyAgent(gateway).check(
        user_input="我要求埃利奥支付三枚金币。",
        current_state=_current_state(),
        turn_resolution_json=(
            turn_resolution_json or project_turn_resolution(_turn_resolution())
        ),
        composition=_composition(scene_text),
        canon_query_set=query_set,
        canon_evidence_set=evidence_set,
        trace_id="trace-1",
    )


def _assertion_payload(**overrides):
    payload = {
        "issue_type": "contract_violation",
        "paragraph_ids": ["P001"],
        "incorrect_statement": "正文写成莱尔向埃利奥支付了两枚金币。",
        "authoritative_fact": "Player Input 与 TR 均确定埃利奥向莱尔支付三枚金币，主体、方向和数量相反。",
        "required_revision": "改正付款主体、方向和数量；保留交易场景中的其他对白与反应。",
        "authority_refs": ["Player Input", "TurnResolutionResult"],
        "canon_query_id": None,
        "evidence_ids": [],
    }
    payload.update(overrides)
    return payload


def test_pass_returns_no_assertions_and_preserves_compact_input_contract():
    gateway = _SpyGateway({
        "audit_summary": "正文与本轮权威输入不存在明确冲突。",
        "canon_findings": [],
        "decision": "pass",
        "assertions": [],
    })

    review = _check(gateway, "埃利奥向莱尔支付了三枚金币。")

    assert review.decision == "pass"
    assert review.assertions == []
    call = gateway.calls[0]
    assert call["task"] == "narrative_consistency_check"
    assert call["temperature"] == 0.2
    assert "max_chars" not in call["input_data"]
    assert "context_facts_json" not in call["input_data"]
    assert "recent_scene_json" not in call["input_data"]
    assert call["input_data"]["historical_canon_evidence_json"] == "[]"
    assert "[P001]" in call["input_data"]["segmented_scene_text"]


def test_check_accepts_projected_turn_resolution_without_effect_reasoning():
    gateway = _SpyGateway({
        "audit_summary": "正文与本轮权威输入不存在明确冲突。",
        "canon_findings": [],
        "decision": "pass",
        "assertions": [],
    })
    projected = json.dumps({
        "action_resolutions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["莱尔"],
            "action_type": "speak",
            "outcome_band": "success",
            "resolved_fact": "埃利奥向莱尔支付三枚金币。",
        }],
        "total_resolutions": 1,
    }, ensure_ascii=False)

    _check(
        gateway,
        "埃利奥向莱尔支付了三枚金币。",
        turn_resolution_json=projected,
    )

    assert gateway.calls[0]["input_data"]["turn_resolution_json"] == projected


def test_revise_outputs_composer_ready_semantic_assertion():
    gateway = _SpyGateway({
        "audit_summary": "发现一项付款方向和数量错误。",
        "canon_findings": [],
        "decision": "revise",
        "assertions": [_assertion_payload()],
    })

    review = _check(gateway, "莱尔向埃利奥支付了两枚金币。")
    instruction = render_revision_instruction(review)

    assert review.decision == "revise"
    assert "正文错误：正文写成莱尔向埃利奥支付了两枚金币。" in instruction
    assert "正确事实：Player Input 与 TR 均确定埃利奥向莱尔支付三枚金币" in instruction
    assert "修订要求：改正付款主体、方向和数量" in instruction
    assert "contract_violation" not in instruction


def test_canon_assertion_requires_real_query_and_evidence():
    query_set = NarrativeCanonQuerySet(
        thought_process="核对知识来源。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P002"],
            claim="埃利奥此前熟知灰烬图书馆。",
            claim_type="knowledge_provenance",
            entities=["埃利奥", "灰烬图书馆"],
            lookup_queries=["埃利奥何时得知灰烬图书馆"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(
        results=[CanonQueryEvidence(
            query_id="q1",
            claim=query_set.queries[0].claim,
            evidence=[CanonEvidenceHit(
                evidence_id="turn_4_p002",
                source_type="scene_paragraph",
                turn_index=4,
                scene_id="scene_4",
                paragraph_index=2,
                text="希尔达首次告诉埃利奥灰烬图书馆的线索。",
                matched_entities=["埃利奥", "灰烬图书馆"],
                retrieval_channels=["entity_all_in_document"],
                score=0.91,
            )],
        )],
        indexed_documents=20,
        indexed_turns=4,
    )
    gateway = _SpyGateway({
        "audit_summary": "发现一项知识来源冲突。",
        "canon_findings": [{
            "query_id": "q1",
            "verdict": "contradiction",
            "reasoning": "历史明确写出希尔达首次告知，和正文的提前认知不能同时成立。",
            "evidence_ids": ["turn_4_p002"],
        }],
        "decision": "revise",
        "assertions": [_assertion_payload(
            issue_type="canon_contradiction",
            incorrect_statement="正文把埃利奥对灰烬图书馆的认知提前到希尔达告知之前。",
            authoritative_fact="历史原文明确演出希尔达告知后埃利奥才形成前往灰烬图书馆的方向。",
            required_revision="删除所有提前认知、提前探索及由此形成的招募动机；保留其他巡演经历。",
            authority_refs=["Historical Canon Evidence"],
            canon_query_id="q1",
            evidence_ids=["turn_4_p002"],
        )],
    })

    review = _check(
        gateway,
        "埃利奥说自己早已探索过灰烬图书馆。",
        query_set=query_set,
        evidence_set=evidence_set,
    )

    assert review.assertions[0].canon_query_id == "q1"
    projected = json.loads(
        gateway.calls[0]["input_data"]["historical_canon_evidence_json"]
    )
    assert projected[0]["historical_records"][0]["evidence_id"] == "turn_4_p002"
    assert projected[0]["audit_target"] == "埃利奥此前熟知灰烬图书馆。"
    assert "claim" not in projected[0]


def test_each_projected_query_requires_exactly_one_canon_finding():
    query_set = NarrativeCanonQuerySet(
        thought_process="核对历史。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P001"],
            claim="埃利奥此前去过灰烬图书馆。",
            claim_type="past_event",
            entities=["埃利奥", "灰烬图书馆"],
            lookup_queries=["埃利奥是否去过灰烬图书馆"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(results=[CanonQueryEvidence(
        query_id="q1",
        claim=query_set.queries[0].claim,
        evidence=[CanonEvidenceHit(
            evidence_id="turn_4_p002",
            source_type="scene_paragraph",
            turn_index=4,
            scene_id="scene_4",
            paragraph_index=2,
            text="历史只提到埃利奥后来听说了图书馆。",
            score=0.5,
        )],
    )])
    gateway = _SpyGateway({
        "audit_summary": "证据不足。",
        "canon_findings": [],
        "decision": "pass",
        "assertions": [],
    })

    with pytest.raises(ValueError, match="cover every projected query"):
        _check(
            gateway,
            "埃利奥说自己去过灰烬图书馆。",
            query_set=query_set,
            evidence_set=evidence_set,
        )


def test_insufficient_is_valid_without_canon_revision_assertion():
    query_set = NarrativeCanonQuerySet(
        thought_process="核对历史。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P001"],
            claim="埃利奥此前去过灰烬图书馆。",
            claim_type="past_event",
            entities=["埃利奥", "灰烬图书馆"],
            lookup_queries=["埃利奥是否去过灰烬图书馆"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(results=[CanonQueryEvidence(
        query_id="q1",
        claim=query_set.queries[0].claim,
        evidence=[CanonEvidenceHit(
            evidence_id="turn_4_p002",
            source_type="scene_paragraph",
            turn_index=4,
            scene_id="scene_4",
            paragraph_index=2,
            text="历史只提到埃利奥后来听说了图书馆。",
            score=0.5,
        )],
    )])
    gateway = _SpyGateway({
        "audit_summary": "历史没有明确证明或反驳该声明。",
        "canon_findings": [{
            "query_id": "q1",
            "verdict": "insufficient",
            "reasoning": "原文相关，但没有明确证明埃利奥此前是否去过。",
            "evidence_ids": ["turn_4_p002"],
        }],
        "decision": "pass",
        "assertions": [],
    })

    review = _check(
        gateway,
        "埃利奥说自己去过灰烬图书馆。",
        query_set=query_set,
        evidence_set=evidence_set,
    )

    assert review.canon_findings[0].verdict == "insufficient"


def test_canon_assertion_rejects_unavailable_evidence_reference():
    gateway = _SpyGateway({
        "audit_summary": "发现冲突。",
        "canon_findings": [],
        "decision": "revise",
        "assertions": [_assertion_payload(
            issue_type="canon_contradiction",
            authority_refs=["Historical Canon Evidence"],
            canon_query_id="q1",
            evidence_ids=["missing"],
        )],
    })

    with pytest.raises(ValueError, match="unavailable query"):
        _check(gateway, "错误正文。")


def test_canon_assertion_may_cite_evidence_retrieved_by_another_query():
    query_set = NarrativeCanonQuerySet(
        thought_process="两条查询召回了可交叉使用的历史原文。",
        queries=[
            NarrativeCanonQuery(
                query_id="q1",
                paragraph_ids=["P001"],
                claim="埃利奥此前去过灰烬图书馆。",
                claim_type="past_event",
                entities=["埃利奥", "灰烬图书馆"],
                lookup_queries=["埃利奥是否去过灰烬图书馆"],
                priority="high",
            ),
            NarrativeCanonQuery(
                query_id="q2",
                paragraph_ids=["P001"],
                claim="埃利奥后来才得知灰烬图书馆。",
                claim_type="knowledge_provenance",
                entities=["埃利奥", "灰烬图书馆"],
                lookup_queries=["埃利奥何时得知灰烬图书馆"],
                priority="high",
            ),
        ],
    )
    evidence_set = CanonEvidenceSet(
        results=[
            CanonQueryEvidence(
                query_id="q1",
                claim=query_set.queries[0].claim,
                evidence=[CanonEvidenceHit(
                    evidence_id="turn_3_p001",
                    source_type="scene_paragraph",
                    turn_index=3,
                    scene_id="scene_3",
                    paragraph_index=1,
                    text="埃利奥谈到了这座图书馆。",
                    score=0.5,
                )],
            ),
            CanonQueryEvidence(
                query_id="q2",
                claim=query_set.queries[1].claim,
                evidence=[CanonEvidenceHit(
                    evidence_id="turn_4_p002",
                    source_type="scene_paragraph",
                    turn_index=4,
                    scene_id="scene_4",
                    paragraph_index=2,
                    text="希尔达首次告诉埃利奥灰烬图书馆的名字。",
                    score=0.9,
                )],
            ),
        ],
    )
    gateway = _SpyGateway({
        "audit_summary": "发现历史形成冲突。",
        "canon_findings": [
            {
                "query_id": "q1",
                "verdict": "contradiction",
                "reasoning": "希尔达首次告知的历史与提前探索不能同时成立。",
                "evidence_ids": ["turn_4_p002"],
            },
            {
                "query_id": "q2",
                "verdict": "explicitly_confirmed",
                "reasoning": "历史原文明确写出希尔达首次告诉埃利奥图书馆的名字。",
                "evidence_ids": ["turn_4_p002"],
            },
        ],
        "decision": "revise",
        "assertions": [_assertion_payload(
            issue_type="canon_contradiction",
            authority_refs=["Historical Canon Evidence"],
            canon_query_id="q1",
            evidence_ids=["turn_4_p002"],
        )],
    })

    review = _check(
        gateway,
        "埃利奥说自己早已探索过灰烬图书馆。",
        query_set=query_set,
        evidence_set=evidence_set,
    )

    assert review.assertions[0].canon_query_id == "q1"
    assert review.assertions[0].evidence_ids == ["turn_4_p002"]


def test_schema_rejects_pass_with_assertions_and_revise_without_assertions():
    with pytest.raises(ValueError, match="pass must not contain assertions"):
        NarrativeConsistencyReview(
            audit_summary="错误。",
            canon_findings=[],
            decision="pass",
            assertions=[NarrativeConsistencyAssertion.model_validate(_assertion_payload())],
        )
    with pytest.raises(ValueError, match="revise requires"):
        NarrativeConsistencyReview(
            audit_summary="错误。",
            canon_findings=[],
            decision="revise",
            assertions=[],
        )


def test_segmentation_preserves_every_natural_paragraph_once():
    paragraphs = ["甲" * 260, "乙" * 260, "丙" * 260, "丁" * 260, "戊" * 120]
    segments = _segment_paragraphs(paragraphs)

    assert [paragraph for segment in segments for paragraph in segment.paragraphs] == paragraphs
    assert [pid for segment in segments for pid in segment.paragraph_ids] == [
        "P001", "P002", "P003", "P004", "P005",
    ]


def test_prompt_defines_assertions_and_forbids_patch_replacement():
    prompt = Path(
        "app/prompts/generation/narrative_consistency_check_v1.md"
    ).read_text(encoding="utf-8")

    assert "incorrect_statement" in prompt
    assert "authoritative_fact" in prompt
    assert "required_revision" in prompt
    assert "正文错误：{{incorrect_statement}}" in prompt
    assert "不得输出 replacement、patch 或修改后的正文" in prompt
    assert "形成之前" in prompt
    assert "默认判定始终是 `insufficient`" in prompt
    assert "每个 query 恰好输出一项" in prompt


def test_check_node_stores_review_without_mutating_composition(monkeypatch):
    composition = _composition("原始正文。")
    review = NarrativeConsistencyReview(
        audit_summary="发现错误。",
        canon_findings=[],
        decision="revise",
        assertions=[NarrativeConsistencyAssertion.model_validate(_assertion_payload())],
    )

    class _FakeAgent:
        def __init__(self, _gateway):
            pass

        def check(self, **_kwargs):
            return review

    monkeypatch.setattr(generation_nodes, "NarrativeConsistencyAgent", _FakeAgent)
    monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())

    result = generation_nodes.check_narrative_consistency_node({
        "user_input": "行动",
        "current_state": _current_state(),
        "turn_resolution": _turn_resolution(),
        "narrative_composition": composition,
        "trace_id": "trace-1",
    })

    assert result["narrative_consistency_review"] == review
    assert "narrative_composition" not in result
    assert result["events"][0]["data"]["revision_required"] is True


def test_revision_node_reuses_composer_with_rendered_assertions(monkeypatch):
    original = _composition("错误正文。")
    revised = _composition("正确正文。")
    review = NarrativeConsistencyReview(
        audit_summary="发现错误。",
        canon_findings=[],
        decision="revise",
        assertions=[NarrativeConsistencyAssertion.model_validate(_assertion_payload())],
    )
    captured = {}
    streamed = []

    class _FakeComposer:
        def __init__(self, _gateway):
            pass

        def compose(self, context, **kwargs):
            captured["context"] = context
            captured.update(kwargs)
            kwargs["on_stream_start"](1)
            kwargs["on_scene_text_delta"]("正确正文。")
            return revised

    monkeypatch.setattr(generation_nodes, "NarrativeComposerAgent", _FakeComposer)
    monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())
    monkeypatch.setattr(generation_nodes, "get_stream_writer", lambda: streamed.append)
    monkeypatch.setattr(
        generation_nodes,
        "_composition_output_state",
        lambda _state, composition: {
            "narrative_composition": composition,
            "outline": composition.to_outline(),
            "scene_result": composition.to_scene_result(),
        },
    )

    result = generation_nodes.revise_narrative_node({
        "context": SimpleNamespace(),
        "recent_scene": None,
        "next_turn_index": 5,
        "narrative_composition": original,
        "narrative_consistency_review": review,
        "trace_id": "trace-1",
    })

    request = captured["revision_request"]
    assert request.previous_scene_text == "错误正文。"
    assert "正文错误：正文写成莱尔向埃利奥支付了两枚金币。" in request.instruction
    assert result["scene_result"].scene_text == "正确正文。"
    assert result["events"][0]["step"] == "narrative_revised"
    assert [event["step"] for event in streamed] == [
        "narrative_revision_started",
        "scene_stream_started",
        "scene_text_delta",
    ]
