import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory import (
    CampaignSetupProposal,
    InitConfig,
    build_core_memory,
    build_initial_current_state,
)
from app.domain.opening_foundation import OpeningFoundation
from app.domain.narrative_composer.runner import _JSONStringFieldStream
from app.domain.scene_writer.opening import OpeningSceneAgent
from app.domain.scene_writer.schemas import SceneWriterResult


def _opening_response(
    scene_text: str = "清晨的北郡修道院钟声响起，卢西安站在熟悉的石阶前。",
) -> str:
    return json.dumps(
        {
            "scene_reasoning": "根据初始化配置建立主角与出生地点。",
            "scene_text": scene_text,
            "closing_prompt": "新的一天已经开始，你会怎么做？",
            "next_action_directions": [],
        },
        ensure_ascii=False,
    )


class TestNarrativeComposerStreaming:
    def test_scene_text_stream_handles_chunk_boundaries_and_json_escapes(self):
        stream = _JSONStringFieldStream("scene_text")

        deltas = [
            stream.feed('{"scene_reasoning":"略","scene_'),
            stream.feed('text":"第一行\\n她说：\\"走吧\\"。'),
            stream.feed('","closing_prompt":"继续？"}'),
        ]

        assert "".join(deltas) == '第一行\n她说："走吧"。'

    def test_scene_text_stream_can_reset_for_retry(self):
        stream = _JSONStringFieldStream("scene_text")
        assert stream.feed('{"scene_text":"失败版本') == "失败版本"

        stream.reset()

        assert stream.feed('{"scene_text":"有效版本"}') == "有效版本"


class TestOpeningSceneAgent:
    def test_opening_prompt_uses_private_foundation_without_leaking_it(self):
        prompt = Path("app/prompts/initialization/opening_scene_v1.md").read_text(
            encoding="utf-8"
        )

        assert "Opening Foundation" in prompt
        assert "地点、开局处境，以及与当下行动有关的初始资源" in prompt
        assert "不得自行引入具名 NPC" in prompt
        assert "不得替玩家作出重大决定" in prompt

    def test_generates_opening_scene(self):
        config = InitConfig(
            world_name="艾兰",
            world_style="古典奇幻",
            ability_system="魔法",
            protagonist_name="卢西安",
            story_expectation="探索世界并逐步成长。",
            failure_severity="适中",
        )
        proposal = CampaignSetupProposal(
            currency_unit="金币",
            public_power_baseline={
                "source": "魔法",
                "prevalence": "少数人掌握",
                "learning_difficulty": "困难",
                "limitation": "不能复活死者。",
            },
            peoples_and_beings=["人类", "精灵"],
            major_world_conflicts=["帝国扩张持续挤压边境诸国。"],
            dominant_powers=["北方帝国", "圣辉教会"],
            civilization_baseline="以马车、冷兵器和手工业为主。",
        )
        foundation = OpeningFoundation.model_validate({
            "starting_location": {
                "name": "北郡修道院",
                "description": "北郡的一座修道院。",
            },
            "protagonist_opening_situation": (
                "卢西安在清晨抵达修道院，必须安排今日的落脚与去向。"
            ),
            "initial_resources": {
                "inventory": [{"name": "旅行包", "quantity": 1}],
                "money": 5,
                "fixed_assets": [],
            },
        })
        core_memory = build_core_memory(config, proposal)
        current_state = build_initial_current_state(
            config,
            location="北郡修道院",
            currency_unit="金币",
            inventory=foundation.initial_resources.inventory,
            money=foundation.initial_resources.money,
        )
        mock = MockLLM()
        mock.set_responses([_opening_response()])

        result = OpeningSceneAgent(mock.build_gateway()).generate(
            current_state=current_state,
            core_memory=core_memory,
            opening_foundation=foundation,
        )

        assert isinstance(result, SceneWriterResult)
        assert "卢西安" in result.scene_text
