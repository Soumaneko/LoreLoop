import pytest
from pathlib import Path
import json

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.plot_outline.schemas import PlotOutlineContext, PlannedEvent, PlotOutlineResult
from app.domain.plot_outline.agent import PlotOutlineAgent
from app.domain.plot_outline.views import project_retrieved_memories
from app.domain.director_npc.schemas import DirectorNPCPlotInjection
from app.domain.director_event.schemas import DirectorEventConsumption
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.graph.generation_helpers import assemble_context


def _make_context() -> PlotOutlineContext:
    return assemble_context(
        user_input="我仔细观察酒馆里的客人，看看有没有可疑的人。",
        current_state=CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="北郡修道院",
                participants=[
                    "主角",
                    {"name": "米莉", "attitude_to_player": "友好"},
                ],
            ),
            player=PlayerState(
                name="主角",
                condition=[],
                visible_status="正常",
            ),
            resources=ResourcesState(inventory=[InventoryItem(name="旧地图")], money=50),
        ),
        turn_resolution=TurnResolutionResult(
            action_resolutions=[
                ActionResolutionFrame(
                    action_id="a1",
                    sequence=1,
                    actor=["主角"],
                    action_type="observe",
                    outcome_band="success",
                    effect_reasoning="观察属于 A 类，玩家意图打量客人，success 给出可疑线索。",
                    resolved_fact="主角发现酒馆客人中的可疑行为。",
                )
            ],
        ),
        core_memory=CoreMemory(
            items=[
                CoreMemoryItem(
                    id="core_player_identity",
                    category="player",
                    content="主角是卢西安，人类法师。",
                    priority=100,
                ),
                CoreMemoryItem(
                    id="core_tone",
                    category="tone",
                    content="剧情风格应保持文字冒险 DM 口吻。",
                    priority=100,
                ),
            ]
        ),
    )


def _build_outline_response(
    *,
    planning_reasoning: str = "本回合根据玩家观察酒馆客人的输入和 TurnResolutionResult 的成功结果，安排主角发现可疑行为。Memories 支持修道院有陌生面孔，因此规划只落实可疑线索，不直接揭开真相。",
    planned_events: list[dict] | None = None,
    must_establish_facts: list[str] | None = None,
    must_not_establish_facts: list[str] | None = None,
) -> str:
    import json
    return json.dumps({
        "planning_reasoning": planning_reasoning,
        "planned_events": planned_events or [
            {
                "event_fact": "主角仔细观察酒馆里的客人，并清楚发现其中存在可疑行为。",
                "linked_outcome_ids": ["a1"],
                "involved_entities": ["酒馆客人"],
            },
        ],
        "must_establish_facts": must_establish_facts or ["主角发现酒馆客人中的可疑行为。"],
        "must_not_establish_facts": must_not_establish_facts or ["不得透露可疑行为背后的完整真相。"],
        "information_boundaries": ["主角只能确认可见行为异常，不能确认幕后原因。"],
        "required_entities": ["酒馆客人"],
        "scene_stopping_point": "主角发现可疑行为后仍留在酒馆大厅，可以继续互动。",
        "director_event_reason": "本回合没有自然交点，未使用。",
        "director_npc_reason": "本回合没有自然交点，未使用。",
    }, ensure_ascii=False)


class _SpyGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestPlotOutlineAgent:
    def test_runtime_schema_puts_director_decisions_before_planning(self):
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        PlotOutlineAgent(gateway).generate(_make_context())

        properties = json.loads(gateway.calls[0]["input_data"]["output_schema_json"])["properties"]
        assert list(properties)[:3] == [
            "director_event_reason",
            "director_npc_reason",
            "planning_reasoning",
        ]

    def test_prompt_input_omits_action_parse_result(self):
        context = _make_context()
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        PlotOutlineAgent(gateway).generate(context)

        input_data = gateway.calls[0]["input_data"]
        assert "user_input" in input_data
        assert "turn_resolution_json" in input_data
        assert "resolution_reasoning" not in input_data["turn_resolution_json"]
        assert "action_parse_result_json" not in input_data
        assert "director_event_instructions" not in input_data
        assert "director_events_json" not in input_data
        schema_json = input_data["output_schema_json"]
        assert "outline_summary" not in schema_json
        assert "event_id" not in schema_json
        assert "protagonist_response_guidance" not in schema_json
        assert "state_implications" not in schema_json
        assert "tone_notes" not in schema_json

    def test_director_event_input_contains_only_consumption_fields(self):
        context = _make_context()
        context.director_event_consumptions = [
            DirectorEventConsumption(
                event_id="unused_event_id",
                reason="本回合没有自然接触面。",
                use_event=False,
                immutable_constraints=["不会传给 Plot 的内部约束。"],
            ),
            DirectorEventConsumption(
                event_id="private_event_id",
                reason="private reasoning",
                use_event=True,
                allowed_facts=["旅店留言板上有一张木偶师便条。"],
                immutable_constraints=["便条位于旅店留言板。"],
            )
        ]
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        PlotOutlineAgent(gateway).generate(context)

        payload = json.loads(
            gateway.calls[0]["input_data"]["director_event_consumptions_json"]
        )
        assert payload == [{
            "allowed_facts": ["旅店留言板上有一张木偶师便条。"],
            "immutable_constraints": ["便条位于旅店留言板。"],
        }]

    def test_director_npc_candidate_input_omits_dispatch_reason(self):
        context = _make_context()
        context.director_npc_injection = DirectorNPCPlotInjection(
            action="dispatch_standby",
            selected_id="director_npc_x",
            selected_name="艾琳娜",
            selected_npc={
                "name": "艾琳娜",
                "role": "流浪药剂师",
                "hidden_secret": "只用于导演判断",
            },
        )
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        PlotOutlineAgent(gateway).generate(context)

        injection_json = gateway.calls[0]["input_data"]["director_npc_injection_json"]
        assert "艾琳娜" in injection_json
        assert "reason" not in injection_json
        assert "immediate_intent" not in injection_json
        assert "first_pressure" not in injection_json

    def test_generates_outline(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert isinstance(result, PlotOutlineResult)
        assert result.planned_events[0].event_id == "e1"
        assert result.planned_events[0].event_fact == "主角仔细观察酒馆里的客人，并清楚发现其中存在可疑行为。"

    def test_prompt_contains_outcome_following_requirements(self):
        prompt = Path("app/prompts/generation/plot_outline_v1.md").read_text(encoding="utf-8")

        assert "TurnResolutionResult" in prompt
        assert "ActionParseResult" not in prompt
        assert "规划本回合确定事件。TurnResolutionResult 是行动结果边界" in prompt
        assert "不得把 Player Input 简化成只移动、只观察或只等待下一回合" in prompt
        assert "不得追加负面后果把 success 实质写成 failure" in prompt
        assert "不得追加有利结果把 failure 实质写成 success" in prompt
        assert "这属于落实判定，不属于擅自扩展结果" in prompt
        assert "系统确认客观事实" in prompt
        assert "不是玩家选择" in prompt
        assert "不得确认是不是目标" in prompt
        assert "避免替玩家判断" in prompt
        assert "must_establish_facts" in prompt
        assert "DirectorEvent Consumptions 是可选的导演事件材料" in prompt
        assert "arc 负责" not in prompt
        assert "ambient 负责" not in prompt
        assert "禁止任何形式的“空泛任务”" in prompt
        assert "必须写出具体的名词和结论" in prompt
        assert "依据不足时必须保持未知" in prompt
        assert "canonical_premise" not in prompt
        assert "allowed_effect" not in prompt
        assert "上述两个字段以外的导演事件内容一律视为不存在" in prompt
        example = prompt.split("# 例子", 1)[1]
        assert example.index('"director_event_reason"') < example.index('"director_npc_reason"')
        assert example.index('"director_npc_reason"') < example.index('"planning_reasoning"')
        assert "mode=foreshadow" not in prompt
        assert "mode=intersect" not in prompt
        assert "narrative_goal" not in prompt
        assert "ActionResolutionFrame" in prompt
        assert "已经确定发生的事实" in prompt
        assert "resolved_effect" not in prompt
        assert "information_policy" not in prompt
        assert "ambiguous_hint_only" not in prompt
        assert "clear_answer_with_advantage" not in prompt
        assert "linked_outcome_ids" in prompt
        assert "protagonist_response_guidance" not in prompt
        assert "outline_summary" not in prompt
        assert "state_implications" not in prompt
        assert "tone_notes" not in prompt
        assert "Context Facts" in prompt
        assert "context_facts_json" in prompt
        assert "SceneSummaries" not in prompt
        assert "scene_summaries_json" not in prompt
        assert "planning_reasoning" in prompt
        assert "规划前的逻辑审计" in prompt
        assert "NPC/世界反应的因果是否成立" in prompt
        assert "哪些后台信息不能在本回合被角色知晓" in prompt
        assert "hidden_secret" in prompt
        assert "只写抽象边界" in prompt
        assert "不得在原因字段里藏具体剧情影响" in prompt
        assert "二次裁量权" in prompt
        assert "不能为了省事每次都拒绝" in prompt

    def test_planned_events_have_required_fields(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.planned_events) >= 1
        event = result.planned_events[0]
        assert event.event_id == "e1"
        assert len(event.event_fact) > 0
        assert isinstance(event.linked_outcome_ids, list)
        assert isinstance(event.involved_entities, list)

    def test_planned_event_accepts_event_without_outcome_links(self):
        event = PlannedEvent(
            event_id="e1",
            event_fact="主角按用户原文行动。",
        )

        assert "行动" in event.event_fact

    def test_retrieved_memory_projection_adds_indices(self):
        projected = project_retrieved_memories(
            CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="mem_001",
                        memory_kind="open_thread", evidence_mode="observed",
                        subject="正式记忆",
                        content="北郡修道院最近来了一些陌生人。",
                        entities=["北郡修道院"],
                        tags=["情报"],
                        salience=0.8,
                        confidence=0.7,
                        score=0.12,
                    ),
                ],
                total_scanned=1,
                total_returned=1,
            )
        )

        data = json.loads(projected)

        assert list(data.keys()) == ["memories", "total_scanned", "total_returned"]
        assert data["memories"][0]["memory_index"] == 0
        assert data["memories"][0]["source"] == "classified"

    def test_outline_has_fact_boundaries(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            must_establish_facts=["事实1", "事实2"],
            must_not_establish_facts=["禁止1"],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.must_establish_facts) >= 1
        assert len(result.must_not_establish_facts) >= 1

    def test_multiple_planned_events_ordered(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            planned_events=[
                {
                    "event_fact": "第一步发生。",
                    "linked_outcome_ids": ["a1"],
                },
                {
                    "event_fact": "第二步发生。",
                },
            ],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.planned_events) == 2
        assert result.planned_events[0].event_id == "e1"
        assert result.planned_events[1].event_id == "e2"

    def test_retries_when_turn_resolution_outcome_is_omitted(self):
        context = _make_context()
        invalid = _build_outline_response(
            planned_events=[
                {
                    "event_fact": "主角观察酒馆，但没有落实判定结果。",
                    "linked_outcome_ids": [],
                }
            ]
        )
        mock = MockLLM()
        mock.set_responses([invalid, _build_outline_response()])

        result = PlotOutlineAgent(mock.build_gateway()).generate(context)

        assert result.planned_events[0].linked_outcome_ids == ["a1"]
        assert "仔细观察" in result.planned_events[0].event_fact

    def test_retries_when_turn_resolution_outcomes_are_out_of_order(self):
        context = _make_context()
        context.turn_resolution = TurnResolutionResult(
            action_resolutions=[
                ActionResolutionFrame(
                    action_id="A2",
                    sequence=2,
                    actor=["主角"],
                    action_type="interact_object",
                    outcome_band="success",
                    effect_reasoning="挖沟属于 A 类，玩家主动挖掘，success 让浅沟拖慢主蔓。",
                    resolved_fact="主角挖出的浅沟拖慢主蔓蔓延。",
                ),
                ActionResolutionFrame(
                    action_id="A3",
                    sequence=3,
                    actor=["主角"],
                    action_type="move",
                    outcome_band="success",
                    effect_reasoning="绕行属于 A 类，玩家沿路返回，success 让其顺利抵达。",
                    resolved_fact="主角沿绕行路线返回镇子外围。",
                ),
            ],
        )
        invalid = _build_outline_response(
            planned_events=[
                {
                    "event_fact": "主角先撤离再挖沟，顺序错误。",
                    "linked_outcome_ids": ["A3", "A2"],
                }
            ]
        )
        valid = _build_outline_response(
            planned_events=[
                {
                    "event_fact": "主角根据用户原文先亲手快速挖出浅沟，完成后立即沿绕行路线返回镇子。",
                    "linked_outcome_ids": ["A2", "A3"],
                }
            ]
        )
        mock = MockLLM()
        mock.set_responses([invalid, valid])

        result = PlotOutlineAgent(mock.build_gateway()).generate(context)

        assert result.planned_events[0].linked_outcome_ids == ["A2", "A3"]
        assert "挖出浅沟" in result.planned_events[0].event_fact
