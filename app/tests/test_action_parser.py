import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import (
    ActionExtractionResult,
    ActionParseResult,
    ActionRetrievalTopic,
    AtomicAction,
)
from app.domain.action.self_check import ActionSelfCheck
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState


def test_prompt_requires_complete_explicit_action_coverage():
    prompt = Path("app/prompts/generation/action_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "覆盖审计" in prompt
    assert "A、B、C 都必须被 atomic_actions 覆盖" in prompt
    assert "擅自推迟到下一回合" in prompt
    assert "完成型命令必须完整提取" in prompt
    assert "接取任务并快速完成" in prompt
    assert "不得只提取接取、准备、前往或讨论等前置步骤" in prompt
    assert "行动者姓名列表" in prompt
    assert "多人共同执行同一个原子行动" in prompt
    assert "仅仅在场、同行、旁观、受益、被保护或作为 target" in prompt
    assert "retrieval_topics 必须是检索词，不是剧情总结" in prompt
    assert "允许根据 CurrentState 或 CoreMemory 使用更稳定的规范名" in prompt


def test_action_parser_returns_retrieval_topics():
    parser = _make_parser([
        json.dumps({
            "atomic_actions": [{
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "hide_or_stealth",
                "raw_text": "埋伏在山脚下的必经之路",
                "verb": "埋伏",
                "target": "山脚下的必经之路",
                "method": None,
                "condition": None,
                "likely_needs_roll": True,
                "risk_level": "medium",
            }],
            "retrieval_topics": [{
                "text": "埋伏在山脚下",
            }],
        }, ensure_ascii=False)
    ])

    result = parser.parse(
        "我埋伏在山脚下的必经之路。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert result.retrieval_topics == [ActionRetrievalTopic(
        text="埋伏在山脚下",
    )]


def test_action_parse_contract_omits_removed_metadata_fields():
    extraction_fields = ActionExtractionResult.model_json_schema()["properties"]
    parse_fields = ActionParseResult.model_json_schema()["properties"]
    action_fields = AtomicAction.model_json_schema()["properties"]
    anchor_fields = ActionParseResult.model_json_schema()["$defs"]["Anchor"]["properties"]

    assert "turn_intent" not in extraction_fields
    assert "turn_intent" not in parse_fields
    assert "source_action_ids" not in ActionRetrievalTopic.model_json_schema()[
        "properties"
    ]
    assert list(extraction_fields)[:2] == ["analysis", "retrieval_topics"]
    assert list(parse_fields)[:2] == ["analysis", "retrieval_topics"]
    assert "goal" not in action_fields
    assert "roll_hint" not in action_fields
    assert "confidence" not in action_fields
    assert "action_ids" not in anchor_fields


def _make_current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_010",
            location="码头仓库区",
            participants=["主角", "伊莱娜", "马洛"],
        ),
        player=PlayerState(name="主角"),
        resources=ResourcesState(),
    )


def _core_memory() -> CoreMemory:
    return CoreMemory(items=[])


def _make_parser(responses: list[str]) -> ActionParser:
    mock = MockLLM()
    mock.set_responses(responses)
    return ActionParser(mock.build_gateway())


def _build_response(actions: list[dict]) -> str:
    return json.dumps({
        "atomic_actions": actions,
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
                        "actor": ["主角"],
                        "action_type": "observe",
                        "raw_text": "仔细观察仓库门口有没有守卫",
                        "verb": "观察",
                        "target": "仓库门口",
                        "method": None,
                        "condition": None,
                        "likely_needs_roll": True,
                        "risk_level": "medium",
                    }
                ]
            )
        ])

        result = parser.parse("我仔细观察仓库门口有没有守卫。", _make_current_state(), core_memory=_core_memory())
        assert isinstance(result, ActionExtractionResult)
        assert len(result.atomic_actions) == 1
        a = result.atomic_actions[0]
        assert a.action_type == "observe"
        assert a.likely_needs_roll is True
        assert a.risk_level == "medium"
        assert a.target == "仓库门口"


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
                        "actor": ["主角"],
                        "action_type": "move",
                        "raw_text": "先绕到仓库侧面",
                        "verb": "绕",
                        "target": "仓库侧面",
                        "method": None,
                        "condition": None,
                        "likely_needs_roll": False,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": ["主角"],
                        "action_type": "inspect_object",
                        "raw_text": "检查窗户有没有锁",
                        "verb": "检查",
                        "target": "窗户",
                        "method": None,
                        "condition": None,
                        "likely_needs_roll": True,
                    },
                ]
            )
        ])

        result = parser.parse("我先绕到仓库侧面，再检查窗户有没有锁。", _make_current_state(), core_memory=_core_memory())
        assert len(result.atomic_actions) == 2
        assert result.atomic_actions[0].action_type == "move"
        assert result.atomic_actions[0].sequence == 1
        assert result.atomic_actions[1].action_type == "inspect_object"
        assert result.atomic_actions[1].sequence == 2


# ══════════════════════════════════════════════════════════════════
#  Test: 旧事引用
# ══════════════════════════════════════════════════════════════════

class TestPastReference:
    def test_past_reference_action(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": ["主角"],
                        "action_type": "signal_ally",
                        "raw_text": "用之前和伊莱娜约好的暗号提醒她别说话",
                        "verb": "提醒",
                        "target": "伊莱娜",
                        "method": "使用之前约好的暗号",
                        "condition": None,
                        "likely_needs_roll": False,
                    }
                ]
            )
        ])

        result = parser.parse("我用之前和伊莱娜约好的暗号提醒她别说话。", _make_current_state(), core_memory=_core_memory())
        assert len(result.atomic_actions) == 1
        assert result.atomic_actions[0].action_type == "signal_ally"

        assert result.atomic_actions[0].method == "使用之前约好的暗号"

    def test_no_memory_need_in_output(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": ["主角"],
                        "action_type": "signal_ally",
                        "raw_text": "用之前和伊莱娜约好的暗号提醒她别说话",
                        "verb": "提醒",
                        "target": "伊莱娜",
                        "method": "使用之前约好的暗号",
                        "condition": None,
                        "likely_needs_roll": False,
                    }
                ]
            )
        ])

        result = parser.parse("我用之前和伊莱娜约好的暗号提醒她别说话。", _make_current_state(), core_memory=_core_memory())
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
                        "actor": ["主角"],
                        "action_type": "hide_or_stealth",
                        "raw_text": "如果守卫靠近，我就假装喝醉",
                        "verb": "假装",
                        "target": "守卫",
                        "method": "假装喝醉",
                        "condition": "守卫靠近",
                        "likely_needs_roll": True,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": ["主角"],
                        "action_type": "move",
                        "raw_text": "如果没人注意，我继续往仓库门口走",
                        "verb": "走",
                        "target": "仓库门口",
                        "method": "趁没人注意",
                        "condition": "没人注意",
                        "likely_needs_roll": True,
                    },
                ]
            )
        ])

        result = parser.parse("如果守卫靠近，我就假装喝醉；如果没人注意，我继续往仓库门口走。", _make_current_state(), core_memory=_core_memory())
        assert len(result.atomic_actions) == 2
        assert result.atomic_actions[0].condition == "守卫靠近"
        assert result.atomic_actions[1].condition == "没人注意"



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
                        "actor": ["主角"],
                        "action_type": "instruct_ally",
                        "raw_text": "我让马洛守在巷口，如果看到黑帆帮的人就吹哨",
                        "verb": "让",
                        "target": "马洛",
                        "method": "守在巷口，看到黑帆帮的人就吹哨",
                        "condition": None,
                        "likely_needs_roll": False,
                    }
                ]
            )
        ])

        result = parser.parse("我让马洛守在巷口，如果看到黑帆帮的人就吹哨。", _make_current_state(), core_memory=_core_memory())
        assert len(result.atomic_actions) == 1
        a = result.atomic_actions[0]
        assert a.action_type == "instruct_ally"
        assert a.target == "马洛"



# ══════════════════════════════════════════════════════════════════
#  Test: 复杂组合
# ══════════════════════════════════════════════════════════════════

class TestComplexAction:
    def test_multi_subtype_actions_with_past_reference(self):
        parser = _make_parser([
            _build_response(
                actions=[
                    {
                        "action_id": "A1",
                        "sequence": 1,
                        "actor": ["主角"],
                        "action_type": "hide_or_stealth",
                        "raw_text": "假装没看见守卫，低头整理斗篷，从码头边绕过去",
                        "verb": "绕",
                        "target": "码头",
                        "method": "假装没看见守卫，低头整理斗篷",
                        "condition": None,
                        "likely_needs_roll": True,
                    },
                    {
                        "action_id": "A2",
                        "sequence": 2,
                        "actor": ["主角"],
                        "action_type": "observe",
                        "raw_text": "观察门口有没有黑帆帮的人",
                        "verb": "观察",
                        "target": "门口",
                        "method": None,
                        "condition": None,
                        "likely_needs_roll": True,
                    },
                    {
                        "action_id": "A3",
                        "sequence": 3,
                        "actor": ["主角"],
                        "action_type": "inspect_object",
                        "raw_text": "看看门锁是不是和旧城区见过的那种一样",
                        "verb": "看看",
                        "target": "门锁",
                        "method": "与旧城区见过的锁进行比较",
                        "condition": None,
                        "likely_needs_roll": True,
                    },
                ]
            )
        ])

        scene_text = "我假装没看见守卫，低头整理斗篷，从码头边绕过去。靠近仓库后，我观察门口有没有黑帆帮的人，再看看门锁是不是和旧城区见过的那种一样。"
        result = parser.parse(scene_text, _make_current_state(), core_memory=_core_memory())
        assert len(result.atomic_actions) >= 3
        subtypes = {a.action_type for a in result.atomic_actions}
        assert "hide_or_stealth" in subtypes
        assert "observe" in subtypes
        assert "inspect_object" in subtypes



# ══════════════════════════════════════════════════════════════════
#  Test: SelfCheck
# ══════════════════════════════════════════════════════════════════

class TestActionSelfCheck:
    def test_retrieval_topics_are_only_trimmed_and_deduplicated(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[AtomicAction_model(
                action_id="A1",
                sequence=1,
                actor=["主角"],
                action_type="hide_or_stealth",
                raw_text="埋伏在山脚下的必经之路",
                verb="埋伏",
                target="山脚下的必经之路",
                likely_needs_roll=True,
            )],
            retrieval_topics=[
                ActionRetrievalTopic(
                    text="埋伏在山脚下",
                ),
                ActionRetrievalTopic(
                    text="埋伏在山脚下",
                ),
                ActionRetrievalTopic(
                    text="山脚埋伏",
                ),
                ActionRetrievalTopic(
                    text="陷空山",
                ),
            ],
        )

        cleaned = sc.check(
            result,
            "我埋伏在山脚下的必经之路。",
            _make_current_state(),
        )

        assert cleaned.retrieval_topics == [
            ActionRetrievalTopic(
                text="埋伏在山脚下",
            ),
            ActionRetrievalTopic(
                text="山脚埋伏",
            ),
            ActionRetrievalTopic(
                text="陷空山",
            ),
        ]
        assert sc.last_result is not None
        assert sc.last_result.original_topic_count == 4
        assert sc.last_result.final_topic_count == 3

    def test_joint_actor_list_is_deduplicated_without_losing_ownership(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1",
                    sequence=1,
                    actor=["主角", "希尔达", "主角"],
                    action_type="interact_object",
                    raw_text="我和希尔达一起推门",
                    verb="推门",
                    target="石门",
                    likely_needs_roll=True,
                )
            ],
        )

        cleaned = sc.check(result, "我和希尔达一起推门", _make_current_state())

        assert cleaned.atomic_actions[0].actor == ["主角", "希尔达"]

    def test_auto_fill_actor_from_viewpoint(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor=[""], action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True,
                )
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "观察四周", _make_current_state())
        assert cleaned.atomic_actions[0].actor == ["主角"]

    def test_fix_sequence(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=5, actor=["主角"], action_type="move",
                    raw_text="绕到侧面", verb="绕", target="侧面",
                    likely_needs_roll=False,
                ),
                AtomicAction_model(
                    action_id="A2", sequence=3, actor=["主角"], action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True,
                ),
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "绕到侧面观察四周", _make_current_state())
        assert cleaned.atomic_actions[0].sequence == 1
        assert cleaned.atomic_actions[1].sequence == 2

    def test_dedup_anchors(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor=["主角"], action_type="observe",
                    raw_text="观察守卫", verb="观察", target="守卫",
                    likely_needs_roll=True,
                ),
            ],
            anchors=[
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "confidence": 1.0},
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "confidence": 1.0},
            ],
        )
        cleaned = sc.check(result, "观察守卫", _make_current_state())
        assert len(cleaned.anchors) == 2
        texts = {a.text for a in cleaned.anchors}
        assert "守卫" in texts
        assert "主角" in texts

    def test_remove_empty_anchor(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor=["主角"], action_type="observe",
                    raw_text="观察守卫", verb="观察", target="守卫",
                    likely_needs_roll=True,
                ),
            ],
            anchors=[
                {"text": "", "normalized": "", "kind": "character", "source": "explicit", "confidence": 1.0},
                {"text": "守卫", "normalized": "守卫", "kind": "character", "source": "explicit", "confidence": 1.0},
            ],
        )
        cleaned = sc.check(result, "观察守卫", _make_current_state())
        assert len(cleaned.anchors) == 2
        texts = {a.text for a in cleaned.anchors}
        assert "守卫" in texts
        assert "主角" in texts

    def test_duplicate_action_id_fixed(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor=["主角"], action_type="move",
                    raw_text="绕到侧面", verb="绕", target="侧面",
                    likely_needs_roll=False,
                ),
                AtomicAction_model(
                    action_id="A1", sequence=2, actor=["主角"], action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True,
                ),
            ],
            anchors=[],
        )
        cleaned = sc.check(result, "绕到侧面观察四周", _make_current_state())
        assert len(cleaned.atomic_actions) == 2
        ids = {a.action_id for a in cleaned.atomic_actions}
        assert len(ids) == 2  # first keeps "A1", second renamed to "A1_1"

    def test_duplicate_action_id_keeps_anchors(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[
                AtomicAction_model(
                    action_id="A1", sequence=1, actor=["主角"], action_type="move",
                    raw_text="绕到侧面", verb="绕", target="侧面",
                    likely_needs_roll=False,
                ),
                AtomicAction_model(
                    action_id="A1", sequence=2, actor=["主角"], action_type="observe",
                    raw_text="观察四周", verb="观察", target="四周",
                    likely_needs_roll=True,
                ),
            ],
            anchors=[
                {
                    "text": "四周",
                    "normalized": "四周",
                    "kind": "location",
                    "source": "explicit",
                    "confidence": 1.0,
                },
            ],
        )

        cleaned = sc.check(result, "绕到侧面观察四周", _make_current_state())
        anchor = next(a for a in cleaned.anchors if a.text == "四周")

        assert anchor.normalized == "四周"

    def test_empty_actions_warns(self):
        sc = ActionSelfCheck()
        result = ActionParseResult(
            atomic_actions=[],
            anchors=[],
        )
        cleaned = sc.check(result, "nothing", _make_current_state())
        assert cleaned.atomic_actions == []


def AtomicAction_model(**kwargs):
    from app.domain.action.schemas import AtomicAction
    return AtomicAction(**kwargs)


# ══════════════════════════════════════════════════════════════════
#  Failure policy: salvage, no-effect, and hidden-event filtering
# ══════════════════════════════════════════════════════════════════

def test_action_parser_provider_failure_falls_back_to_empty():
    from app.core.llm import MockLLM

    mock = MockLLM()
    mock.set_fail_provider(True)
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我拔出剑。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert result.atomic_actions == []
    assert result.retrieval_topics == []
    assert [warning.code for warning in parser.last_warnings] == [
        "action_extract_fallback"
    ]


def test_action_parser_blank_user_input_is_no_effect():
    parser = ActionParser(object())
    result = parser.parse(
        "   \n  ",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert result.atomic_actions == []
    assert parser.last_warnings == []


def test_action_parser_first_invalid_retries_and_second_valid_subset_wins():
    first = json.dumps({
        "analysis": "第一次解析",
        "atomic_actions": [
            {
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "observe",
                "raw_text": "观察四周",
                "verb": "观察",
                "likely_needs_roll": False,
            },
            {
                "action_id": "  ",
                "sequence": 2,
                "actor": ["主角"],
                "action_type": "attack",
                "raw_text": "挥剑",
                "verb": "挥",
                "likely_needs_roll": True,
            },
        ],
    }, ensure_ascii=False)
    second = _build_response([{
        "action_id": "A1",
        "sequence": 1,
        "actor": ["主角"],
        "action_type": "observe",
        "raw_text": "观察四周",
        "verb": "观察",
        "likely_needs_roll": False,
    }])

    parser = _make_parser([first, second])
    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert parser.last_warnings == []


def test_action_parser_second_invalid_reuses_first_valid_subset():
    first = json.dumps({
        "atomic_actions": [
            {
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "observe",
                "raw_text": "观察四周",
                "verb": "观察",
                "likely_needs_roll": False,
            },
            {
                "action_id": "A2",
                "sequence": 2,
                "actor": ["主角"],
                "action_type": "attack",
                "raw_text": "挥剑",
                "verb": "",
                "likely_needs_roll": True,
            },
        ],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "B1",
            "sequence": 1,
            "actor": [],
            "action_type": "speak",
            "raw_text": "说话",
            "verb": "",
            "likely_needs_roll": False,
        }],
    }, ensure_ascii=False)

    parser = _make_parser([first, second])
    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert any(
        warning.code == "action_dropped"
        for warning in parser.last_warnings
    )


def test_action_parser_filters_unknown_touched_event_id_after_retry():
    from types import SimpleNamespace

    visible_event = SimpleNamespace(
        event_id="evt_visible",
        role="arc",
        title="商路封锁",
        subject="河务署",
        intent="控制渡河通道",
        hook_pool=[],
    )
    first = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "touched_event_ids": ["unknown_event"],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "touched_event_ids": ["evt_visible", "unknown_event_2"],
    }, ensure_ascii=False)

    parser = _make_parser([first, second])
    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
        events=[visible_event],
    )

    assert result.touched_event_ids == ["evt_visible"]
    assert any(
        warning.code == "action_dropped"
        for warning in parser.last_warnings
    )


def test_action_parser_reuse_first_preserves_second_dropped_warnings():
    from app.core.llm import MockLLM

    first = json.dumps({
        "atomic_actions": [
            {
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "observe",
                "raw_text": "观察四周",
                "verb": "观察",
                "likely_needs_roll": False,
            },
            {
                "action_id": "A2",
                "sequence": 2,
                "actor": ["主角"],
                "action_type": "attack",
                "raw_text": "挥剑",
                "verb": "",
                "likely_needs_roll": True,
            },
        ],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "A2",
            "sequence": 2,
            "actor": ["主角"],
            "action_type": "attack",
            "raw_text": "挥剑",
            "verb": "",
            "likely_needs_roll": True,
        }],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert any(
        warning.code == "action_dropped"
        for warning in parser.last_warnings
    )


def test_action_parser_rejects_non_string_actor_and_retries():
    from app.core.llm import MockLLM

    first = json.dumps({
        "atomic_actions": [{
            "action_id": "A_bad",
            "sequence": 1,
            "actor": [123, "主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "A_good",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A_good"]


def test_action_parser_reports_retrieval_topic_count_dropped_on_salvage():
    from app.core.llm import MockLLM

    topics = [{"text": f"topic_{index}"} for index in range(9)]
    response = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "retrieval_topics": topics,
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([response, response])
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert len(result.retrieval_topics) == 8
    assert any(
        warning.code == "action_dropped"
        and "keeping first 8" in warning.message
        for warning in parser.last_warnings
    )


def test_action_parser_salvage_prefers_first_actions_over_second_topics():
    from app.core.llm import MockLLM

    first = json.dumps({
        "atomic_actions": [
            {
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "observe",
                "raw_text": "观察四周",
                "verb": "观察",
                "likely_needs_roll": False,
            },
            {
                "action_id": "A2",
                "sequence": 2,
                "actor": ["主角"],
                "action_type": "attack",
                "raw_text": "挥剑",
                "verb": "",
                "likely_needs_roll": True,
            },
        ],
        "retrieval_topics": [],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "B1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "speak",
            "raw_text": "说话",
            "verb": "",
            "likely_needs_roll": False,
        }],
        "retrieval_topics": [{"text": "第二次合法检索词"}],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert result.retrieval_topics == []
