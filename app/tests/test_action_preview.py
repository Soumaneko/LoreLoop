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


def _build_response() -> str:
    return _response([_preview(level=level, label=f"{level}行动") for level in (
        "正面威慑", "情感动员", "线索核验", "话术试探",
    )])


class TestActionPreviewAgent:
    def test_generates_valid_preview_result(self):
        mock = MockLLM()
        mock.set_responses([_build_response()])

        result = ActionPreviewAgent(mock.build_gateway()).generate(
            current_state=_make_state(),
            core_memory=_make_core_memory(),
            recent_summaries=[],
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
        recent_summaries=[],
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
