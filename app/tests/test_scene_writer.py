import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import CurrentState, InventoryItem, PlayerState, ResourcesState, SceneState
from app.domain.plot_outline.schemas import PlannedEvent, PlotOutlineResult
from app.domain.scene_writer.agent import SceneWriterAgent
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.domain.scene_writer.views import project_outline, project_recent_scene
from app.domain.short_term_memory import ShortTermScene
from app.domain.core_memory import InitConfig, build_core_memory, build_initial_current_state


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
        planning_reasoning="本测试规划根据主角与米莉交谈的输入组织两个连续事件，先落实打招呼，再承接询问消息。",
        planned_events=[
            PlannedEvent(
                event_id="e1",
                event_fact="主角走向吧台与米莉打招呼。",
                involved_entities=["米莉"],
            ),
            PlannedEvent(
                event_id="e2",
                event_fact="米莉告诉主角关于暴风城的消息。",
                involved_entities=["米莉", "暴风城"],
            ),
        ],
        must_establish_facts=[
            "主角必须以人类法师身份行动",
            "不能改变米莉的核心人设",
        ],
        must_not_establish_facts=[
            "不能让主角使用超出能力范围的魔法",
            "不能让米莉突然变得敌对",
        ],
        information_boundaries=["主角只能知道米莉公开说出的暴风城消息。"],
        required_entities=["米莉", "暴风城"],
        scene_stopping_point="主角停在吧台前，米莉的话题仍可继续追问。",
        director_npc_reason="本测试未使用。",
    )


def _build_response(
    scene_text: str = "主角推开酒馆沉重的木门，夜风卷起几片落叶随着他一同进入温暖的室内。米莉正在吧台后面擦拭酒杯，看到主角后露出微笑。",
    closing_prompt: str = "米莉似乎知道暴风城的更多消息，你会怎么做？",
    directions: list[dict] | None = None,
) -> str:
    return json.dumps({
        "scene_reasoning": "本回合先承接规划事件顺序，确认主角行动与米莉反馈都来自公开上下文。正文只演出已给出的酒馆、米莉和暴风城消息，不新增任务或隐藏真相。",
        "scene_text": scene_text,
        "closing_prompt": closing_prompt,
        "next_action_directions": directions or [],
    }, ensure_ascii=False)


class TestSceneWriterAgent:
    def test_outline_projection_uses_planned_events(self):
        projected = json.loads(project_outline(_make_outline()))

        assert "protagonist_response_guidance" not in projected["planned_events"][0]
        assert "state_implications" not in projected["planned_events"][0]
        assert "tone_notes" not in projected["planned_events"][0]
        assert "outline_summary" not in projected
        assert "planning_reasoning" not in projected
        assert projected["planned_events"][0]["event_fact"] == "主角走向吧台与米莉打招呼。"

    def test_recent_scene_projection_omits_user_input(self):
        projected = json.loads(project_recent_scene(ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            user_input="观察四周",
            scene_text="主角环顾酒馆。",
        )))

        assert projected == {
            "scene_id": "scene_001",
            "turn_index": 1,
            "scene_text": "主角环顾酒馆。",
        }
        assert "user_input" not in projected

    def test_prompt_allows_natural_reactions_but_protects_major_decisions(self):
        prompt = Path("app/prompts/generation/scene_write_v1.md").read_text(encoding="utf-8")

        assert "不得替主角接受或拒绝任务" in prompt
        assert "必须自然落实所有 planned_events" in prompt
        assert "planned_events` 是事实清单" in prompt
        assert "不是把每个 planned_event 扩写成独立小节" in prompt
        assert "落实可以是概括、转述或一句明确结果" in prompt
        assert "{max_chars}" not in prompt
        assert "Length Target" not in prompt
        assert "不得从“行动已经完成以后”开始" in prompt
        assert "protagonist_response_guidance" not in prompt
        assert "changes_summary" not in prompt
        assert "scene_reasoning" in prompt
        assert "closing_prompt" in prompt
        assert "正文生成前的逻辑审计" in prompt
        assert "不得在 scene_text 中自行补出具体机构、专有名词、功能物品" in prompt
        assert "Entity Profiles" in prompt
        assert "绝对不得出现在 scene_text 中" in prompt

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
            recent_scene=ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="主角推开修道院厚重的木门，走进酒馆大厅。",
            ),
        )

        assert isinstance(result, SceneWriterResult)

        assert len(result.scene_text) > 0
        assert not result.scene_text.endswith(result.closing_prompt)
        assert result.closing_prompt not in result.scene_text
        assert result.closing_prompt == "米莉似乎知道暴风城的更多消息，你会怎么做？"

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
            recent_scene=None,
        )

        assert result.next_action_directions == [
            NextActionDirection(
                direction="追问仓库区的异常",
                rationale="米莉刚提到夜里有人出没，现场仍可继续交谈。",
                anchors=["米莉", "仓库区"],
            )
        ]

    def test_strips_duplicate_closing_prompt_from_scene_text(self):
        closing_prompt = "米莉似乎知道更多消息，你会怎么做？"
        mock = MockLLM()
        mock.set_responses([_build_response(
            scene_text=f"米莉放下酒杯。\n\n{closing_prompt}",
            closing_prompt=closing_prompt,
        )])

        result = SceneWriterAgent(mock.build_gateway()).write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            recent_scene=None,
        )

        assert result.scene_text == "米莉放下酒杯。"
        assert result.closing_prompt == closing_prompt

    def test_accepts_scene_text_beyond_max_chars_interface_hint(self):
        mock = MockLLM()
        mock.set_responses([
            _build_response(scene_text="过" * 11),
            _build_response(scene_text="不应重试"),
        ])

        result = SceneWriterAgent(mock.build_gateway()).write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            recent_scene=None,
            max_chars=10,
        )

        assert result.scene_text == "过" * 11

    def test_handles_no_recent_scene(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=_make_outline(),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            recent_scene=None,
        )

        assert len(result.scene_text) > 0

    def test_respects_constraints(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        agent = SceneWriterAgent(mock.build_gateway())
        result = agent.write(
            outline=PlotOutlineResult(
                planning_reasoning="本测试规划只用于验证事实边界会被传入 SceneWriter。",
                planned_events=[PlannedEvent(
                    event_id="e1",
                    event_fact="测试事件成立。",
                )],
                must_establish_facts=["不能使用魔法"],
                must_not_establish_facts=["不要引入新角色"],
                scene_stopping_point="测试停点。",
            ),
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            recent_scene=None,
        )

        assert isinstance(result, SceneWriterResult)



class TestOpeningSceneAgent:
    def test_opening_prompt_initializes_scene_without_npcs_or_events(self):
        prompt = Path("app/prompts/confirmation/opening_scene_v1.md").read_text(encoding="utf-8")

        assert "初始情景" in prompt
        assert "不得引入新的具名 NPC" in prompt
        assert "不得引入具体剧情事件" in prompt
        assert "重点是初始化" in prompt

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
        )])

        result = OpeningSceneAgent(mock.build_gateway()).generate(
            config=config,
            current_state=current_state,
            core_memory=core_memory,
        )

        assert isinstance(result, SceneWriterResult)
        assert "卢西安" in result.scene_text
