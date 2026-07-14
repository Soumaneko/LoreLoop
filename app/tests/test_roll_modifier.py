import json

import pytest

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.current_state import CurrentState, InventoryItem, PlayerState, ResourcesState, SceneState
from app.domain.roll.schemas import RollResultBatch
from app.domain.roll_modifier.evaluator import RollModifierEvaluator
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.graph import generation_nodes


def _make_action(action_id: str, likely_needs_roll: bool) -> AtomicAction:
    return AtomicAction(
        action_id=action_id,
        sequence=1,
        actor=["卢西安"],
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

    def test_generation_skips_modifier_llm_when_no_action_requires_roll(self, monkeypatch):
        parse_result = ActionParseResult(
            turn_intent="观察庭院",
            atomic_actions=[_make_action("a1", False)],
            anchors=[],
            ambiguities=[],
        )

        class _UnexpectedEvaluator:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("modifier evaluator must not be created")

        monkeypatch.setattr(
            generation_nodes,
            "RollModifierEvaluator",
            _UnexpectedEvaluator,
        )

        result = generation_nodes.evaluate_roll_modifiers_node({
            "action_parse_result": parse_result,
            "current_state": _make_state(),
            "core_memory": _make_core_memory(),
            "difficulty": "困难",
            "trace_id": "trace_no_roll",
        })

        assert result["modifier_batch"].modifiers == []
        assert result["roll_results"].total_rolls == 0
        assert [event["step"] for event in result["events"]] == ["rolls_skipped"]

    def test_generation_runs_turn_resolution_llm_without_rolls(self, monkeypatch):
        calls = []

        class _ResolutionAgent:
            def __init__(self, _gateway):
                pass

            def resolve(self, **kwargs):
                calls.append(kwargs)
                return TurnResolutionResult(action_resolutions=[
                    ActionResolutionFrame(
                        action_id="a1",
                        sequence=1,
                        actor=["卢西安"],
                        action_type="observe",
                        outcome_band="success",
                        effect_reasoning="无需随机判定。",
                        resolved_fact="卢西安完成观察。",
                    )
                ])

        monkeypatch.setattr(generation_nodes, "TurnResolutionAgent", _ResolutionAgent)
        monkeypatch.setattr(generation_nodes, "gateway_for", lambda _state: object())
        parse_result = ActionParseResult(
            turn_intent="观察庭院",
            atomic_actions=[_make_action("a1", False)],
        )

        result = generation_nodes.resolve_turn_node({
            "user_input": "观察庭院",
            "current_state": _make_state(),
            "action_parse_result": parse_result,
            "roll_results": RollResultBatch(
                dice_formula="0d20",
                dice_sides=20,
                total_rolls=0,
                results=[],
            ),
            "core_memory": _make_core_memory(),
            "context_facts": ContextFactSet(turn_focus="观察庭院", facts=[]),
            "trace_id": "trace_no_roll",
        })

        assert calls
        assert result["turn_resolution"].total_resolutions == 1
        assert [event["step"] for event in result["events"]] == [
            "turn_resolution_done"
        ]

    def test_trims_excess_modifiers_to_top_two(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "modifiers": [
                        {"action_id": "a1", "label": "一", "reason": "原因一", "value": 3},
                        {"action_id": "a1", "label": "二", "reason": "原因二", "value": 3},
                        {"action_id": "a1", "label": "三", "reason": "原因三", "value": 1},
                    ]
                },
                ensure_ascii=False,
            )
        ])

        batch = RollModifierEvaluator(mock.build_gateway()).evaluate(
            _make_parse_result(),
            _make_state(),
            _make_core_memory(),
            roll_actions=[_make_action("a1", True)],
        )
        assert len(batch.modifiers) == 2
        labels = {m.label for m in batch.modifiers}
        assert labels == {"一", "二"}

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
