import json
from pathlib import Path

import pytest

from app.core.llm import MockLLM
from app.core.llm.errors import LLMProviderError
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState
from app.domain.roll.schemas import RollResult, RollResultBatch
from app.domain.roll_modifier.schemas import RollModifier
from app.domain.turn_resolution import (
    ActionResolutionFrame,
    TurnResolutionAgent,
    TurnResolutionResult,
    build_action_judgments,
    build_player_turn_resolution,
    map_roll_to_band,
)


def _action(
    action_id: str = "a1",
    action_type: str = "observe",
    risk_level: str | None = None,
) -> AtomicAction:
    return AtomicAction(
        action_id=action_id,
        sequence=1,
        actor=["主角"],
        action_type=action_type,
        raw_text="观察四周",
        verb="观察",
        target="四周",
        likely_needs_roll=True,
        risk_level=risk_level,
    )


def _parse(action: AtomicAction | None = None) -> ActionParseResult:
    return ActionParseResult(
        atomic_actions=[action or _action()],
    )


def _roll(value: int = 20, action_id: str = "a1") -> RollResult:
    return RollResult(
        action_id=action_id,
        sequence=1,
        actor=["主角"],
        action_type="observe",
        dice="1d20",
        value=value,
        dice_sides=20,
        roll_hint="perception",
    )


def _roll_batch(*rolls: RollResult) -> RollResultBatch:
    return RollResultBatch(
        dice_formula=f"{len(rolls)}d20",
        dice_sides=20,
        total_rolls=len(rolls),
        results=list(rolls),
    )


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡"),
        player=PlayerState(name="卢西安"),
        resources=ResourcesState(),
    )


def _response(outcome_band: str = "critical_success") -> str:
    return json.dumps({
        "resolution_reasoning": "本回合只有观察行动，action_judgment 提供给定 outcome_band。CurrentState 支持主角处在当前场景中，因此结果应兑现为发现环境异常。",
        "action_resolutions": [
            {
                "action_id": "a1",
                "effect_reasoning": "支点：当前场景可观察；边界：只发现环境异常和可用优势。",
                "resolved_fact": "主角发现四周最关键的异常，并获得一个可立即利用的优势。",
                "narrative_hook": "异常的来源隐约可辨，主角可以决定是否立即追查。",
            }
        ],
    }, ensure_ascii=False)


class _SpyGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestActionJudgment:
    def test_roll_band_mapping_stays_programmatic(self):
        assert map_roll_to_band(1, 20) == "critical_failure"
        assert map_roll_to_band(20, 20) == "critical_success"
        assert map_roll_to_band(10, 20) == "partial_success"

    def test_build_action_judgments_uses_adjusted_roll_value(self):
        roll = RollResult(
            action_id="a1",
            sequence=1,
            actor=["主角"],
            action_type="observe",
            dice="1d20",
            value=12,
            dice_sides=20,
            raw_value=7,
            modifiers=[
                RollModifier(
                    action_id="a1",
                    label="清晰视野",
                    reason="环境提供明确观察优势。",
                    value=5,
                )
            ],
        )

        judgments = build_action_judgments(_parse(), _roll_batch(roll))

        assert judgments[0].resolution_source == "roll"
        assert judgments[0].roll_value == 12
        assert judgments[0].raw_value == 7
        assert judgments[0].outcome_band == "partial_success"

    def test_build_action_judgments_marks_no_roll_action_automatic_success(self):
        action = _action().model_copy(update={"likely_needs_roll": False})

        judgments = build_action_judgments(_parse(action), _roll_batch())

        assert len(judgments) == 1
        assert judgments[0].resolution_source == "automatic"
        assert judgments[0].outcome_band == "success"
        assert judgments[0].dice is None

    def test_low_risk_move_forced_without_roll_is_automatic_success(self):
        action = _action(action_type="move", risk_level="low")

        judgments = build_action_judgments(_parse(action), _roll_batch())

        assert len(judgments) == 1
        assert judgments[0].resolution_source == "automatic"
        assert judgments[0].outcome_band == "success"
        assert judgments[0].dice is None

    def test_action_resolution_fact_is_length_limited_to_soft_limit(self):
        with pytest.raises(ValueError):
            ActionResolutionFrame(
                action_id="a1",
                sequence=1,
                actor=["主角"],
                action_type="observe",
                outcome_band="success",
                effect_reasoning="支点：当前场景可观察；边界：只给克制发现。",
                resolved_fact="超长事实边界" * 31,
            )

    def test_builds_persistable_player_roll_summary(self):
        roll = RollResult(
            action_id="a1",
            sequence=1,
            actor=["主角"],
            action_type="observe",
            dice="2d20kh",
            value=16,
            raw_value=14,
            all_raw_values=[8, 14],
            dice_sides=20,
            modifiers=[RollModifier(
                action_id="a1",
                label="清晰视野",
                reason="视线良好。",
                value=2,
            )],
        )
        resolution = ActionResolutionFrame(
            action_id="a1",
            sequence=1,
            actor=["主角"],
            action_type="observe",
            outcome_band="success",
            effect_reasoning="观察成功。",
            resolved_fact="发现异常。",
        )

        summary = build_player_turn_resolution(
            _parse(),
            _roll_batch(roll),
            TurnResolutionResult(action_resolutions=[resolution]),
        )

        action = summary.actions[0]
        assert action.action_text == "观察四周"
        assert action.raw_values == [8, 14]
        assert action.modifiers[0].label == "清晰视野"
        assert action.modifiers[0].reason == "视线良好。"
        assert action.modifiers[0].value == 2
        assert action.modifier_total == 2
        assert action.final_value == 16
        assert action.outcome_band == "success"


class TestTurnResolutionAgent:
    def test_prompt_contains_top_level_reasoning_requirements(self):
        prompt = Path("app/prompts/generation/turn_resolution_v1.md").read_text(encoding="utf-8")

        assert "resolution_reasoning" in prompt
        assert "回合判定前的逻辑审计" in prompt
        assert "事实边界是什么" in prompt
        assert "不负责“剧情演出”" in prompt
        assert "客观可能性" in prompt
        assert "本行动的结果依据" in prompt
        assert "resolved_fact" in prompt
        assert "本行动判定后的权威事实结论" in prompt
        assert "不得改成主角陪同" in prompt
        assert "具体的台词内容让下游去写" in prompt

    def test_prompt_input_omits_action_parse_result(self):
        gateway = _SpyGateway(json.loads(_response("critical_success")))

        TurnResolutionAgent(gateway).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
        )

        input_data = gateway.calls[0]["input_data"]
        assert "user_input" in input_data
        assert "action_judgments_json" in input_data
        assert "roll_judgments_json" not in input_data
        assert "event_resolution_context_json" not in input_data
        assert "director_event_json" not in input_data
        assert "action_parse_result_json" not in input_data
        assert "available_types_json" not in input_data
        assert "resolution_reasoning" in input_data["output_schema_json"]
        assert "selected_type" not in input_data["output_schema_json"]
        assert "intended_goal" not in input_data["action_judgments_json"]
        schema = json.loads(input_data["output_schema_json"])
        action_resolution_ref = schema["properties"]["action_resolutions"]["items"]["$ref"]
        action_resolution_def = schema["$defs"][action_resolution_ref.removeprefix("#/$defs/")]
        action_resolution_fields = action_resolution_def["properties"]
        assert set(action_resolution_fields) == {
            "action_id",
            "effect_reasoning",
            "resolved_fact",
            "narrative_hook",
        }

    def test_llm_interprets_consequence(self):
        mock = MockLLM()
        mock.set_responses([_response("critical_success")])

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
        )

        assert "观察行动" in result.resolution_reasoning
        assert result.action_resolutions[0].outcome_band == "critical_success"
        assert result.action_resolutions[0].resolution_source == "roll"
        assert "异常" in result.action_resolutions[0].resolved_fact

    def test_llm_resolves_action_without_roll(self):
        mock = MockLLM()
        mock.set_responses([_response("success")])
        action = _action().model_copy(update={"likely_needs_roll": False})

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(action),
            roll_results=_roll_batch(),
            core_memory=CoreMemory(items=[]),
        )

        assert result.total_resolutions == 1
        assert result.action_resolutions[0].outcome_band == "success"
        assert result.action_resolutions[0].resolution_source == "automatic"

    def test_fills_action_resolution_identity_from_roll_judgment(self):
        mock = MockLLM()
        mock.set_responses([_response("failure")])

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="观察",
            current_state=_state(),
            action_parse_result=_parse(),
            roll_results=_roll_batch(_roll(20)),
            core_memory=CoreMemory(items=[]),
        )

        frame = result.action_resolutions[0]
        assert frame.sequence == 1
        assert frame.actor == ["主角"]
        assert frame.action_type == "observe"
        assert frame.outcome_band == "critical_success"

    def test_joint_actors_are_preserved_through_resolution(self):
        action = _action().model_copy(update={
            "actor": ["主角", "希尔达"],
            "likely_needs_roll": False,
            "raw_text": "我和希尔达一起推开石门",
        })
        mock = MockLLM()
        mock.set_responses([_response("success")])

        result = TurnResolutionAgent(mock.build_gateway()).resolve(
            user_input="我和希尔达一起推开石门",
            current_state=_state(),
            action_parse_result=_parse(action),
            roll_results=_roll_batch(),
            core_memory=CoreMemory(items=[]),
        )

        assert result.action_resolutions[0].actor == ["主角", "希尔达"]


def test_project_turn_resolution_adds_routine_mode():
    from app.domain.turn_resolution.views import project_turn_resolution

    result = TurnResolutionResult(action_resolutions=[
        ActionResolutionFrame(
            action_id="a1",
            sequence=1,
            actor=["主角"],
            action_type="move",
            outcome_band="success",
            resolution_source="automatic",
            effect_reasoning="常规移动。",
            resolved_fact="主角向陷空山行进。",
        ),
        ActionResolutionFrame(
            action_id="a2",
            sequence=2,
            actor=["主角"],
            action_type="observe",
            outcome_band="success",
            resolution_source="roll",
            effect_reasoning="警觉观察。",
            resolved_fact="主角发现远处异动。",
        ),
    ])

    projected = json.loads(project_turn_resolution(result))
    by_id = {
        item["action_id"]: item for item in projected["action_resolutions"]
    }
    assert by_id["a1"]["resolution_source"] == "automatic"
    assert by_id["a1"]["resolution_mode"] == "routine"
    assert by_id["a1"]["outcome_band"] == "success"
    assert by_id["a2"]["resolution_source"] == "roll"
    assert by_id["a2"]["resolution_mode"] == "roll"
    assert by_id["a2"]["outcome_band"] == "success"
    assert "effect_reasoning" not in by_id["a1"]


def _two_action_parse() -> ActionParseResult:
    return ActionParseResult(
        atomic_actions=[
            _action("a1").model_copy(update={"sequence": 1}),
            _action("a2").model_copy(update={"sequence": 2}),
        ],
    )


def _two_roll_batch() -> RollResultBatch:
    return _roll_batch(
        _roll(20, "a1"),
        _roll(5, "a2"),
    )


def _two_action_response(only_a1: bool = False) -> str:
    resolutions = [
        {
            "action_id": "a1",
            "effect_reasoning": "观察成功。",
            "resolved_fact": "主角发现异常。",
            "narrative_hook": "异常的源头留下了可追查的痕迹。",
        },
    ]
    if not only_a1:
        resolutions.append({
            "action_id": "a2",
            "effect_reasoning": "第二次行动按掷骰结果推进。",
            "resolved_fact": "主角承受失败后果。",
            "narrative_hook": "失败的代价迫近，主角需要立即做出取舍。",
        })
    return json.dumps({
        "resolution_reasoning": "两行动按判定推进。",
        "action_resolutions": resolutions,
    }, ensure_ascii=False)


def test_turn_resolution_provider_failure_propagates():
    from app.core.llm import MockLLM
    from app.core.llm.errors import LLMProviderError

    mock = MockLLM()
    mock.set_fail_provider(True)
    agent = TurnResolutionAgent(mock.build_gateway())

    with pytest.raises(LLMProviderError):
        agent.resolve(
            user_input="观察并攻击",
            current_state=_state(),
            action_parse_result=_two_action_parse(),
            roll_results=_two_roll_batch(),
            core_memory=CoreMemory(items=[]),
        )


def test_turn_resolution_first_missing_triggers_retry_second_complete():
    from app.core.llm import MockLLM

    mock = MockLLM()
    mock.set_responses([
        _two_action_response(only_a1=True),
        _two_action_response(),
    ])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察并攻击",
        current_state=_state(),
        action_parse_result=_two_action_parse(),
        roll_results=_two_roll_batch(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.total_resolutions == 2
    assert {frame.action_id for frame in result.action_resolutions} == {
        "a1", "a2"
    }
    assert agent.last_warnings == []


def test_turn_resolution_second_missing_fills_deterministically():
    from app.core.llm import MockLLM

    mock = MockLLM()
    mock.set_responses([
        _two_action_response(only_a1=True),
        _two_action_response(only_a1=True),
    ])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察并攻击",
        current_state=_state(),
        action_parse_result=_two_action_parse(),
        roll_results=_two_roll_batch(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.total_resolutions == 2
    by_id = {frame.action_id: frame for frame in result.action_resolutions}
    assert by_id["a1"].effect_reasoning == "观察成功。"
    assert by_id["a2"].effect_reasoning == "行动按判定推进，未生成详细效果说明。"
    assert any(
        warning.code == "turn_resolution_missing_filled"
        for warning in agent.last_warnings
    )


def test_turn_resolution_soft_truncates_second_attempt_text():
    from app.core.llm import MockLLM

    first = json.dumps({
        "resolution_reasoning": "软截断测试。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "长" * 151,
            "resolved_fact": "事实",
            "narrative_hook": "抓手",
        }],
    }, ensure_ascii=False)
    second = json.dumps({
        "resolution_reasoning": "软截断测试。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "长" * 150,
            "resolved_fact": "超长" * 100,
            "narrative_hook": "钩" * 200,
        }],
    }, ensure_ascii=False)

    mock = MockLLM()
    mock.set_responses([first, second])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察",
        current_state=_state(),
        action_parse_result=_parse(),
        roll_results=_roll_batch(_roll(20)),
        core_memory=CoreMemory(items=[]),
    )

    frame = result.action_resolutions[0]
    assert len(frame.effect_reasoning) == 150
    assert len(frame.resolved_fact) == 180
    assert len(frame.narrative_hook) == 180
    assert any(
        warning.code == "turn_resolution_truncated"
        for warning in agent.last_warnings
    )


def test_turn_resolution_reasoning_only_after_retry_is_fallback_not_missing_filled():
    from app.core.llm import MockLLM

    response = json.dumps({
        "resolution_reasoning": "只有推理没有合法 resolution。",
        "action_resolutions": [],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([response, response])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察并攻击",
        current_state=_state(),
        action_parse_result=_two_action_parse(),
        roll_results=_two_roll_batch(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.total_resolutions == 2
    assert all(
        frame.effect_reasoning == "行动按判定推进，未生成详细效果说明。"
        for frame in result.action_resolutions
    )
    assert [warning.code for warning in agent.last_warnings] == [
        "turn_resolution_fallback"
    ]


def test_turn_resolution_reuse_first_preserves_second_dropped_warnings():
    from app.core.llm import MockLLM

    first = json.dumps({
        "resolution_reasoning": "第一轮。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "观察成功。",
            "resolved_fact": "主角发现异常。",
            "narrative_hook": "异常的源头留下了可追查的痕迹。",
        }],
    }, ensure_ascii=False)
    second = json.dumps({
        "resolution_reasoning": "第二轮。",
        "action_resolutions": [{
            "action_id": "missing",
            "effect_reasoning": "未知行动。",
            "resolved_fact": "未知事实。",
            "narrative_hook": "未知的悬念。",
        }],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察并攻击",
        current_state=_state(),
        action_parse_result=_two_action_parse(),
        roll_results=_two_roll_batch(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.total_resolutions == 2
    assert result.action_resolutions[0].action_id == "a1"
    assert any(
        warning.code == "turn_resolution_dropped"
        for warning in agent.last_warnings
    )

def test_turn_resolution_missing_hook_is_rejected_and_falls_back():
    from app.core.llm import MockLLM

    response = json.dumps({
        "resolution_reasoning": "缺少 narrative_hook。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "观察成功。",
            "resolved_fact": "主角发现异常。",
        }],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([response, response])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察",
        current_state=_state(),
        action_parse_result=_parse(),
        roll_results=_roll_batch(_roll(20)),
        core_memory=CoreMemory(items=[]),
    )

    # Missing hook fails validation on every attempt; domain fallback keeps "".
    assert result.action_resolutions[0].narrative_hook == ""
    assert any(
        warning.code == "turn_resolution_fallback"
        for warning in agent.last_warnings
    )


def test_turn_resolution_hook_over_accept_limit_is_dropped_then_retried():
    from app.core.llm import MockLLM

    first = json.dumps({
        "resolution_reasoning": "抓手超长。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "观察成功。",
            "resolved_fact": "主角发现异常。",
            "narrative_hook": "长" * 151,
        }],
    }, ensure_ascii=False)

    mock = MockLLM()
    mock.set_responses([first, _response()])
    agent = TurnResolutionAgent(mock.build_gateway())

    result = agent.resolve(
        user_input="观察",
        current_state=_state(),
        action_parse_result=_parse(),
        roll_results=_roll_batch(_roll(20)),
        core_memory=CoreMemory(items=[]),
    )

    frame = result.action_resolutions[0]
    # First attempt exceeded the accept limit and was dropped; the retried
    # second response's hook was adopted instead.
    assert frame.narrative_hook.startswith("异常的来源隐约可辨")
    assert all(
        warning.code != "turn_resolution_truncated"
        for warning in agent.last_warnings
    )


def test_turn_resolution_text_within_accept_buffer_is_kept_without_retry():
    from app.core.llm.providers import MockProvider
    from app.core.llm.gateway import LLMGateway
    from app.core.llm.tracing import NullTraceLogger

    class _CountingProvider(MockProvider):
        def __init__(self, responses):
            super().__init__()
            self.set_responses(responses)
            self.calls = 0

        def generate(self, *args, **kwargs):
            self.calls += 1
            return MockProvider.generate(self, *args, **kwargs)

    # 121-150 chars sit inside the Accept buffer (H=120, A=150): accepted on
    # the first attempt without any feedback retry.
    response = json.dumps({
        "resolution_reasoning": "缓冲区长度接受。",
        "action_resolutions": [{
            "action_id": "a1",
            "effect_reasoning": "长" * 140,
            "resolved_fact": "事实",
            "narrative_hook": "抓手",
        }],
    }, ensure_ascii=False)

    provider = _CountingProvider([response])
    gateway = LLMGateway(
        provider=provider,
        prompt_loader=MockLLM().prompt_loader,
        tracer=NullTraceLogger(),
    )
    agent = TurnResolutionAgent(gateway)

    result = agent.resolve(
        user_input="观察",
        current_state=_state(),
        action_parse_result=_parse(),
        roll_results=_roll_batch(_roll(20)),
        core_memory=CoreMemory(items=[]),
    )

    frame = result.action_resolutions[0]
    assert len(frame.effect_reasoning) == 140
    assert provider.calls == 1
