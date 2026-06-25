import pytest
from pathlib import Path
import json

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.plot_outline.schemas import PlotBeat, PlotOutlineResult
from app.domain.plot_outline.agent import PlotOutlineAgent
from app.domain.plot_outline.views import project_retrieved_memories
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate, PendingMemoryNote
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.turn_resolution import ActionResolutionFrame, TurnResolutionResult
from app.workflows.context_assembler import PlotOutlineContext, assemble_context
from app.workflows.generate_plot_outline import generate_plot_outline_workflow


def _make_context() -> PlotOutlineContext:
    return assemble_context(
        user_input="我仔细观察酒馆里的客人，看看有没有可疑的人。",
        current_state=CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="北郡修道院",
                time_label="第1日深夜",
                participants=["主角", "米莉"],
                atmosphere="宁静",
            ),
            player=PlayerState(
                name="主角",
                condition=[],
                position="酒馆大厅",
                visible_status="正常",
                intent="调查可疑人物",
            ),
            npcs=[
                NPCState(
                    name="米莉",
                    role="酒馆老板",
                    location="酒馆吧台",
                    condition=[],
                    attitude_to_player="友好",
                    current_intent="招待客人",
                ),
            ],
            resources=ResourcesState(inventory=[InventoryItem(name="旧地图")], money=50),
        ),
        action_parse_result=ActionParseResult(
            turn_intent="观察酒馆里的客人",
            atomic_actions=[
                AtomicAction(
                    action_id="a1",
                    sequence=1,
                    actor="主角",
                    action_type="observe",
                    raw_text="仔细观察酒馆里的客人",
                    verb="观察",
                    target="酒馆客人",
                    likely_needs_roll=True,
                    roll_hint="perception",
                    confidence=0.9,
                ),
            ],
            anchors=[],
            ambiguities=[],
        ),
        turn_resolution=TurnResolutionResult(
            available_types=["advance", "relationship_shift"],
            selected_type="advance",
            action_resolutions=[
                ActionResolutionFrame(
                    action_id="a1",
                    sequence=1,
                    actor="主角",
                    action_type="observe",
                    outcome_band="success",
                    resolved_effect="主角清楚发现酒馆客人中的可疑行为。",
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
        retrieved_memories=CandidateRetrievalResult(
            candidates=[
                MemoryCandidate(
                    memory_id="mem_001",
                    subtype="fact",
                    subject="修道院情报",
                    content="北郡修道院最近来了一些陌生的面孔，不是常客。",
                    entities=["北郡修道院"],
                    tags=["情报"],
                    salience=0.8,
                    confidence=0.7,
                    score=0.12,
                    score_breakdown={"rrf_score": 0.12},
                ),
            ],
            total_scanned=50,
            total_returned=1,
        ),
        scene_summaries=[
            SceneSummary(
                level=2,
                content="此前几回合中，卢西安在北郡调查陌生人。",
                scene_ids=["scene_001", "scene_002"],
                source_ids=[],
                turn_start=1,
                turn_end=2,
            )
        ],
    )


def _build_outline_response(
    *,
    outline_summary: str = "主角观察酒馆客人，注意到可疑行为",
    beats: list[dict] | None = None,
    constraints: list[str] | None = None,
    risks: list[str] | None = None,
) -> str:
    import json
    return json.dumps({
        "outline_summary": outline_summary,
        "beats": beats or [
            {
                "beat_id": "b1",
                "summary": "主角环顾酒馆，注意到角落里的可疑人物",
                "user_action_execution": "主角按照用户输入仔细观察酒馆里的客人。",
                "protagonist_response_guidance": "主角以警觉的观察和简短确认表现自己已经发现异常，但不立刻惊动对方。",
                "linked_action_ids": ["a1"],
                "linked_outcome_ids": ["a1"],
                "required_outcomes": ["主角清楚发现酒馆客人中的可疑行为。"],
                "required_memory_indices": [0],
                "state_implications": ["主角发现可疑线索"],
                "tone_notes": ["保持悬疑氛围"],
            },
        ],
        "constraints_for_scene_writer": constraints or ["必须描写酒馆氛围"],
        "risks_to_avoid": risks or ["不要透露真相太早"],
    }, ensure_ascii=False)


class TestPlotOutlineAgent:
    def test_generates_outline(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert isinstance(result, PlotOutlineResult)
        assert result.outline_summary == "主角观察酒馆客人，注意到可疑行为"

    def test_prompt_contains_outcome_following_requirements(self):
        prompt = Path("app/prompts/plot_outline_v1.md").read_text(encoding="utf-8")

        assert "TurnResolutionResult" in prompt
        assert "narrative_goal" not in prompt
        assert "resolved_effect" in prompt
        assert "information_policy" not in prompt
        assert "ambiguous_hint_only" not in prompt
        assert "clear_answer_with_advantage" not in prompt
        assert "linked_outcome_ids" in prompt
        assert "failure 或 critical_failure" in prompt
        assert "partial_success" in prompt
        assert "protagonist_response_guidance" in prompt
        assert "低决策成本的自然反应" in prompt
        assert "保留玩家选择”不等于让主角全程沉默" in prompt
        assert "SceneSummaries" in prompt
        assert "scene_summaries_json" in prompt

    def test_beats_have_required_fields(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.beats) >= 1
        beat = result.beats[0]
        assert beat.beat_id == "b1"
        assert len(beat.summary) > 0
        assert len(beat.user_action_execution) > 0
        assert len(beat.protagonist_response_guidance) > 0
        assert isinstance(beat.linked_action_ids, list)
        assert isinstance(beat.linked_outcome_ids, list)
        assert isinstance(beat.required_outcomes, list)
        assert isinstance(beat.required_memory_indices, list)

    def test_plot_beat_requires_protagonist_response_guidance(self):
        with pytest.raises(ValueError):
            PlotBeat(
                beat_id="b1",
                summary="NPC 单方面提供一段重要情报。",
            )

    def test_plot_beat_requires_execution_for_linked_actions(self):
        with pytest.raises(ValueError, match="user_action_execution is required"):
            PlotBeat(
                beat_id="b1",
                summary="行动结束后 NPC 作出评价。",
                protagonist_response_guidance="主角简短回应 NPC 的评价。",
                linked_action_ids=["a1"],
            )

    def test_plot_beat_rejects_execution_without_linked_actions(self):
        with pytest.raises(ValueError, match="must be null"):
            PlotBeat(
                beat_id="b1",
                summary="纯环境变化。",
                user_action_execution="主角执行了某个行动。",
                protagonist_response_guidance="主角警觉地观察环境变化。",
            )

    @pytest.mark.parametrize(
        "guidance",
        [
            "主角可以追问材料用量。",
            "主角可选择确认风险。",
            "主角追问配比，或询问附近是否有材料。",
            "主角也可以保持沉默。",
        ],
    )
    def test_plot_beat_rejects_optional_response_choices(self, guidance):
        with pytest.raises(ValueError, match="one concrete response"):
            PlotBeat(
                beat_id="b1",
                summary="NPC 提供关键情报。",
                protagonist_response_guidance=guidance,
            )

    def test_plot_beat_accepts_one_concrete_response(self):
        beat = PlotBeat(
            beat_id="b1",
            summary="NPC 说明错误点火会扩大孢子扩散。",
            protagonist_response_guidance=(
                "主角质疑错误点火的风险，并追问安全引燃距离。"
            ),
        )

        assert beat.protagonist_response_guidance.startswith("主角质疑")

    def test_retrieved_memory_projection_uses_unified_indices(self):
        projected = project_retrieved_memories(
            CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="mem_001",
                        subtype="fact",
                        subject="正式记忆",
                        content="北郡修道院最近来了一些陌生人。",
                        entities=["北郡修道院"],
                        tags=["情报"],
                        salience=0.8,
                        confidence=0.7,
                        score=0.12,
                    ),
                ],
                pending_notes=[
                    PendingMemoryNote(
                        pending_id="pending_001",
                        scene_id="scene_001",
                        turn_index=1,
                        raw_text="米莉刚刚提到调查团异常。",
                        entities=["米莉", "调查团"],
                    ),
                ],
                total_scanned=2,
                total_returned=2,
            )
        )

        data = json.loads(projected)

        assert list(data.keys()) == ["memories", "total_scanned", "total_returned"]
        assert data["memories"][0]["memory_index"] == 0
        assert data["memories"][0]["source"] == "classified"
        assert data["memories"][1]["memory_index"] == 1
        assert data["memories"][1]["source"] == "pending"
        assert data["memories"][1]["pending_id"] == "pending_001"

    def test_outline_has_constraints_and_risks(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            constraints=["约束1", "约束2"],
            risks=["风险1"],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.constraints_for_scene_writer) >= 1
        assert len(result.risks_to_avoid) >= 1

    def test_handles_empty_retrieved_memories(self):
        context = _make_context()
        context.retrieved_memories = CandidateRetrievalResult(
            candidates=[], total_scanned=0, total_returned=0
        )
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "主角观察但没发现异常",
                    "user_action_execution": "主角仔细观察酒馆中的所有客人。",
                    "protagonist_response_guidance": "主角对没有发现异常表现出克制的疑惑，并继续保持观察姿态。",
                    "linked_action_ids": ["a1"],
                    "required_memory_indices": [],
                    "state_implications": [],
                    "tone_notes": [],
                },
            ],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.beats) >= 1

    def test_multiple_beats_ordered(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "第一步",
                    "user_action_execution": "主角执行观察行动。",
                    "protagonist_response_guidance": "主角对第一步结果作即时确认。",
                    "linked_action_ids": ["a1"],
                    "required_memory_indices": [],
                    "state_implications": [],
                    "tone_notes": [],
                },
                {
                    "beat_id": "b2",
                    "summary": "第二步",
                    "protagonist_response_guidance": "主角承接前一步信息，自然回应当前变化。",
                    "linked_action_ids": [],
                    "required_memory_indices": [],
                    "state_implications": [],
                    "tone_notes": [],
                },
            ],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert len(result.beats) == 2
        assert result.beats[0].beat_id == "b1"
        assert result.beats[1].beat_id == "b2"

    def test_retries_when_user_action_is_omitted(self):
        context = _make_context()
        invalid = _build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "从观察行动完成后的 NPC 反应开始。",
                    "user_action_execution": None,
                    "protagonist_response_guidance": "主角对 NPC 的反应保持警觉。",
                    "linked_action_ids": [],
                }
            ]
        )
        mock = MockLLM()
        mock.set_responses([invalid, _build_outline_response()])

        result = PlotOutlineAgent(mock.build_gateway()).generate(context)

        assert result.beats[0].linked_action_ids == ["a1"]
        assert "仔细观察" in result.beats[0].user_action_execution

    def test_retries_when_plot_starts_after_earlier_user_actions(self):
        context = _make_context()
        context.action_parse_result.atomic_actions = [
            AtomicAction(
                action_id="A1",
                sequence=1,
                actor="主角",
                action_type="speak",
                raw_text="说明无法力敌，并提出挖沟拖延。",
                verb="说明",
                target="同伴",
                likely_needs_roll=False,
                confidence=0.95,
            ),
            AtomicAction(
                action_id="A2",
                sequence=2,
                actor="主角",
                action_type="interact_object",
                raw_text="快速挖一条小壕沟。",
                verb="挖掘",
                target="地面",
                likely_needs_roll=True,
                confidence=0.95,
            ),
            AtomicAction(
                action_id="A3",
                sequence=3,
                actor="主角",
                action_type="move",
                raw_text="以最快速度绕路返回镇子。",
                verb="返回",
                target="镇子",
                likely_needs_roll=True,
                confidence=0.95,
            ),
        ]
        invalid = _build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "两人沿绕行路线撤离。",
                    "user_action_execution": "主角开始绕路返回镇子。",
                    "protagonist_response_guidance": "主角警觉地观察撤离路线。",
                    "linked_action_ids": ["A3"],
                }
            ]
        )
        valid = _build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "主角说明计划并挖沟，然后开始撤离。",
                    "user_action_execution": (
                        "主角先向同伴说明无法力敌并提出挖沟拖延，随后亲手快速挖出浅沟，"
                        "完成后立即沿绕行路线返回镇子。"
                    ),
                    "protagonist_response_guidance": "主角确认浅沟截住主蔓后，警觉地观察撤离路线。",
                    "linked_action_ids": ["A1", "A2", "A3"],
                }
            ]
        )
        mock = MockLLM()
        mock.set_responses([invalid, valid])

        result = PlotOutlineAgent(mock.build_gateway()).generate(context)

        assert result.beats[0].linked_action_ids == ["A1", "A2", "A3"]
        assert "挖出浅沟" in result.beats[0].user_action_execution


class TestPlotOutlineWorkflow:
    def test_workflow_with_mock(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        result = generate_plot_outline_workflow(context, mock.build_gateway())

        assert isinstance(result, PlotOutlineResult)
        assert len(result.beats) >= 1
