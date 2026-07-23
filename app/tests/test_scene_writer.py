import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory import InitConfig, build_core_memory, build_initial_current_state
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
    def test_opening_prompt_initializes_scene_without_npcs_or_events(self):
        prompt = Path("app/prompts/confirmation/opening_scene_v1.md").read_text(
            encoding="utf-8"
        )

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
        mock.set_responses([_opening_response()])

        result = OpeningSceneAgent(mock.build_gateway()).generate(
            config=config,
            current_state=current_state,
            core_memory=core_memory,
        )

        assert isinstance(result, SceneWriterResult)
        assert "卢西安" in result.scene_text
