"""Opt-in real-LLM quality test for Check -> Composer revision.

This module never calls a provider during the normal test suite. Run it explicitly with:

    RUN_REAL_LLM_TESTS=1 env PYTHONPATH=. uv run pytest -q -s \
        app/tests/test_narrative_revision_live.py
"""

import json
import os

import pytest

from app.core.llm import LLMConfig, create_gateway
from app.domain.canon_evidence.schemas import (
    CanonEvidenceHit,
    CanonEvidenceSet,
    CanonQueryEvidence,
)
from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)
from app.domain.narrative_canon_query.schemas import (
    NarrativeCanonQuery,
    NarrativeCanonQuerySet,
)
from app.domain.narrative_composer import (
    NarrativeComposerAgent,
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.narrative_rewrite import SourceNarrativeRewriter, SourceRewriteRequest
from app.domain.narrative_consistency import (
    NarrativeConsistencyAgent,
    render_revision_instruction,
)
from app.domain.plot_outline.schemas import PlannedEvent
from app.domain.turn_resolution.schemas import (
    ActionResolutionFrame,
    TurnResolutionResult,
)
from app.domain.turn_resolution.views import project_turn_resolution


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_REAL_LLM_TESTS") != "1",
    reason="real LLM tests are opt-in; set RUN_REAL_LLM_TESTS=1",
)


PLAYER_INPUT = (
    "我只把三枚金币交给埃利奥作为定金，并明确告诉他：今天我不会离开广场，"
    "也没有答应加入他的旅程。"
)

FLAWED_SCENE_TEXT = """莱尔摊开手掌。埃利奥反而取出两枚金币塞给莱尔，说这笔定金由他来付。

旁白随即断言：莱尔原有十枚金币，收到两枚以后，现在一共有十三枚。

莱尔没有再询问，便郑重点头，承诺永远加入埃利奥的巡演队伍；他随即登上马车，与埃利奥一起离开了广场。

埃利奥说，他多年前曾在灰烬图书馆住过半年，还亲自探索过地下书库，因此才来到老橡树镇招募莱尔。

导演事件要求此处暂时隐藏真相，TurnResolutionResult 已经完成，所以下游模块应当结束本场景。"""


def _current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_026",
            location="老橡树镇中央广场",
            participants=["莱尔", "埃利奥", "希尔达"],
        ),
        world_clock=WorldClock(day=7, phase="下午"),
        player=PlayerState(name="莱尔"),
        resources=ResourcesState(money=10, money_unit="金币"),
    )


def _turn_resolution() -> TurnResolutionResult:
    return TurnResolutionResult(
        resolution_reasoning=(
            "交易动作成功；严格保留玩家限定，不扩展为同行承诺或离开广场。"
        ),
        action_resolutions=[ActionResolutionFrame(
            action_id="A1",
            sequence=1,
            actor=["莱尔"],
            action_type="支付定金并声明边界",
            outcome_band="success",
            effect_reasoning="埃利奥接受三枚金币，也接受莱尔今日不离开且未承诺同行的边界。",
            resolved_fact="莱尔向埃利奥支付三枚金币；莱尔仍在广场，且没有承诺加入埃利奥的旅程。",
        )],
    )


def _context_facts() -> ContextFactSet:
    return ContextFactSet(
        turn_focus="支付有限定条件的定金",
        facts=[
            ContextFact(
                fact_id="f1",
                fact="莱尔已知(亲身持有): 支付前钱袋中有十枚金币。",
            ),
            ContextFact(
                fact_id="f2",
                fact="埃利奥已知(莱尔当面声明): 莱尔今天不离开广场，也未承诺加入旅程。",
            ),
        ],
    )


def _composition() -> NarrativeComposerResult:
    return NarrativeComposerResult(
        planning_reasoning="本轮应落实有限定条件的三金币定金交易。",
        must_establish_facts=[
            "莱尔向埃利奥支付三枚金币。",
            "莱尔仍留在广场，且没有承诺加入埃利奥的旅程。",
        ],
        must_not_establish_facts=[
            "不得颠倒付款方向或改变金币数量。",
            "不得替莱尔承诺同行或让莱尔离开广场。",
        ],
        information_boundaries=[],
        scene_stopping_point="定金被埃利奥收下，莱尔仍在广场。",
        planned_events=[PlannedEvent(
            event_id="e1",
            event_fact="埃利奥接受莱尔支付的三枚金币及其行动边界。",
            linked_outcome_ids=["A1"],
            involved_entities=["莱尔", "埃利奥"],
        )],
        scene_reasoning="这是一份故意注入多种一致性错误的测试正文。",
        scene_text=FLAWED_SCENE_TEXT,
        closing_prompt="交易之后，你准备如何继续交谈？",
        next_action_directions=[],
    )


def _canon_inputs() -> tuple[NarrativeCanonQuerySet, CanonEvidenceSet]:
    query_set = NarrativeCanonQuerySet(
        thought_process="第四段声称埃利奥在更早以前熟悉并探索过灰烬图书馆。",
        queries=[NarrativeCanonQuery(
            query_id="q1",
            paragraph_ids=["P004"],
            claim="埃利奥多年前已经居住并探索过灰烬图书馆。",
            claim_type="knowledge_provenance",
            entities=["埃利奥", "灰烬图书馆"],
            lookup_queries=["埃利奥何时第一次得知灰烬图书馆"],
            priority="high",
        )],
    )
    evidence_set = CanonEvidenceSet(
        indexed_documents=26,
        indexed_turns=25,
        results=[CanonQueryEvidence(
            query_id="q1",
            claim=query_set.queries[0].claim,
            evidence=[CanonEvidenceHit(
                evidence_id="turn_025_p006",
                source_type="scene_paragraph",
                turn_index=25,
                scene_id="scene_025",
                paragraph_index=6,
                context_before=(
                    "埃利奥皱起眉，问希尔达口中的那座藏有缚灵资料的废墟究竟叫什么。"
                ),
                text=(
                    "希尔达第一次向埃利奥说出‘灰烬图书馆’这个名字，并指出"
                    "《魂匣与缚灵诸法考》可能仍埋在那里的地下书库。"
                ),
                context_after=(
                    "埃利奥重复了一遍这个陌生地名，随后才决定把灰烬图书馆作为下一处调查目标。"
                ),
                matched_entities=["埃利奥", "灰烬图书馆"],
                matched_terms=["第一次", "陌生地名", "调查目标"],
                retrieval_channels=["entity_all_in_document", "semantic_trigram"],
                score=0.93,
            )],
        )],
    )
    return query_set, evidence_set


def _composer_context() -> NarrativeComposerContext:
    return NarrativeComposerContext(
        user_input=PLAYER_INPUT,
        current_state=_current_state(),
        turn_resolution=_turn_resolution(),
        core_memory=CoreMemory(items=[CoreMemoryItem(
            id="core_1",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content="这是重视行动后果、角色认知来源和连续性的文字冒险。",
        )]),
        context_facts=_context_facts(),
        entity_profiles=[],
        npc_profiles=[],
        director_event_consumptions=[],
        director_npc_injection=None,
    )


def test_real_llm_check_assertions_then_composer_revision(tmp_path):
    """Exercise the paid two-call quality path and print its complete result."""
    if not LLMConfig.is_configured():
        pytest.skip("OPENAI_API_KEY is required when RUN_REAL_LLM_TESTS=1")

    trace_path = tmp_path / "narrative_revision_live_traces.jsonl"
    gateway = create_gateway(log_path=str(trace_path))
    trace_id = "live-narrative-revision-multi-error"
    query_set, evidence_set = _canon_inputs()
    flawed_composition = _composition()

    review = NarrativeConsistencyAgent(gateway).check(
        user_input=PLAYER_INPUT,
        current_state=_current_state(),
        turn_resolution_json=project_turn_resolution(_turn_resolution()),
        composition=flawed_composition,
        canon_query_set=query_set,
        canon_evidence_set=evidence_set,
        trace_id=trace_id,
    )

    assert review.decision == "revise"
    cited_paragraphs = {
        paragraph_id
        for assertion in review.assertions
        for paragraph_id in assertion.paragraph_ids
    }
    # Require the contract/arithmetic, agency, and historical-canon failures.
    # P005 (system-language leakage) is deliberately still printed as a quality
    # observation, but does not prevent the test from exercising Composer when
    # a model merges or omits one secondary assertion.
    assert {"P001", "P002", "P003", "P004"}.issubset(cited_paragraphs)
    assert any(
        assertion.issue_type == "canon_contradiction"
        and assertion.canon_query_id == "q1"
        and "turn_025_p006" in assertion.evidence_ids
        for assertion in review.assertions
    )

    instruction = render_revision_instruction(review)
    print("\n=== Narrative Consistency Review ===")
    print(json.dumps(review.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print("\n=== Expected Paragraph Coverage ===")
    print(json.dumps({
        "covered": sorted(cited_paragraphs),
        "missed": sorted(
            {"P001", "P002", "P003", "P004", "P005"} - cited_paragraphs
        ),
    }, ensure_ascii=False, indent=2))
    print("\n=== Program-rendered Revision Instruction ===")
    print(instruction)

    revised = SourceNarrativeRewriter(gateway).rewrite(
        _composer_context(),
        SourceRewriteRequest(
            source_scene_text=flawed_composition.scene_text,
            requirements=[instruction],
        ),
        recent_scene=None,
        turn_index=26,
        trace_id=trace_id,
    )

    assert revised.scene_text != flawed_composition.scene_text
    assert "三枚金币" in revised.scene_text
    assert "导演事件" not in revised.scene_text
    assert "TurnResolutionResult" not in revised.scene_text
    assert revised.planned_events
    assert "A1" in {
        outcome_id
        for event in revised.planned_events
        for outcome_id in event.linked_outcome_ids
    }

    print("\n=== Revised Narrative Composer Result ===")
    print(json.dumps(revised.model_dump(mode="json"), ensure_ascii=False, indent=2))
    print(f"\nTrace: {trace_path}")
