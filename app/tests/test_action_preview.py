import json

import pytest

from app.core.llm import MockLLM
from app.core.llm.errors import LLMProviderError
from app.domain.action_preview.agent import ActionPreviewAgent
from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.scene_writer.schemas import NextActionDirection
from app.domain.scene_facts.schemas import SceneFact, SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院酒馆",
            participants=[
                "卢西安",
                {"name": "米莉", "attitude_to_player": "谨慎友善"},
            ],
        ),
        player=PlayerState(
            name="卢西安",
        ),
        resources=ResourcesState(inventory=[InventoryItem(name="旧地图")], money=50),
    )


def _make_core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world",
                kind="world_invariant",
                strength="hard_constraint",
                source="player",
                content="当前世界是经典旧世时代的艾泽拉斯。",
        )
    ])


def _make_recent_summaries() -> list[SceneSummary]:
    return []


def _build_response() -> str:
    return json.dumps({
        "previews": [
            {
                "level": "正面威慑",
                "label": "立刻追问米莉仓库区的异常。",
                "good_result": "可能逼近仓库区线索的核心。",
                "risk": "追问过急可能让米莉感到不安。",
            },
            {
                "level": "情感动员",
                "label": "先稳住语气确认米莉是否安全。",
                "good_result": "可能获得更多信任并减少误会。",
                "risk": "节奏放慢可能错过继续追问的窗口。",
            },
            {
                "level": "线索核验",
                "label": "对照旧地图询问仓库区位置。",
                "good_result": "可能明确下一步可前往的方向。",
                "risk": "暴露你对仓库区缺乏了解。",
            },
            {
                "level": "话术试探",
                "label": "用闲聊试探米莉是否避开某些名字。",
                "good_result": "可能发现她刻意回避的线索范围。",
                "risk": "试探太明显可能让谈话变僵。",
            },
        ],
    }, ensure_ascii=False)


class TestActionPreviewAgent:
    def test_generates_valid_preview_result(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = ActionPreviewAgent(mock.build_gateway()).generate(
            current_state=_make_state(),
            core_memory=_make_core_memory(),
            recent_summaries=_make_recent_summaries(),
        )

        assert isinstance(result, ActionPreviewResult)
        assert len(result.previews) == 4
        assert {preview.level for preview in result.previews} == {
            "正面威慑",
            "情感动员",
            "线索核验",
            "话术试探",
        }
        assert all(preview.label for preview in result.previews)

    def test_passes_next_action_directions_without_closing_prompt(self):
        class SpyGateway:
            def __init__(self):
                self.input_data = None

            def run(self, **kwargs):
                self.input_data = kwargs["input_data"]
                if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
                    kwargs["on_attempt_start"](1)
                result = ActionPreviewResult.model_validate(json.loads(_build_response()))
                if "output_validator" in kwargs and kwargs["output_validator"] is not None:
                    kwargs["output_validator"](result)
                return result

        gateway = SpyGateway()

        ActionPreviewAgent(gateway).generate(
            current_state=_make_state(),
            core_memory=_make_core_memory(),
            recent_summaries=_make_recent_summaries(),
            next_action_directions=[
                NextActionDirection(
                    direction="追问仓库区的夜间出没",
                    rationale="正文结尾公开了米莉的线索，主角仍在酒馆内。",
                    anchors=["米莉", "仓库区"],
                )
            ],
            scene_facts=SceneFactSet(
                scene_focus="米莉公开了仓库区线索。",
                facts=[SceneFact(fact_id="f1", fact="米莉指出仓库区夜间有异常。")],
            ),
        )

        assert "closing_prompt" not in gateway.input_data
        assert "scene_text" not in gateway.input_data
        directions = json.loads(gateway.input_data["next_action_directions_json"])
        assert directions == [{
            "direction": "追问仓库区的夜间出没",
            "rationale": "正文结尾公开了米莉的线索，主角仍在酒馆内。",
            "anchors": ["米莉", "仓库区"],
        }]
        scene_facts = json.loads(gateway.input_data["scene_facts_json"])
        assert scene_facts["scene_focus"] == "米莉公开了仓库区线索。"
        assert scene_facts["facts"][0]["fact"] == "米莉指出仓库区夜间有异常。"




def _preview(**overrides):
    data = {
        "level": "正面威慑",
        "label": "立刻追问米莉仓库区的异常。",
        "good_result": "可能逼近仓库区线索的核心。",
        "risk": "追问过急可能让米莉感到不安。",
    }
    data.update(overrides)
    return data


def _response(previews):
    return json.dumps({"previews": previews}, ensure_ascii=False)


def _run(mock, **overrides):
    agent = ActionPreviewAgent(mock.build_gateway())
    result = agent.generate(
        current_state=_make_state(),
        core_memory=_make_core_memory(),
        recent_summaries=_make_recent_summaries(),
        **overrides,
    )
    return result, agent


def test_provider_failure_propagates():
    mock = MockLLM()
    mock.set_fail_provider(True)

    with pytest.raises(LLMProviderError):
        _run(mock)


def test_retries_invalid_preview_and_uses_second():
    first = [_preview(), _preview(label="第二条", good_result=None), _preview(label="第三条"), _preview(label="第四条")]
    second = [_preview(label="一"), _preview(label="二"), _preview(label="三"), _preview(label="四")]
    mock = MockLLM()
    mock.set_responses([_response(first), _response(second)])

    result, agent = _run(mock)

    assert len(result.previews) == 4
    assert agent.last_warnings == []


def test_uses_first_valid_subset_when_second_has_no_valid_previews():
    first = [_preview(), _preview(label="二"), _preview(label="三"), _preview(good_result=None)]
    second = [_preview(good_result=None), _preview(good_result=None), _preview(good_result=None), _preview(good_result=None)]
    mock = MockLLM()
    mock.set_responses([_response(first), _response(second)])

    result, agent = _run(mock)

    assert len(result.previews) == 3
    assert any(w.code == "action_preview_dropped" for w in agent.last_warnings)


def test_preview_count_not_four_triggers_retry():
    first = [_preview(label="一"), _preview(label="二"), _preview(label="三")]
    second = [_preview(label="一"), _preview(label="二"), _preview(label="三"), _preview(label="四")]
    mock = MockLLM()
    mock.set_responses([_response(first), _response(second)])

    result, agent = _run(mock)

    assert len(result.previews) == 4
    assert agent.last_warnings == []


def test_duplicate_label_second_drops_duplicate_and_keeps_unique():
    first = [_preview(), _preview(), _preview(label="三"), _preview(label="四")]
    second = [_preview(), _preview(), _preview(label="三"), _preview(label="四")]
    mock = MockLLM()
    mock.set_responses([_response(first), _response(second)])

    result, agent = _run(mock)

    assert len(result.previews) == 3
    assert any(w.code == "action_preview_dropped" for w in agent.last_warnings)
