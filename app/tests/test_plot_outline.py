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
from app.domain.narrative_composer import (
    NarrativeComposerAgent,
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.plot_outline.schemas import PlannedEvent
from app.domain.plot_outline.views import project_retrieved_memories
from app.domain.director_npc.schemas import DirectorNPCPlotInjection
from app.domain.director_event.schemas import DirectorEventConsumption
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.domain.short_term_memory import ShortTermScene
from app.graph.generation_helpers import assemble_narrative_context


def _make_context() -> NarrativeComposerContext:
    return assemble_narrative_context(
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
        "director_event_reason": "本回合没有自然交点，未使用。",
        "director_npc_reason": "本回合没有自然交点，未使用。",
        "planning_reasoning": planning_reasoning,
        "must_establish_facts": must_establish_facts or ["主角发现酒馆客人中的可疑行为。"],
        "must_not_establish_facts": must_not_establish_facts or ["不得透露可疑行为背后的完整真相。"],
        "information_boundaries": ["主角只能确认可见行为异常，不能确认幕后原因。"],
        "scene_stopping_point": "主角发现可疑行为后仍留在酒馆大厅，可以继续互动。",
        "planned_events": planned_events or [
            {
                "event_fact": "主角仔细观察酒馆里的客人，并清楚发现其中存在可疑行为。",
                "linked_outcome_ids": ["a1"],
                "involved_entities": ["酒馆客人"],
            },
        ],
        "scene_reasoning": "规划与正文由同一次调用完成，并保持玩家行动、结果与停点一致。",
        "scene_text": "你仔细观察酒馆里的客人，很快发现角落里有人反复避开众人的视线。",
        "closing_prompt": "那名可疑客人仍在角落观察四周，你会怎么做？",
        "next_action_directions": [],
    }, ensure_ascii=False)


class _SpyGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestNarrativeComposerAgent:
    def test_runtime_schema_locks_semantics_before_creative_planning(self):
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        NarrativeComposerAgent(gateway).compose(_make_context())

        properties = json.loads(gateway.calls[0]["input_data"]["output_schema_json"])["properties"]
        assert list(properties)[:3] == [
            "director_event_reason",
            "director_npc_reason",
            "planning_reasoning",
        ]
        ordered_fields = list(properties)
        assert ordered_fields.index("planning_reasoning") < ordered_fields.index(
            "must_establish_facts"
        )
        assert ordered_fields.index("must_establish_facts") < ordered_fields.index(
            "must_not_establish_facts"
        )
        assert ordered_fields.index("must_not_establish_facts") < ordered_fields.index(
            "information_boundaries"
        )
        assert ordered_fields.index("information_boundaries") < ordered_fields.index(
            "scene_stopping_point"
        )
        assert ordered_fields.index("scene_stopping_point") < ordered_fields.index(
            "planned_events"
        )
        assert ordered_fields.index("planned_events") < ordered_fields.index(
            "scene_reasoning"
        )
        assert list(properties).index("scene_reasoning") < list(properties).index(
            "scene_text"
        )

    def test_one_call_receives_player_input_recent_scene_and_uses_creative_temperature(self):
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        result = NarrativeComposerAgent(gateway).compose(
            _make_context(),
            recent_scene=ShortTermScene(
                scene_id="scene_001",
                turn_index=4,
                user_input="旧输入不应进入 recent scene 投影",
                scene_text="主角仍站在酒馆大厅。",
            ),
            turn_index=5,
            max_chars=1800,
        )

        call = gateway.calls[0]
        assert call["task"] == "narrative_compose"
        assert call["temperature"] == 0.8
        assert call["input_data"]["user_input"] == _make_context().user_input
        assert "max_chars" not in call["input_data"]
        assert json.loads(call["input_data"]["recent_scene_json"]) == {
            "scene_id": "scene_001",
            "turn_index": 4,
            "scene_text": "主角仍站在酒馆大厅。",
        }
        assert result.to_outline().planned_events[0].event_id == "e1"
        assert result.to_scene_result().scene_text == result.scene_text

    def test_reserved_max_chars_argument_does_not_control_composition(self):
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        NarrativeComposerAgent(gateway).compose(_make_context(), max_chars=-1)

        assert "max_chars" not in gateway.calls[0]["input_data"]

    def test_prompt_input_omits_action_parse_result(self):
        context = _make_context()
        gateway = _SpyGateway(json.loads(_build_outline_response()))

        NarrativeComposerAgent(gateway).compose(context)

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
                immutable_constraints=["不会传给 Composer 的内部约束。"],
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

        NarrativeComposerAgent(gateway).compose(context)

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

        NarrativeComposerAgent(gateway).compose(context)

        injection_json = gateway.calls[0]["input_data"]["director_npc_injection_json"]
        assert "艾琳娜" in injection_json
        assert "reason" not in injection_json
        assert "immediate_intent" not in injection_json
        assert "first_pressure" not in injection_json

    def test_generates_outline(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = NarrativeComposerAgent(mock.build_gateway())
        result = agent.compose(context)

        assert isinstance(result, NarrativeComposerResult)
        assert result.planned_events[0].event_id == "e1"
        assert result.planned_events[0].event_fact == "主角仔细观察酒馆里的客人，并清楚发现其中存在可疑行为。"

    def test_prompt_contains_outcome_following_requirements(self):
        prompt = Path("app/prompts/generation/narrative_compose_v1.md").read_text(encoding="utf-8")

        assert "TurnResolutionResult" in prompt
        assert "ActionParseResult" not in prompt
        assert "同一次推理中完成本回合的事实规划与最终正文" in prompt
        assert "Player Input 定义了玩家的绝对意图" in prompt
        assert "Player Input 管行动语义，TR 管结果方向" in prompt
        assert "规划字段是你对正文的绝对事实承诺" in prompt
        assert "must_establish_facts" in prompt
        assert "DirectorEvent Consumptions" in prompt
        assert "arc 负责" not in prompt
        assert "ambient 负责" not in prompt
        assert "不得以“TR 没写”为由输出空泛内容" in prompt
        assert "canonical_premise" not in prompt
        assert "allowed_effect" not in prompt
        assert "只包含 `allowed_facts` 与 `immutable_constraints`" in prompt
        assert "mode=foreshadow" not in prompt
        assert "mode=intersect" not in prompt
        assert "narrative_goal" not in prompt
        assert "ActionResolutionFrame" in prompt
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
        assert "自然语言逻辑审计" in prompt
        assert "本回合的语义骨架" in prompt
        assert "第一个允许进行创造性实例化的字段" in prompt
        assert "严格分成“语义锁定”和“创造性实例化”两层" in prompt
        assert "hidden_secret" in prompt
        assert "不自动成为任何角色已经知道的前台信息" in prompt
        assert "所有字段必须描述完全相同的人物、动作、结果和状态" in prompt
        assert "scene_text" in prompt
        assert "closing_prompt" in prompt

    def test_planned_events_have_required_fields(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = NarrativeComposerAgent(mock.build_gateway())
        result = agent.compose(context)

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

        agent = NarrativeComposerAgent(mock.build_gateway())
        result = agent.compose(context)

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

        agent = NarrativeComposerAgent(mock.build_gateway())
        result = agent.compose(context)

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

        result = NarrativeComposerAgent(mock.build_gateway()).compose(context)

        assert result.planned_events[0].linked_outcome_ids == ["a1"]
        assert "仔细观察" in result.planned_events[0].event_fact

    def test_accepts_turn_resolution_outcomes_in_any_link_order(self):
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
        response = _build_outline_response(
            planned_events=[
                {
                    "event_fact": "主角挖沟后撤离，事件同时关联两个判定结果。",
                    "linked_outcome_ids": ["A3", "A2"],
                }
            ]
        )
        mock = MockLLM()
        mock.set_responses([response])

        result = NarrativeComposerAgent(mock.build_gateway()).compose(context)

        assert result.planned_events[0].linked_outcome_ids == ["A3", "A2"]
        assert "挖沟后撤离" in result.planned_events[0].event_fact
