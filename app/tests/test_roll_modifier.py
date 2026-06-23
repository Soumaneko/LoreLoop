import json

import pytest

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import CurrentState, InventoryItem, PlayerState, ResourcesState, SceneState
from app.domain.roll_modifier.evaluator import RollModifierEvaluator
from app.workflows.evaluate_roll_modifiers import evaluate_roll_modifiers_workflow


def _make_action(action_id: str, likely_needs_roll: bool) -> AtomicAction:
    return AtomicAction(
        action_id=action_id,
        sequence=1,
        actor="卢西安",
        action_type="observe",
        raw_text="观察",
        verb="观察",
        target="庭院",
        likely_needs_roll=likely_needs_roll,
        confidence=0.9,
    )


def _make_parse_result() -> ActionParseResult:
    return ActionParseResult(
        turn_intent="观察庭院",
        atomic_actions=[
            _make_action("a1", True),
            _make_action("a2", False),
        ],
        anchors=[],
        ambiguities=[],
    )


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡庭院"),
        player=PlayerState(name="卢西安"),
        resources=ResourcesState(inventory=[InventoryItem(name="旧地图")]),
    )


def _make_core_memory() -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(
                id="core_world",
                category="world",
                content="当前世界是经典旧世时代的艾泽拉斯。",
                priority=100,
            )
        ]
    )


class TestRollModifierEvaluator:
    def test_evaluator_adds_situational_source_and_filters_to_roll_actions(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "modifiers": [
                        {
                            "action_id": "a2",
                            "label": "旧地图",
                            "reason": "旧地图能帮助卢西安判断庭院路线。",
                            "value": 2,
                        },
                        {
                            "action_id": "missing",
                            "label": "不存在",
                            "reason": "这个 action 不在实际掷骰列表中。",
                            "value": 2,
                        },
                    ]
                },
                ensure_ascii=False,
            )
        ])

        result = RollModifierEvaluator(mock.build_gateway()).evaluate(
            _make_parse_result(),
            _make_state(),
            _make_core_memory(),
            roll_actions=[_make_action("a2", False)],
        )

        assert len(result.modifiers) == 1
        assert result.modifiers[0].action_id == "a2"
        assert result.modifiers[0].source == "situational"

    def test_workflow_uses_evaluator(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "modifiers": [
                        {
                            "action_id": "a1",
                            "label": "清晰视野",
                            "reason": "庭院开阔，能帮助观察。",
                            "value": 1,
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])

        result = evaluate_roll_modifiers_workflow(
            _make_parse_result(),
            _make_state(),
            mock.build_gateway(),
            core_memory=_make_core_memory(),
            roll_actions=[_make_action("a1", True)],
        )

        assert result.modifiers[0].value == 1

    def test_rejects_too_many_modifiers_for_one_action(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "modifiers": [
                        {"action_id": "a1", "label": "一", "reason": "原因一", "value": 3},
                        {"action_id": "a1", "label": "二", "reason": "原因二", "value": 3},
                        {"action_id": "a1", "label": "三", "reason": "原因三", "value": 3},
                    ]
                },
                ensure_ascii=False,
            )
        ])

        with pytest.raises(ValueError):
            RollModifierEvaluator(mock.build_gateway()).evaluate(
                _make_parse_result(),
                _make_state(),
                _make_core_memory(),
                roll_actions=[_make_action("a1", True)],
            )

    def test_rejects_total_modifier_over_eight(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "modifiers": [
                        {"action_id": "a1", "label": "一", "reason": "原因一", "value": 5},
                        {"action_id": "a1", "label": "二", "reason": "原因二", "value": 4},
                    ]
                },
                ensure_ascii=False,
            )
        ])

        with pytest.raises(ValueError):
            RollModifierEvaluator(mock.build_gateway()).evaluate(
                _make_parse_result(),
                _make_state(),
                _make_core_memory(),
                roll_actions=[_make_action("a1", True)],
            )
