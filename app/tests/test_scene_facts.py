import json
from pathlib import Path

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneFactSet,
)


class _SpyGateway:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="酒馆", participants=["卢西安"]),
        player=PlayerState(name="卢西安", position="酒馆"),
    )


def test_scene_fact_extractor_returns_empty_for_blank_text():
    gateway = _SpyGateway({})

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text=" ",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
        )
    )

    assert result == SceneFactSet(scene_focus="空场景正文", facts=[])
    assert gateway.calls == []


def test_scene_fact_extractor_sends_scene_text_and_assigns_fact_ids():
    response = {
        "scene_focus": "卢西安进入酒馆。",
        "facts": [
            {"fact": "卢西安刚刚进入酒馆。"},
            {"fact": "剧情结束时卢西安身处酒馆。"},
        ],
    }
    gateway = _SpyGateway(response)

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="卢西安进入酒馆。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
        ),
        trace_id="trace",
    )

    input_data = gateway.calls[0]["input_data"]
    assert gateway.calls[0]["task"] == "scene_fact_extract"
    assert input_data["scene_text"] == "卢西安进入酒馆。"
    schema_properties = json.loads(input_data["output_schema_json"])["properties"]
    assert sorted(schema_properties) == [
        "facts",
        "scene_focus",
    ]
    assert result.scene_focus == "卢西安进入酒馆。"
    assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]
    assert result.facts[0].fact == "卢西安刚刚进入酒馆。"


def test_prompt_requires_explicit_time_progression_to_be_preserved():
    prompt = Path("app/prompts/confirmation/scene_fact_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "应当准确提取时间流逝" in prompt
    assert "不得压缩成只描述早餐、出发、抵达等事件而丢失时间变化" in prompt
