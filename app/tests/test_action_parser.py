import json

import pytest

from app.core.llm import MockLLM
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionParseResult
from app.domain.action.self_check import ActionSelfCheck
from app.workflows.parse_action import parse_action_workflow

SCENE_CONTEXT = {
    "scene_id": "scene_010",
    "location": "码头仓库区",
    "time_label": "第4日深夜",
    "participants": ["主角", "伊莱娜", "马洛"],
    "viewpoint": "主角",
}


def _make_parser(responses: list[str]) -> ActionParser:
    mock = MockLLM()
    mock.set_responses(responses)
    return ActionParser(mock.build_gateway())


def _build_response(actions: list[dict], anchors: list[dict], *, turn_intent: str = "测试意图", ambiguities: list[str] | None = None) -> str:
    return json.dumps({
        "turn_intent": turn_intent,
        "atomic_actions": actions,
        "anchors": anchors,
        "ambiguities": ambiguities or [],
    }, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  Test: 简单单动作
# ══════════════════════════════════════════════════════════════════

class TestSimpleSingleAction:
    def test_observe_action(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "observe",
                        "raw_text": "仔细观察仓库门口有没有守卫",
                        "verb": "观察",
                        "target": "仓库门口",
                        "method": None,
                        "goal": "确认是否有守卫",
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "perception",
                        "confidence": 0.95,
                    }
                ],
                anchors=[
                    {"text": "仓库门口", "normalized": "仓库门口", "kind": "location", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                ],
            )
        ])

        result = parser.parse("我仔细观察仓库门口有没有守卫。", SCENE_CONTEXT)
        assert isinstance(result, ActionParseResult)
        assert len(result.atomic_actions) == 1
        a = result.atomic_actions[0]
        assert a.action_type == "observe"
        assert a.likely_needs_roll is True
        assert a.target == "仓库门口"
        assert len(result.anchors) >= 2
        anchor_texts = {anchor.text for anchor in result.anchors}
        assert "仓库门口" in anchor_texts
        assert "守卫" in anchor_texts


# ══════════════════════════════════════════════════════════════════
#  Test: 多动作顺序
# ══════════════════════════════════════════════════════════════════

class TestMultiAction:
    def test_move_then_inspect(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "move",
                        "raw_text": "先绕到仓库侧面",
                        "verb": "绕",
                        "target": "仓库侧面",
                        "method": None,
                        "goal": None,
                        "condition": None,
                        "likely_needs_roll": False,
                        "roll_hint": None,
                        "confidence": 0.95,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": "主角",
                        "action_type": "inspect_object",
                        "raw_text": "检查窗户有没有锁",
                        "verb": "检查",
                        "target": "窗户",
                        "method": None,
                        "goal": "确认是否有锁",
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "investigation",
                        "confidence": 0.9,
                    },
                ],
                anchors=[
                    {"text": "仓库侧面", "normalized": "仓库侧面", "kind": "location", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "窗户", "normalized": "窗户", "kind": "item", "source": "explicit", "action_ids": ["A2"], "confidence": 1.0},
                ],
            )
        ])

        result = parser.parse("我先绕到仓库侧面，再检查窗户有没有锁。", SCENE_CONTEXT)
        assert len(result.atomic_actions) == 2
        assert result.atomic_actions[0].action_type == "move"
        assert result.atomic_actions[0].sequence == 1
        assert result.atomic_actions[1].action_type == "inspect_object"
        assert result.atomic_actions[1].sequence == 2


# ══════════════════════════════════════════════════════════════════
#  Test: 旧事引用
# ══════════════════════════════════════════════════════════════════

class TestPastReference:
    def test_past_reference_anchors(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "signal_ally",
                        "raw_text": "用之前和伊莱娜约好的暗号提醒她别说话",
                        "verb": "提醒",
                        "target": "伊莱娜",
                        "method": "使用之前约好的暗号",
                        "goal": "让她别说话",
                        "condition": None,
                        "likely_needs_roll": False,
                        "roll_hint": None,
                        "confidence": 0.95,
                    }
                ],
                anchors=[
                    {"text": "之前", "normalized": "past_reference", "kind": "past_reference", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "伊莱娜", "normalized": "伊莱娜", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "暗号", "normalized": "暗号", "kind": "concept", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "别说话", "normalized": "保持沉默", "kind": "action_keyword", "source": "explicit", "action_ids": ["A1"], "confidence": 0.95},
                ],
            )
        ])

        result = parser.parse("我用之前和伊莱娜约好的暗号提醒她别说话。", SCENE_CONTEXT)
        assert len(result.atomic_actions) == 1
        assert result.atomic_actions[0].action_type == "signal_ally"

        kinds = {a.kind for a in result.anchors}
        assert "past_reference" in kinds
        assert "character" in kinds
        assert "concept" in kinds

        normalized_vals = {a.normalized for a in result.anchors}
        assert "伊莱娜" in normalized_vals
        assert "暗号" in normalized_vals

    def test_no_memory_need_in_output(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "signal_ally",
                        "raw_text": "用之前和伊莱娜约好的暗号提醒她别说话",
                        "verb": "提醒",
                        "target": "伊莱娜",
                        "method": "使用之前约好的暗号",
                        "goal": "让她别说话",
                        "condition": None,
                        "likely_needs_roll": False,
                        "roll_hint": None,
                        "confidence": 0.95,
                    }
                ],
                anchors=[
                    {"text": "之前", "normalized": "past_reference", "kind": "past_reference", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                ],
            )
        ])

        result = parser.parse("我用之前和伊莱娜约好的暗号提醒她别说话。", SCENE_CONTEXT)
        dumped = result.model_dump_json()
        assert "memory_need" not in dumped


# ══════════════════════════════════════════════════════════════════
#  Test: 条件动作
# ══════════════════════════════════════════════════════════════════

class TestConditionalAction:
    def test_conditional_actions_with_condition_field(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "hide_or_stealth",
                        "raw_text": "如果守卫靠近，我就假装喝醉",
                        "verb": "假装",
                        "target": "守卫",
                        "method": "假装喝醉",
                        "goal": "不被发现",
                        "condition": "守卫靠近",
                        "likely_needs_roll": True,
                        "roll_hint": "deception",
                        "confidence": 0.9,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": "主角",
                        "action_type": "move",
                        "raw_text": "如果没人注意，我继续往仓库门口走",
                        "verb": "走",
                        "target": "仓库门口",
                        "method": "趁没人注意",
                        "goal": None,
                        "condition": "没人注意",
                        "likely_needs_roll": True,
                        "roll_hint": "stealth",
                        "confidence": 0.9,
                    },
                ],
                anchors=[
                    {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "仓库门口", "normalized": "仓库门口", "kind": "location", "source": "explicit", "action_ids": ["A2"], "confidence": 1.0},
                ],
            )
        ])

        result = parser.parse("如果守卫靠近，我就假装喝醉；如果没人注意，我继续往仓库门口走。", SCENE_CONTEXT)
        assert len(result.atomic_actions) == 2
        assert result.atomic_actions[0].condition == "守卫靠近"
        assert result.atomic_actions[1].condition == "没人注意"

        anchor_texts = {a.text for a in result.anchors}
        assert "守卫" in anchor_texts
        assert "仓库门口" in anchor_texts


# ══════════════════════════════════════════════════════════════════
#  Test: 对同伴指令
# ══════════════════════════════════════════════════════════════════

class TestInstructAlly:
    def test_instruct_ally_with_target(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "instruct_ally",
                        "raw_text": "我让马洛守在巷口，如果看到黑帆帮的人就吹哨",
                        "verb": "让",
                        "target": "马洛",
                        "method": "守在巷口，看到黑帆帮的人就吹哨",
                        "goal": "警戒黑帆帮",
                        "condition": None,
                        "likely_needs_roll": False,
                        "roll_hint": None,
                        "confidence": 0.95,
                    }
                ],
                anchors=[
                    {"text": "马洛", "normalized": "马洛", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "巷口", "normalized": "巷口", "kind": "location", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "黑帆帮", "normalized": "黑帆帮", "kind": "organization", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "吹哨", "normalized": "吹哨", "kind": "action_keyword", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                ],
            )
        ])

        result = parser.parse("我让马洛守在巷口，如果看到黑帆帮的人就吹哨。", SCENE_CONTEXT)
        assert len(result.atomic_actions) == 1
        a = result.atomic_actions[0]
        assert a.action_type == "instruct_ally"
        assert a.target == "马洛"

        anchor_texts = {a.text for a in result.anchors}
        assert "马洛" in anchor_texts
        assert "巷口" in anchor_texts
        assert "黑帆帮" in anchor_texts
        assert "吹哨" in anchor_texts


# ══════════════════════════════════════════════════════════════════
#  Test: 复杂组合
# ══════════════════════════════════════════════════════════════════

class TestComplexAction:
    def test_multi_type_actions_with_past_reference(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "主角",
                        "action_type": "hide_or_stealth",
                        "raw_text": "假装没看见守卫，低头整理斗篷，从码头边绕过去",
                        "verb": "绕",
                        "target": "码头",
                        "method": "假装没看见守卫，低头整理斗篷",
                        "goal": "不被发现",
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "stealth",
                        "confidence": 0.9,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": "主角",
                        "action_type": "observe",
                        "raw_text": "观察门口有没有黑帆帮的人",
                        "verb": "观察",
                        "target": "门口",
                        "method": None,
                        "goal": "确认是否有黑帆帮的人",
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "perception",
                        "confidence": 0.9,
                    },
                    {
                        "action_id": "A3",
                        "sequence": 3,
                        "actor": "主角",
                        "action_type": "inspect_object",
                        "raw_text": "看看门锁是不是和旧城区见过的那种一样",
                        "verb": "看看",
                        "target": "门锁",
                        "method": "与旧城区见过的锁进行比较",
                        "goal": "判断门锁是否相似",
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "investigation",
                        "confidence": 0.85,
                    },
                ],
                anchors=[
                    {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "斗篷", "normalized": "斗篷", "kind": "item", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "码头", "normalized": "码头", "kind": "location", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                    {"text": "仓库", "normalized": "仓库", "kind": "location", "source": "explicit", "action_ids": ["A2", "A3"], "confidence": 1.0},
                    {"text": "黑帆帮", "normalized": "黑帆帮", "kind": "organization", "source": "explicit", "action_ids": ["A2"], "confidence": 1.0},
                    {"text": "门锁", "normalized": "门锁", "kind": "item", "source": "explicit", "action_ids": ["A3"], "confidence": 1.0},
                    {"text": "旧城区", "normalized": "旧城区", "kind": "location", "source": "explicit", "action_ids": ["A3"], "confidence": 1.0},
                    {"text": "见过的那种", "normalized": "past_seen_similar_object", "kind": "past_reference", "source": "explicit", "action_ids": ["A3"], "confidence": 0.95},
                ],
            )
        ])

        scene_text = "我假装没看见守卫，低头整理斗篷，从码头边绕过去。靠近仓库后，我观察门口有没有黑帆帮的人，再看看门锁是不是和旧城区见过的那种一样。"
        result = parser.parse(scene_text, SCENE_CONTEXT)
        assert len(result.atomic_actions) >= 3
        types = {a.action_type for a in result.atomic_actions}
        assert "hide_or_stealth" in types
        assert "observe" in types
        assert "inspect_object" in types

        anchor_texts = {a.text for a in result.anchors}
        assert "守卫" in anchor_texts
        assert "斗篷" in anchor_texts
        assert "码头" in anchor_texts
        assert "黑帆帮" in anchor_texts
        assert "门锁" in anchor_texts
        assert "旧城区" in anchor_texts
        assert "见过的那种" in anchor_texts


# ══════════════════════════════════════════════════════════════════
#  Test: SelfCheck
# ══════════════════════════════════════════════════════════════════

class TestActionSelfCheck:
    def test_auto_fill_actor_from_viewpoint(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor="", action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True, confidence=0.9,
                )
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "观察四周", SCENE_CONTEXT)
        assert cleaned.atomic_actions[0].actor == "主角"

    def test_fix_sequence(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=5, actor="主角", action_type="move",
                    raw_text="绕到侧面", verb="绕", target="侧面",
                    likely_needs_roll=False, confidence=0.9,
                ),
                AtomicAction_model(
                    action_id="A2", sequence=3, actor="主角", action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True, confidence=0.9,
                ),
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "绕到侧面观察四周", SCENE_CONTEXT)
        assert cleaned.atomic_actions[0].sequence == 1
        assert cleaned.atomic_actions[1].sequence == 2

    def test_dedup_anchors(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor="主角", action_type="observe",
                    raw_text="观察守卫", verb="观察", target="守卫",
                    likely_needs_roll=True, confidence=0.9,
                ),
            ],
            anchors=[
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
            ],
        )
        cleaned = sc.check(result, "观察守卫", SCENE_CONTEXT)
        assert len(cleaned.anchors) == 1

    def test_remove_invalid_action_id(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor="主角", action_type="observe",
                    raw_text="观察守卫", verb="观察", target="守卫",
                    likely_needs_roll=True, confidence=0.9,
                ),
            ],
            anchors=[
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1", "A99"], "confidence": 1.0},
            ],
        )
        cleaned = sc.check(result, "观察守卫", SCENE_CONTEXT)
        assert len(cleaned.anchors) == 1
        assert cleaned.anchors[0].action_ids == ["A1"]

    def test_remove_empty_anchor(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor="主角", action_type="observe",
                    raw_text="观察守卫", verb="观察", target="守卫",
                    likely_needs_roll=True, confidence=0.9,
                ),
            ],
            anchors=[
                {"text": "", "normalized": "", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
            ],
        )
        cleaned = sc.check(result, "观察守卫", SCENE_CONTEXT)
        assert len(cleaned.anchors) == 1
        assert cleaned.anchors[0].text == "守卫"

    def test_duplicate_action_id_fixed(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor="主角", action_type="move",
                    raw_text="绕到侧面", verb="绕", target="侧面",
                    likely_needs_roll=False, confidence=0.9,
                ),
                AtomicAction_model(
                    action_id="A1", sequence=2, actor="主角", action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True, confidence=0.9,
                ),
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "绕到侧面观察四周", SCENE_CONTEXT)
        assert len(cleaned.atomic_actions) == 2
        ids = {a.action_id for a in cleaned.atomic_actions}
        assert len(ids) == 2  # first keeps "A1", second renamed to "A1_1"

    def test_empty_actions_warns(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            turn_intent="测试",
            atomic_actions=[],
            anchors=[],
        )
        cleaned = sc.check(result, "nothing", SCENE_CONTEXT)
        assert cleaned.atomic_actions == []


# ══════════════════════════════════════════════════════════════════
#  Test: Workflow
# ══════════════════════════════════════════════════════════════════

class TestActionWorkflow:
    def test_workflow_with_self_check(self):
        mock = MockLLM()
        mock.set_responses([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": "",
                        "action_type": "observe",
                        "raw_text": "仔细观察仓库门口",
                        "verb": "观察",
                        "target": "仓库门口",
                        "method": None,
                        "goal": None,
                        "condition": None,
                        "likely_needs_roll": True,
                        "roll_hint": "perception",
                        "confidence": 0.95,
                    }
                ],
                anchors=[
                    {"text": "仓库门口", "normalized": "仓库门口", "kind": "location", "source": "explicit", "action_ids": ["A1"], "confidence": 1.0},
                ],
            )
        ])

        parser = ActionParser(mock.build_gateway())
        sc = ActionSelfCheck()
        result = parse_action_workflow(
            "我仔细观察仓库门口。", SCENE_CONTEXT, parser, sc
        )

        assert len(result.atomic_actions) == 1
        assert result.atomic_actions[0].actor == "主角"


def AtomicAction_model(**kwargs):
    from app.domain.action.schemas import AtomicAction
    return AtomicAction(**kwargs)
