import pytest
from pydantic import ValidationError
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, AtomicAction
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.narrative_pacer import NarrativePacingResult
from app.domain.outcome import OutcomeFrame, OutcomeResolutionResult
from app.domain.plot_outline.schemas import PlotBeat, PlotOutlineResult
from app.domain.plot_outline.agent import PlotOutlineAgent
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.roll.schemas import RollResult, RollResultBatch
from app.domain.short_term_memory import ShortTermMemory, ShortTermScene
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
            resources=ResourcesState(inventory=["旧地图"], money=50),
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
        roll_results=RollResultBatch(
            dice_formula="1d20",
            dice_sides=20,
            total_rolls=1,
            results=[
                RollResult(
                    action_id="a1",
                    sequence=1,
                    actor="主角",
                    action_type="observe",
                    dice="1d20",
                    value=18,
                    dice_sides=20,
                    roll_hint="perception",
                ),
            ],
        ),
        outcome_resolution=OutcomeResolutionResult(
            outcomes=[
                OutcomeFrame(
                    action_id="a1",
                    sequence=1,
                    actor="主角",
                    action_type="observe",
                    dice="1d20",
                    roll_value=18,
                    dice_sides=20,
                    outcome_band="success",
                    required_consequence="The action should succeed in a clear but grounded way.",
                    information_policy="clear_answer_allowed",
                )
            ],
            total_outcomes=1,
        ),
        narrative_pacing=NarrativePacingResult(
            available_types=["advance", "relationship_shift"],
            selected_type="advance",
            narrative_goal="回应当前观察行动，并推动调查目标向前。",
            previous_types=[],
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
        short_term_memory=ShortTermMemory(
            recent_scenes=[
                ShortTermScene(
                    scene_id="scene_001",
                    turn_index=1,
                    scene_text="主角推开修道院厚重的木门，走进酒馆大厅。",
                ),
            ],
            max_items=5,
        ),
        retrieved_memories=CandidateRetrievalResult(
            candidates=[
                MemoryCandidate(
                    memory_id="mem_001",
                    memory_type="fact",
                    subject="修道院情报",
                    content="北郡修道院最近来了一些陌生的面孔，不是常客。",
                    entities=["北郡修道院"],
                    tags=["情报"],
                    salience=0.8,
                    certainty=0.7,
                    score=0.12,
                    score_breakdown={"rrf_score": 0.12},
                ),
            ],
            total_scanned=50,
            total_returned=1,
        ),
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
                "linked_action_ids": ["a1"],
                "linked_roll_ids": ["a1"],
                "linked_outcome_ids": ["a1"],
                "required_outcomes": ["The action should succeed in a clear but grounded way."],
                "required_memories": ["mem_001"],
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

        assert "OutcomeResolutionResult" in prompt
        assert "NarrativePacingResult" in prompt
        assert "narrative_goal" in prompt
        assert "required_consequence" in prompt
        assert "information_policy" in prompt
        assert "ambiguous_hint_only" in prompt
        assert "clear_answer_with_advantage" in prompt
        assert "linked_outcome_ids" in prompt
        assert "failure 或 critical_failure" in prompt
        assert "partial_success" in prompt

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
        assert isinstance(beat.linked_action_ids, list)
        assert isinstance(beat.linked_roll_ids, list)
        assert isinstance(beat.linked_outcome_ids, list)
        assert isinstance(beat.required_outcomes, list)

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

    def test_handles_zero_roll_results(self):
        context = _make_context()
        context.roll_results = RollResultBatch(
            dice_formula="0d20", dice_sides=20, total_rolls=0, results=[]
        )
        mock = MockLLM()
        mock.set_responses([_build_outline_response(
            beats=[
                {
                    "beat_id": "b1",
                    "summary": "主角观察酒馆，一切如常",
                    "linked_action_ids": ["a1"],
                    "linked_roll_ids": [],
                    "required_memories": [],
                    "state_implications": [],
                    "tone_notes": ["平淡推进"],
                },
            ],
        )])

        agent = PlotOutlineAgent(mock.build_gateway())
        result = agent.generate(context)

        assert result.outline_summary == "主角观察酒馆客人，注意到可疑行为"

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
                    "linked_action_ids": ["a1"],
                    "linked_roll_ids": ["a1"],
                    "required_memories": [],
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
                    "linked_action_ids": ["a1"],
                    "linked_roll_ids": [],
                    "required_memories": [],
                    "state_implications": [],
                    "tone_notes": [],
                },
                {
                    "beat_id": "b2",
                    "summary": "第二步",
                    "linked_action_ids": [],
                    "linked_roll_ids": [],
                    "required_memories": [],
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


class TestPlotOutlineWorkflow:
    def test_workflow_with_mock(self):
        context = _make_context()
        mock = MockLLM()
        mock.set_responses([_build_outline_response()])

        result = generate_plot_outline_workflow(context, mock.build_gateway())

        assert isinstance(result, PlotOutlineResult)
        assert len(result.beats) >= 1
