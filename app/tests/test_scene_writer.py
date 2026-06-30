import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import CurrentState, InventoryItem, PlayerState, ResourcesState, SceneState
from app.domain.plot_outline.schemas import PlotBeat, PlotOutlineResult
from app.domain.retrieval.schemas import CandidateRetrievalResult, MemoryCandidate, PendingMemoryNote
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.domain.scene_writer.views import project_outline, project_recent_scene
from app.domain.short_term_memory import ShortTermMemory, ShortTermScene
from app.domain.core_memory import InitConfig, build_core_memory, build_initial_current_state
from app.workflows.generate_scene import generate_scene_workflow, select_required_memories


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
        resources=ResourcesState(inventory=[InventoryItem(name="旧地图")], money=50),
    )


def _make_outline() -> PlotOutlineResult:
    return PlotOutlineResult(
        outline_summary="主角与米莉交谈，达成约定",
        beats=[
            PlotBeat(
                beat_id="b1",
                summary="主角走向吧台与米莉打招呼",
                user_action_execution="主角走向吧台，主动向米莉打招呼。",
                protagonist_response_guidance="主角自然回应米莉的招呼，并以轻松语气开启话题。",
                required_memory_indices=[0],
                state_implications=["米莉对主角的态度更加友善"],
                tone_notes=["轻松", "友好"],
            ),
            PlotBeat(
                beat_id="b2",
                summary="米莉告诉主角关于暴风城的消息",
                user_action_execution="主角继续询问暴风城的近况。",
                protagonist_response_guidance="主角对消息表现出警觉，简短追问与自己当前处境最相关的部分。",
                required_memory_indices=[],
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
    changes: list[str] | None = None,
    directions: list[dict] | None = None,
) -> str:
    return json.dumps({
        "scene_text": scene_text,
        "changes_summary": changes or ["主角进入了酒馆", "与米莉进行了交谈"],
        "next_action_directions": directions or [],
    }, ensure_ascii=False)


class TestSceneWriterAgent:
    def test_outline_projection_keeps_protagonist_response_guidance(self):
        projected = json.loads(project_outline(_make_outline()))

        assert projected["beats"][0]["protagonist_response_guidance"] == (
            "主角自然回应米莉的招呼，并以轻松语气开启话题。"
        )
        assert projected["beats"][0]["user_action_execution"] == (
            "主角走向吧台，主动向米莉打招呼。"
        )

    def test_recent_scene_projection_omits_user_input(self):
        projected = json.loads(project_recent_scene(ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            user_input="观察四周",
            scene_text="主角环顾酒馆。",
            narrative_type="advance",
        )))

        assert projected == {
            "scene_id": "scene_001",
            "turn_index": 1,
            "scene_text": "主角环顾酒馆。",
            "narrative_type": "advance",
        }
        assert "user_input" not in projected

    def test_prompt_allows_natural_reactions_but_protects_major_decisions(self):
        prompt = Path("app/prompts/scene_write_v1.md").read_text(encoding="utf-8")

        assert "不得替主角接受或拒绝任务" in prompt
        assert "不得把多条 guidance 合并成开头的一次反应" in prompt
        assert "必须先完整演出 `user_action_execution`" in prompt
        assert "不得从“行动已经完成以后”开始" in prompt
        assert "不是用户原始行动的替代品" in prompt

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
            selected_memories=[
                MemoryCandidate(
                    memory_id="mem_001",
                    memory_kind="open_thread", evidence_mode="observed",
                    subject="暴风城现状",
                    content="暴风城正在经历政治风波，暗流涌动。",
                    entities=["暴风城"],
                    tags=["情报"],
                    salience=0.8,
                    confidence=0.7,
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
        assert len(result.changes_summary) >= 1

    def test_preserves_next_action_directions(self):
        mock = MockLLM()
        mock.set_responses([_build_response(directions=[
            {
                "direction": "追问仓库区的异常",
                "rationale": "米莉刚提到夜里有人出没，现场仍可继续交谈。",
                "anchors": ["米莉", "仓库区"],
            }
        ])])

        result = SceneWriterAgent(mock.build_gateway()).write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            selected_memories=[],
            recent_scene=None,
        )

        assert result.next_action_directions == [
            NextActionDirection(
                direction="追问仓库区的异常",
                rationale="米莉刚提到夜里有人出没，现场仍可继续交谈。",
                anchors=["米莉", "仓库区"],
            )
        ]

    def test_retries_when_scene_exceeds_max_chars(self):
        mock = MockLLM()
        mock.set_responses([
            _build_response(scene_text="过" * 11),
            _build_response(scene_text="合规正文"),
        ])

        result = SceneWriterAgent(mock.build_gateway()).write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            selected_memories=[],
            recent_scene=None,
            max_chars=10,
        )

        assert result.scene_text == "合规正文"

    def test_handles_no_recent_scene(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
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
                    protagonist_response_guidance="主角以紧张的观察和简短回应参与当前场面。",
                    required_memory_indices=[],
                    state_implications=[],
                    tone_notes=["紧张"],
                )],
                constraints_for_scene_writer=["不能使用魔法"],
                risks_to_avoid=["不要引入新角色"],
            ),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
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
            retrieved_memories=CandidateRetrievalResult(
                candidates=[
                    MemoryCandidate(
                        memory_id="mem_001",
                        memory_kind="open_thread", evidence_mode="observed",
                        subject="暴风城现状",
                        content="暴风城正在经历政治风波。",
                        entities=["暴风城"],
                        tags=[],
                        salience=0.8,
                        confidence=0.7,
                        score=0.15,
                        score_breakdown={"rrf_score": 0.15},
                    ),
                    MemoryCandidate(
                        memory_id="mem_999",
                        memory_kind="relationship_state", evidence_mode="inferred",
                        subject="无关记忆",
                        content="不应该被选中的记忆。",
                        entities=["无关"],
                        tags=[],
                        salience=0.5,
                        confidence=0.5,
                        score=0.05,
                        score_breakdown={"rrf_score": 0.05},
                    ),
                ],
                total_scanned=10,
                total_returned=2,
            ),
            short_term_memory=ShortTermMemory(recent_scenes=[], max_items=5),
            gateway=mock.build_gateway(),
            turn_index=1,
        )

        assert isinstance(result, SceneWriterResult)

    def test_select_required_memories_uses_input_indices(self):
        outline = PlotOutlineResult(
            outline_summary="测试",
            beats=[
                PlotBeat(
                    beat_id="b1",
                    summary="测试节拍",
                    protagonist_response_guidance="主角确认自己理解了当前信息，但不作新的重大决定。",
                    required_memory_indices=[0, 1, 999],
                )
            ],
        )
        retrieved = CandidateRetrievalResult(
            candidates=[
                MemoryCandidate(
                    memory_id="mem_001",
                    memory_kind="open_thread", evidence_mode="observed",
                    subject="米莉的邀请",
                    content="米莉邀请卢西安下次再来北郡。",
                    entities=["米莉"],
                    tags=["邀请"],
                    salience=0.8,
                    confidence=0.9,
                    score=0.2,
                ),
                MemoryCandidate(
                    memory_id="mem_002",
                    memory_kind="relationship_state", evidence_mode="inferred",
                    subject="卢西安的施法风格",
                    content="卢西安擅长精细奥术魔法控制。",
                    entities=["卢西安"],
                    tags=["奥术"],
                    salience=0.7,
                    confidence=0.8,
                    score=0.15,
                ),
                MemoryCandidate(
                    memory_id="mem_999",
                    memory_kind="open_thread", evidence_mode="observed",
                    subject="无关记忆",
                    content="不应被选择。",
                    entities=[],
                    tags=[],
                    salience=0.1,
                    confidence=0.9,
                    score=0.01,
                ),
            ],
            total_scanned=3,
            total_returned=3,
        )

        selected = select_required_memories(outline, retrieved)

        assert [memory.memory_id for memory in selected] == ["mem_001", "mem_002"]

    def test_select_required_memories_can_select_pending_notes(self):
        outline = PlotOutlineResult(
            outline_summary="测试",
            beats=[
                PlotBeat(
                    beat_id="b1",
                    summary="测试节拍",
                    protagonist_response_guidance="主角对刚出现的情报作简短追问。",
                    required_memory_indices=[1],
                )
            ],
        )
        retrieved = CandidateRetrievalResult(
            candidates=[
                MemoryCandidate(
                    memory_id="mem_001",
                    memory_kind="open_thread", evidence_mode="observed",
                    subject="正式记忆",
                    content="这条正式记忆不应被选中。",
                    entities=[],
                    tags=[],
                    salience=0.8,
                    confidence=0.9,
                    score=0.2,
                ),
            ],
            pending_notes=[
                PendingMemoryNote(
                    pending_id="pending_001",
                    scene_id="scene_001",
                    turn_index=3,
                    raw_text="米莉刚刚提到暴风城调查团有异常动向。",
                    entities=["米莉", "暴风城调查团"],
                    memory_kind="belief", subject="暴风城调查团异常动向",
                    score=0.14,
                ),
            ],
            total_scanned=2,
            total_returned=2,
        )

        selected = select_required_memories(outline, retrieved)

        assert len(selected) == 1
        assert selected[0].memory_id == "pending:pending_001"
        assert selected[0].memory_kind == "belief"
        assert selected[0].evidence_mode == "inferred"
        assert selected[0].content == "米莉刚刚提到暴风城调查团有异常动向。"
        assert selected[0].source_pending_ids == ["pending_001"]

    def test_workflow_with_short_term_memory(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = generate_scene_workflow(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
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
            turn_index=1,
        )

        assert isinstance(result, SceneWriterResult)


class TestOpeningSceneAgent:
    def test_generates_opening_scene(self):
        config = InitConfig(
            fanfic=True,
            work_name="魔兽世界",
            birth_location="北郡修道院",
            story_era="经典旧世",
            protagonist_name="卢西安",
        )
        core_memory = build_core_memory(config)
        current_state = build_initial_current_state(config)
        mock = MockLLM()
        mock.set_responses([_build_response(
            scene_text="清晨的北郡修道院钟声响起，卢西安站在熟悉的石阶前。",
            changes=["卢西安从北郡修道院开始旅程"],
        )])

        result = OpeningSceneAgent(mock.build_gateway()).generate(
            config=config,
            current_state=current_state,
            core_memory=core_memory,
        )

        assert isinstance(result, SceneWriterResult)
        assert "卢西安" in result.scene_text
        assert result.changes_summary == ["卢西安从北郡修道院开始旅程"]
