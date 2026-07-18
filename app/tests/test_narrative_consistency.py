from pathlib import Path

import pytest

from app.domain.context_facts.schemas import ContextFactSet
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.narrative_composer.schemas import NarrativeComposerResult
from app.domain.narrative_consistency import (
    NarrativeConsistencyAgent,
    NarrativeConsistencyOutcome,
    NarrativeConsistencyRejectedError,
    NarrativeConsistencyReview,
)
from app.domain.narrative_consistency.agent import _segment_paragraphs
from app.domain.plot_outline.schemas import PlannedEvent
from app.domain.turn_resolution.schemas import (
    ActionResolutionFrame,
    TurnResolutionResult,
)
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
        scene=SceneState(
            scene_id="scene_1",
            location="广场",
            participants=["莱尔", "埃利奥"],
        ),
        player=PlayerState(name="莱尔"),
    )


def _turn_resolution() -> TurnResolutionResult:
    return TurnResolutionResult(action_resolutions=[
        ActionResolutionFrame(
            action_id="A1",
            sequence=1,
            actor=["莱尔"],
            action_type="speak",
            outcome_band="success",
            effect_reasoning="埃利奥接受付款条件。",
            resolved_fact="埃利奥向莱尔支付三枚金币。",
        )
    ])


def _composition(scene_text: str) -> NarrativeComposerResult:
    return NarrativeComposerResult(
        planning_reasoning="莱尔提出条件，埃利奥接受并付款。",
        must_establish_facts=["埃利奥向莱尔支付三枚金币。"],
        must_not_establish_facts=["莱尔向埃利奥支付三枚金币。"],
        information_boundaries=[],
        scene_stopping_point="莱尔收到三枚金币后仍在广场。",
        planned_events=[
            PlannedEvent(
                event_id="e1",
                event_fact="埃利奥向莱尔支付三枚金币。",
                linked_outcome_ids=["A1"],
                involved_entities=["埃利奥"],
            )
        ],
        scene_reasoning="正文落实付款结果。",
        scene_text=scene_text,
        closing_prompt="交易已经完成，你会怎么做？",
    )


def _check(gateway: _SpyGateway, scene_text: str):
    return NarrativeConsistencyAgent(gateway).check(
        user_input="我要求埃利奥支付三枚金币。",
        current_state=_current_state(),
        turn_resolution=_turn_resolution(),
        context_facts=ContextFactSet(turn_focus="交易", facts=[]),
        recent_scene=None,
        composition=_composition(scene_text),
        trace_id="trace-1",
    )


def _segment_review(
    paragraph_ids: list[str],
    *,
    verdict: str = "pass",
    issues: list[dict] | None = None,
) -> dict:
    return {
        "segment_id": "S001",
        "paragraph_ids": paragraph_ids,
        "audit_reasoning": (
            "正文事实：正文中的交易继续进行；PLAYER_INPUT：行动语义一致；"
            "TR：最终状态一致；玩家决定：无新增。"
        ),
        "verdict": verdict,
        "contract_refs": ["TR:A1"],
        "issues": issues or [],
    }


def _cross_review(*, verdict: str = "pass", issues: list[dict] | None = None) -> dict:
    return {
        "verdict": verdict,
        "summary": "跨段状态与最终停点一致。",
        "issues": issues or [],
    }


def test_pass_preserves_scene_byte_for_byte():
    gateway = _SpyGateway({
        "segment_reviews": [_segment_review(["P001", "P002", "P003"])],
        "cross_segment_review": _cross_review(),
        "decision": "pass",
        "summary": "不存在明确的逻辑或一致性错误。",
        "patches": [],
    })
    scene_text = "第一段。\n\n第二段。\n\n第三段。"

    outcome = _check(gateway, scene_text)

    assert outcome.scene_text == scene_text
    assert outcome.changed is False
    call = gateway.calls[0]
    assert call["task"] == "narrative_consistency_check"
    assert call["temperature"] == 0.2
    assert "max_chars" not in call["input_data"]
    segmented = call["input_data"]["segmented_scene_text"]
    assert "## S001 | P001-P003" in segmented
    assert "[P001]\n第一段。" in segmented
    assert "plan_json" not in call["input_data"]


def test_patch_replaces_only_the_named_paragraph():
    gateway = _SpyGateway({
        "segment_reviews": [_segment_review(
            ["P001", "P002", "P003", "P004", "P005"],
            verdict="issue",
            issues=[{
                "issue_type": "contract_violation",
                "paragraph_ids": ["P003"],
                "explanation": "TR要求埃利奥向莱尔付款，正文写反。",
            }],
        )],
        "cross_segment_review": _cross_review(),
        "decision": "patch",
        "summary": "第三段颠倒付款方向。",
        "patches": [{
            "start_paragraph_id": "P003",
            "end_paragraph_id": "P003",
            "replacement": "埃利奥将三枚金币交给你。",
        }],
    })
    scene_text = "第一段保持原样。\n\n第二段保持原样。\n\n你把三枚金币交给埃利奥。\n\n第四段保持原样。\n\n第五段保持原样。"

    outcome = _check(gateway, scene_text)

    assert outcome.changed_paragraph_ids == ["P003"]
    assert outcome.scene_text == (
        "第一段保持原样。\n\n第二段保持原样。\n\n埃利奥将三枚金币交给你。"
        "\n\n第四段保持原样。\n\n第五段保持原样。"
    )


def test_patch_guard_rejects_broad_rewriting():
    gateway = _SpyGateway({
        "segment_reviews": [_segment_review(
            ["P001", "P002", "P003", "P004", "P005"],
            verdict="issue",
            issues=[{
                "issue_type": "other",
                "paragraph_ids": ["P002", "P003"],
                "explanation": "范围过大。",
            }],
        )],
        "cross_segment_review": _cross_review(),
        "decision": "patch",
        "summary": "尝试改写多个段落。",
        "patches": [{
            "start_paragraph_id": "P002",
            "end_paragraph_id": "P003",
            "replacement": "重新创作的内容。",
        }],
    })

    with pytest.raises(ValueError, match="affects too much prose"):
        _check(gateway, "第一段。\n\n第二段。\n\n第三段。\n\n第四段。\n\n第五段。")


def test_reject_blocks_prose_only_repair():
    gateway = _SpyGateway({
        "segment_reviews": [_segment_review(["P001"])],
        "cross_segment_review": _cross_review(
            verdict="issue",
            issues=[{
                "issue_type": "contract_violation",
                "paragraph_ids": [],
                "explanation": "Plan与TR冲突。",
            }],
        ),
        "decision": "reject",
        "summary": "Plan本身颠倒了付款方向。",
        "patches": [],
    })

    with pytest.raises(NarrativeConsistencyRejectedError, match="Plan本身"):
        _check(gateway, "埃利奥等待你的回应。")


def test_prompt_forbids_creative_and_editorial_rewriting():
    prompt = Path(
        "app/prompts/generation/narrative_consistency_check_v1.md"
    ).read_text(encoding="utf-8")

    assert "你不是编辑、润色者、共同作者、剧情规划者或安全审查者" in prompt
    assert "创造性内容是文字冒险的必要组成部分" in prompt
    assert "把明确进展改回未知" in prompt
    assert "重新规划后续剧情" in prompt
    assert "执行简明的三方核对思维链" in prompt
    assert "对照 Player Input" in prompt
    assert "对照 TR" in prompt
    assert "参数无损穿透" in prompt
    assert "判定结果锚定" in prompt
    assert "上下文不相容" in prompt
    assert "基础物理与时空逻辑" in prompt
    assert "知识隔离边界" in prompt
    assert "行动所有权与蔓延" in prompt
    assert "不得新增任何玩家动作、选择、同意、拒绝、承诺或参数" in prompt


def test_segment_review_schema_requires_audit_without_punctuation_pattern():
    schema = NarrativeConsistencyReview.model_json_schema()
    segment_schema = schema["$defs"]["SegmentReview"]

    assert "audit_reasoning" in segment_schema["required"]
    assert "factual_summary" not in segment_schema["properties"]
    assert list(segment_schema["properties"])[:4] == [
        "segment_id",
        "paragraph_ids",
        "audit_reasoning",
        "verdict",
    ]
    assert "pattern" not in segment_schema["properties"]["audit_reasoning"]

    review = _segment_review(["P001"])
    review["audit_reasoning"] = (
        "正文事实：正文没有变化。PLAYER_INPUT：不涉及。"
        "TR：不涉及。玩家决定：无新增。"
    )
    NarrativeConsistencyReview.model_validate({
        "segment_reviews": [review],
        "cross_segment_review": _cross_review(),
        "decision": "pass",
        "summary": "通过。",
        "patches": [],
    })


def test_segmentation_keeps_paragraphs_atomic_and_rebalances_short_tail():
    paragraphs = [f"第{index}段" + "字" * 196 for index in range(1, 7)]

    segments = _segment_paragraphs(paragraphs)

    assert [segment.paragraph_ids for segment in segments] == [
        ["P001", "P002", "P003", "P004"],
        ["P005", "P006"],
    ]
    assert [paragraph for segment in segments for paragraph in segment.paragraphs] == paragraphs


def test_runtime_schema_requires_every_segment_once_and_in_order():
    paragraphs = [f"第{index}段" + "字" * 196 for index in range(1, 7)]
    scene_text = "\n\n".join(paragraphs)
    gateway = _SpyGateway({
        "segment_reviews": [_segment_review(["P001", "P002", "P003", "P004"])],
        "cross_segment_review": _cross_review(),
        "decision": "pass",
        "summary": "遗漏了第二个检查段。",
        "patches": [],
    })

    with pytest.raises(ValueError, match="cover every segment exactly once"):
        _check(gateway, scene_text)


def test_generation_node_promotes_checked_text_to_scene_result(monkeypatch):
    original = _composition("错误正文。")
    review = NarrativeConsistencyReview(
        segment_reviews=[_segment_review(
            ["P001"],
            verdict="issue",
            issues=[{
                "issue_type": "logic_contradiction",
                "paragraph_ids": ["P001"],
                "explanation": "正文与语义合同冲突。",
            }],
        )],
        cross_segment_review=_cross_review(),
        decision="patch",
        summary="修复明确错误。",
        patches=[{
            "start_paragraph_id": "P001",
            "end_paragraph_id": "P001",
            "replacement": "正确正文。",
        }],
    )

    class _FakeAgent:
        def __init__(self, _gateway):
            pass

        def check(self, **_kwargs):
            return NarrativeConsistencyOutcome(
                review=review,
                scene_text="正确正文。",
                changed_paragraph_ids=["P001"],
            )

    monkeypatch.setattr(generation_nodes, "NarrativeConsistencyAgent", _FakeAgent)
    monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())

    result = generation_nodes.check_narrative_consistency_node({
        "user_input": "行动",
        "current_state": _current_state(),
        "turn_resolution": _turn_resolution(),
        "context_facts": ContextFactSet(turn_focus="行动", facts=[]),
        "recent_scene": None,
        "narrative_composition": original,
        "trace_id": "trace-1",
    })

    assert result["narrative_composition"].scene_text == "正确正文。"
    assert result["scene_result"].scene_text == "正确正文。"
    assert result["events"][0]["step"] == "narrative_checked"
    assert result["events"][0]["data"]["changed_paragraph_ids"] == ["P001"]
