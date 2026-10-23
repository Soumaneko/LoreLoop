import json
from types import SimpleNamespace
import pytest
from app.core.diagnostics import DiagnosticLogger
from app.domain.canon_evidence.schemas import CanonEvidenceHit, CanonEvidenceSet, CanonQueryEvidence
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.narrative_canon_query.schemas import NarrativeCanonQuery, NarrativeCanonQuerySet
from app.domain.narrative_composer.schemas import NarrativeComposerResult
from app.domain.narrative_consistency import (
    NarrativeConsistencyAgent,
    NarrativeConsistencyAssertion,
    NarrativeConsistencyReview,
    NarrativeConsistencyWarning,
    render_revision_instruction,
)
from app.domain.narrative_consistency.agent import _segment_paragraphs
from app.domain.plot_outline.schemas import PlannedEvent
from app.domain.action_judgment.schemas import ActionJudgment
from app.domain.action_judgment.views import project_action_judgments
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


def _action_judgments() -> list[ActionJudgment]:
    return [ActionJudgment(
        action_id="A1",
        sequence=1,
        actor=["莱尔"],
        action_type="speak",
        action_text="要求埃利奥支付三枚金币",
        resolution_source="automatic",
        outcome_band="success",
    )]


def _composition(scene_text: str) -> NarrativeComposerResult:
    return NarrativeComposerResult(
        action_resolutions=[{
            "action_id": "A1",
            "outcome_band": "success",
            "resolved_fact": "埃利奥向莱尔支付三枚金币。",
        }],
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
    action_judgments_json: str | None = None,
):
    return NarrativeConsistencyAgent(gateway).check(
        user_input="我要求埃利奥支付三枚金币。",
        current_state=_current_state(),
        action_judgments_json=(
            action_judgments_json or project_action_judgments(_action_judgments())
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
        "authority_refs": ["Player Input", "Action Judgments"],
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
    assert call["input_data"]["historical_canon_evidence_json"] == "[]"
    assert "[P001]" in call["input_data"]["segmented_scene_text"]


def test_check_accepts_projected_action_judgments():
    gateway = _SpyGateway({
        "audit_summary": "正文与本轮权威输入不存在明确冲突。",
        "canon_findings": [],
        "decision": "pass",
        "assertions": [],
    })
    projected = json.dumps([
        {
            "action_id": "A1",
            "sequence": 1,
            "actor": ["莱尔"],
            "action_type": "speak",
            "outcome_band": "success",
            "action_text": "要求埃利奥支付三枚金币",
            "resolution_source": "automatic",
            "risk_level": "medium",
        }
    ], ensure_ascii=False)

    _check(
        gateway,
        "埃利奥向莱尔支付了三枚金币。",
        action_judgments_json=projected,
    )

    assert gateway.calls[0]["input_data"]["action_judgments_json"] == projected


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
    assertion = review.assertions[0]
    assert assertion.incorrect_statement in instruction
    assert assertion.authoritative_fact in instruction
    assert assertion.required_revision in instruction


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


def test_unknown_canon_assertion_is_ignored_with_warning():
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

    agent = NarrativeConsistencyAgent(gateway)
    review = agent.check(
        user_input="行动",
        current_state=_current_state(),
        action_judgments_json=project_action_judgments(_action_judgments()),
        composition=_composition("错误正文。"),
        trace_id="trace-1",
    )

    assert review.decision == "pass"
    assert review.assertions == []
    assert agent.last_warnings[0].reason == "unknown_query_id"


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
                "reasoning": "q2 召回的首次告知历史与提前探索不能同时成立。",
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

    agent = NarrativeConsistencyAgent(gateway)
    review = agent.check(
        user_input="行动",
        current_state=_current_state(),
        action_judgments_json=project_action_judgments(_action_judgments()),
        composition=_composition("埃利奥说自己早已探索过灰烬图书馆。"),
        canon_query_set=query_set,
        canon_evidence_set=evidence_set,
        trace_id="trace-1",
    )

    assert review.decision == "revise"
    assert len(review.assertions) == 1
    assert review.assertions[0].canon_query_id == "q1"
    assert review.assertions[0].evidence_ids == ["turn_4_p002"]
    assert agent.last_warnings == []


def test_unknown_evidence_assertion_is_ignored_while_valid_one_is_retained():
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
            text="希尔达首次告诉埃利奥灰烬图书馆的名字。",
            score=0.9,
        )],
    )])
    gateway = _SpyGateway({
        "audit_summary": "发现历史冲突。",
        "canon_findings": [{
            "query_id": "q1",
            "verdict": "contradiction",
            "reasoning": "首次告知历史与提前探索不能同时成立。",
            "evidence_ids": ["turn_4_p002"],
        }],
        "decision": "revise",
        "assertions": [
            _assertion_payload(
                issue_type="canon_contradiction",
                authority_refs=["Historical Canon Evidence"],
                canon_query_id="q1",
                evidence_ids=["turn_4_p002"],
            ),
            _assertion_payload(
                issue_type="canon_contradiction",
                authority_refs=["Historical Canon Evidence"],
                canon_query_id="q1",
                evidence_ids=["missing"],
            ),
        ],
    })
    agent = NarrativeConsistencyAgent(gateway)

    review = agent.check(
        user_input="行动",
        current_state=_current_state(),
        action_judgments_json=project_action_judgments(_action_judgments()),
        composition=_composition("埃利奥说自己早已探索过灰烬图书馆。"),
        canon_query_set=query_set,
        canon_evidence_set=evidence_set,
        trace_id="trace-1",
    )

    assert review.decision == "revise"
    assert len(review.assertions) == 1
    assert review.assertions[0].evidence_ids == ["turn_4_p002"]
    assert agent.last_warnings[0].reason == "unknown_evidence"


@pytest.mark.parametrize(
    ("assertion_query_id", "warning_reason"),
    [
        (None, "missing_query_id"),
        ("q1", "non_contradiction_finding"),
    ],
)
def test_invalid_canon_assertion_is_ignored_and_decision_becomes_pass(
    assertion_query_id,
    warning_reason,
):
    query_set = NarrativeCanonQuerySet(
        thought_process="核对身份。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P001"],
            claim="莱尔已经抵达广场。",
            claim_type="location_or_presence",
            entities=["莱尔"],
            lookup_queries=["莱尔 抵达 广场"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(results=[CanonQueryEvidence(
        query_id="q1",
        claim=query_set.queries[0].claim,
        evidence=[CanonEvidenceHit(
            evidence_id="turn_1_p001",
            source_type="scene_paragraph",
            turn_index=1,
            scene_id="scene_1",
            paragraph_index=1,
            text="莱尔抵达广场。",
            score=0.9,
        )],
    )])
    gateway = _SpyGateway({
        "audit_summary": "错误地发现了另一项冲突。",
        "canon_findings": [{
            "query_id": "q1",
            "verdict": "explicitly_confirmed",
            "reasoning": "历史明确确认莱尔抵达广场。",
            "evidence_ids": ["turn_1_p001"],
        }],
        "decision": "revise",
        "assertions": [_assertion_payload(
            issue_type="canon_contradiction",
            authority_refs=["Historical Canon Evidence"],
            canon_query_id=assertion_query_id,
            evidence_ids=["turn_1_p001"],
        )],
    })
    agent = NarrativeConsistencyAgent(gateway)

    review = agent.check(
        user_input="行动",
        current_state=_current_state(),
        action_judgments_json=project_action_judgments(_action_judgments()),
        composition=_composition("莱尔抵达广场。"),
        canon_query_set=query_set,
        canon_evidence_set=evidence_set,
        trace_id="trace-1",
    )

    assert review.decision == "pass"
    assert review.assertions == []
    assert agent.last_warnings[0].reason == warning_reason


def test_contradiction_finding_without_valid_assertion_still_fails():
    query_set = NarrativeCanonQuerySet(
        thought_process="核对历史。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P001"],
            claim="莱尔此前离开过广场。",
            claim_type="past_event",
            entities=["莱尔"],
            lookup_queries=["莱尔 离开 广场"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(results=[CanonQueryEvidence(
        query_id="q1",
        claim=query_set.queries[0].claim,
        evidence=[CanonEvidenceHit(
            evidence_id="turn_1_p001",
            source_type="scene_paragraph",
            turn_index=1,
            scene_id="scene_1",
            paragraph_index=1,
            text="莱尔始终留在广场。",
            score=0.9,
        )],
    )])
    gateway = _SpyGateway({
        "audit_summary": "发现历史冲突。",
        "canon_findings": [{
            "query_id": "q1",
            "verdict": "contradiction",
            "reasoning": "历史明确写出莱尔始终没有离开。",
            "evidence_ids": ["turn_1_p001"],
        }],
        "decision": "revise",
        "assertions": [_assertion_payload(
            issue_type="canon_contradiction",
            authority_refs=["Historical Canon Evidence"],
            canon_query_id=None,
            evidence_ids=["turn_1_p001"],
        )],
    })

    with pytest.raises(
        ValueError,
        match="each contradiction finding requires exactly one valid canon assertion",
    ):
        _check(
            gateway,
            "莱尔声称自己此前离开过广场。",
            query_set=query_set,
            evidence_set=evidence_set,
        )


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


def test_check_node_stores_review_without_mutating_composition(
    monkeypatch,
    tmp_path,
):
    composition = _composition("原始正文。")
    review = NarrativeConsistencyReview(
        audit_summary="发现错误。",
        canon_findings=[],
        decision="revise",
        assertions=[NarrativeConsistencyAssertion.model_validate(_assertion_payload())],
    )

    class _FakeAgent:
        def __init__(self, _gateway):
            self.last_warnings = [NarrativeConsistencyWarning(
                assertion_index=1,
                reason="missing_query_id",
            )]

        def check(self, **_kwargs):
            return review

    monkeypatch.setattr(generation_nodes, "NarrativeConsistencyAgent", _FakeAgent)
    monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")

    result = generation_nodes.check_narrative_consistency_node({
        "user_input": "行动",
        "current_state": _current_state(),
        "action_judgments": _action_judgments(),
        "narrative_composition": composition,
        "trace_id": "trace-1",
        "runtime_services": SimpleNamespace(diagnostics=diagnostics),
    })

    assert result["narrative_consistency_review"] == review
    assert result["narrative_consistency_warnings"][0].reason == "missing_query_id"
    assert "narrative_composition" not in result
    assert result["events"][0]["data"]["revision_required"] is True
    assert result["events"][0]["data"]["warnings"][0]["reason"] == "missing_query_id"
    diagnostic = diagnostics.read(trace_id="trace-1")[0]
    assert diagnostic["code"] == "ignored_canon_assertion"
    assert diagnostic["details"]["reason"] == "missing_query_id"


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

    class _FakeSourceRewriter:
        def __init__(self, _gateway):
            pass

        def rewrite(self, context, request, **kwargs):
            captured["context"] = context
            captured["request"] = request
            captured.update(kwargs)
            kwargs["on_stream_start"](1)
            kwargs["on_scene_text_delta"]("正确正文。")
            return revised

    monkeypatch.setattr(
        generation_nodes,
        "SourceNarrativeRewriter",
        _FakeSourceRewriter,
    )
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

    request = captured["request"]
    assert request.source_scene_text == "错误正文。"
    assert review.assertions[0].incorrect_statement in request.requirements[0]
    assert result["scene_result"].scene_text == "正确正文。"
    assert result["events"][0]["step"] == "narrative_revised"
    assert [event["step"] for event in streamed] == [
        "narrative_revision_started",
        "scene_stream_started",
        "scene_text_delta",
    ]


def test_skipped_check_reaches_player_and_survives_reload(configure_runtime_services):
    from app.core.llm import MockLLM
    from app.player_server import PlayerHandler
    from app.runtime.session import get_campaign_runtime
    from app.runtime.turn_history import CampaignHistory
    from app.runtime.runtime_feedback import notices_from_event

    mock = MockLLM()
    mock.set_responses(["invalid secret-model-text", "invalid secret-model-text"])
    services = configure_runtime_services(gateway_factory=mock.build_gateway)
    runtime = get_campaign_runtime()
    def stream():
        result = generation_nodes.check_narrative_consistency_node({
            "user_input": "观察", "current_state": _current_state(),
            "action_judgments": _action_judgments(), "narrative_composition": _composition("原始正文。"),
            "trace_id": "skipped-check", "runtime_services": services,
        })
        yield from result["events"]
    operation_id = runtime.operations.submit_stream("generation", stream)
    assert runtime.operations.wait(operation_id, timeout=5).status == "succeeded"
    events = runtime.operations.read_events(operation_id).events
    checked = next(event for event in events if event.step == "narrative_checked")
    projected = PlayerHandler._player_event({"step": checked.step, "data": checked.data})
    assert projected["data"] == {"validation_skipped": True}
    notice = next(event for event in events if event.step == "runtime_notice")
    public = PlayerHandler._player_event({"step": notice.step, "data": notice.data})
    assert "【后校验】未完成" in public["data"]["message"]
    assert "secret-model-text" not in str(public)
    reloaded = CampaignHistory(runtime.paths.data_dir).runtime_notices()
    assert reloaded[0]["notice_id"] == notice.data["notice_id"]
    assert "【后校验】未完成" in reloaded[0]["message"]
    assert notices_from_event("narrative_checked", {"validation_skipped": False}) == []
    assert notices_from_event("unrelated", {"warning_codes": ["unknown"], "secret": "hidden"}) == []
