import json

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState
from app.domain.plot_outline.schemas import PlotBeat, PlotOutlineResult
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory import ShortTermMemory, ShortTermScene
from app.workflows.generate_scene import generate_scene_workflow


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院酒馆",
            time_label="第1日深夜",
            participants=["主角", "米莉"],
            atmosphere="宁静",
        ),
        player=PlayerState(
            name="主角",
            condition=[],
            position="酒馆吧台",
        ),
        resources=ResourcesState(inventory=["旧地图"], money=50),
    )


def _make_outline() -> PlotOutlineResult:
    return PlotOutlineResult(
        outline_summary="主角与米莉交谈，达成约定",
        beats=[
            PlotBeat(
                beat_id="b1",
                summary="主角走向吧台与米莉打招呼",
                linked_action_ids=["a1"],
                linked_roll_ids=["a1"],
                required_memories=["mem_001"],
                state_implications=["米莉对主角的态度更加友善"],
                tone_notes=["轻松", "友好"],
            ),
            PlotBeat(
                beat_id="b2",
                summary="米莉告诉主角关于暴风城的消息",
                linked_action_ids=["a2"],
                linked_roll_ids=["a2"],
                required_memories=[],
                state_implications=["主角获得了新的情报"],
                tone_notes=["神秘", "暗示"],
            ),
        ],
        constraints_for_scene_writer=[
            "主角必须以人类法师身份行动",
            "不能改变米莉的核心人设",
        ],
        risks_to_avoid=[
            "不能让主角使用超出能力范围的魔法",
            "不能让米莉突然变得敌对",
        ],
    )


def _build_response(
    scene_text: str = "主角推开酒馆沉重的木门，夜风卷起几片落叶随着他一同进入温暖的室内。米莉正在吧台后面擦拭酒杯，看到主角后露出微笑。",
    turn_index: int = 2,
    changes: list[str] | None = None,
) -> str:
    return json.dumps({
        "scene_text": scene_text,
        "turn_index": turn_index,
        "changes_summary": changes or ["主角进入了酒馆", "与米莉进行了交谈"],
    }, ensure_ascii=False)


class TestSceneWriterAgent:
    def test_generates_scene_text(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[
                CoreMemoryItem(id="core_identity", category="player", content="主角是卢西安，人类法师。", priority=100),
            ]),
            user_input="我走向吧台，向米莉打听暴风城的情况。",
            selected_memories=[
                MemoryCandidate(
                    memory_id="mem_001",
                    memory_type="fact",
                    subject="暴风城现状",
                    content="暴风城正在经历政治风波，暗流涌动。",
                    entities=["暴风城"],
                    tags=["情报"],
                    salience=0.8,
                    certainty=0.7,
                    score=0.15,
                    score_breakdown={"rrf_score": 0.15},
                ),
            ],
            recent_scene=ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="主角推开修道院厚重的木门，走进酒馆大厅。",
            ),
        )

        assert isinstance(result, SceneWriterResult)
        assert len(result.scene_text) > 0
        assert result.turn_index == 2
        assert len(result.changes_summary) >= 1

    def test_handles_no_recent_scene(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            user_input="测试行动。",
            selected_memories=[],
            recent_scene=None,
        )

        assert len(result.scene_text) > 0

    def test_respects_constraints(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=PlotOutlineResult(
                outline_summary="测试",
                beats=[PlotBeat(
                    beat_id="b1",
                    summary="测试节拍",
                    linked_action_ids=[],
                    linked_roll_ids=[],
                    required_memories=[],
                    state_implications=[],
                    tone_notes=["紧张"],
                )],
                constraints_for_scene_writer=["不能使用魔法"],
                risks_to_avoid=["不要引入新角色"],
            ),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            user_input="测试。",
            selected_memories=[],
            recent_scene=None,
        )

        assert isinstance(result, SceneWriterResult)


class TestSceneWriterWorkflow:
    def test_workflow_filters_memories_by_required(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = generate_scene_workflow(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            user_input="我走向吧台。",
            retrieved_memories=CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="mem_001",
                        memory_type="fact",
                        subject="暴风城现状",
                        content="暴风城正在经历政治风波。",
                        entities=["暴风城"],
                        tags=[],
                        salience=0.8,
                        certainty=0.7,
                        score=0.15,
                        score_breakdown={"rrf_score": 0.15},
                    ),
                    MemoryCandidate(
                        memory_id="mem_999",
                        memory_type="trait",
                        subject="无关记忆",
                        content="不应该被选中的记忆。",
                        entities=["无关"],
                        tags=[],
                        salience=0.5,
                        certainty=0.5,
                        score=0.05,
                        score_breakdown={"rrf_score": 0.05},
                    ),
                ],
                total_scanned=10,
                total_returned=2,
            ),
            short_term_memory=ShortTermMemory(recent_scenes=[], max_items=5),
            gateway=mock.build_gateway(),
        )

        assert isinstance(result, SceneWriterResult)

    def test_workflow_with_short_term_memory(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = generate_scene_workflow(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            user_input="我走向吧台。",
            retrieved_memories=CandidateRetrievalResult(
                candidates=[],
                total_scanned=0,
                total_returned=0,
            ),
            short_term_memory=ShortTermMemory(
                recent_scenes=[
                    ShortTermScene(
                        scene_id="scene_001",
                        turn_index=1,
                        user_input="观察四周",
                        scene_text="主角环顾酒馆。",
                    ),
                ],
                max_items=5,
            ),
            gateway=mock.build_gateway(),
        )

        assert isinstance(result, SceneWriterResult)
