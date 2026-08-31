import json

import pytest

from app.core.llm.errors import LLMOutputValidationError, LLMProviderError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_facts import (
    SceneFactExtractInput,
    SceneFactExtractor,
    SceneFactSet,
    SceneParagraphInput,
)


class _SpyGateway:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        validated = kwargs["output_schema"].model_validate(self.response)
        output_validator = kwargs.get("output_validator")
        if output_validator is not None:
            try:
                output_validator(validated)
            except ValueError as exc:
                raise LLMOutputValidationError(str(exc)) from exc
        return validated


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="酒馆", participants=["卢西安"]),
        player=PlayerState(name="卢西安"),
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
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_1_p001",
                    text="卢西安进入酒馆。",
                )
            ],
        ),
        trace_id="trace",
    )

    input_data = gateway.calls[0]["input_data"]
    assert gateway.calls[0]["task"] == "scene_fact_extract"
    assert input_data["scene_text"] == "卢西安进入酒馆。"
    schema_properties = json.loads(input_data["output_schema_json"])["properties"]
    assert sorted(schema_properties) == [
        "facts",
        "npc_mentions",
        "scene_focus",
    ]
    assert "$ref" in schema_properties["facts"]["items"]
    assert "$ref" in schema_properties["npc_mentions"]["items"]
    assert result.scene_focus == "卢西安进入酒馆。"
    assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]
    assert result.facts[0].fact == "卢西安刚刚进入酒馆。"


def _mention(**overrides):
    base = {
        "primary_surface": "沈孤鸿",
        "presence": "appeared",
        "alias_relations": [],
        "evidence_fact_ids": ["f1"],
        "evidence_paragraph_ids": ["turn_12_p001"],
    }
    base.update(overrides)
    return base








def test_scene_fact_extractor_drops_mentions_with_unknown_paragraph_ids():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [
            _mention(
                evidence_fact_ids=[],
                evidence_paragraph_ids=["turn_99_p999"],
            ),
        ],
    }
    gateway = _SpyGateway(response)

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="沈孤鸿正式成为醉花楼主。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_12_p001",
                    text="他接过印信，正式成为醉花楼主。",
                )
            ],
        )
    )

    assert result.degraded is True
    assert [fact.fact_id for fact in result.facts] == ["f1"]
    assert result.npc_mentions == []


def test_scene_fact_extractor_keeps_facts_when_mentions_degrade():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [
            _mention(
                evidence_fact_ids=[],
                evidence_paragraph_ids=["turn_99_p999"],
            ),
        ],
    }
    gateway = _SpyGateway(response)

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="沈孤鸿正式成为醉花楼主。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_12_p001",
                    text="他接过印信，正式成为醉花楼主。",
                )
            ],
        )
    )

    assert result.degraded is True
    assert [fact.fact_id for fact in result.facts] == ["f1"]
    assert result.npc_mentions == []










def test_scene_fact_extractor_provider_failure_propagates():
    class _RetryThenProviderFailGateway:
        def __init__(self, response):
            self.response = response

        def run(self, **kwargs):
            validated = kwargs["output_schema"].model_validate(self.response)
            output_validator = kwargs.get("output_validator")
            try:
                output_validator(validated)
            except ValueError as exc:
                raise LLMProviderError(
                    f"Rate limit exceeded: 429 ({exc})"
                ) from exc

    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [
            _mention(
                evidence_fact_ids=[],
                evidence_paragraph_ids=["turn_99_p999"],
            ),
        ],
    }
    gateway = _RetryThenProviderFailGateway(response)

    with pytest.raises(LLMProviderError):
        SceneFactExtractor(gateway).extract(
            SceneFactExtractInput(
                scene_text="沈孤鸿正式成为醉花楼主。",
                current_state=_state(),
                core_memory=CoreMemory(items=[]),
                scene_paragraphs=[
                    SceneParagraphInput(
                        document_id="turn_12_p001",
                        text="他接过印信，正式成为醉花楼主。",
                    )
                ],
            )
        )












def _mention_list(count: int) -> list[dict]:
    return [
        {
            "primary_surface": f"人物{index}",
            "alias_relations": [],
            "evidence_fact_ids": ["f1"],
            "evidence_paragraph_ids": [],
        }
        for index in range(count)
    ]














def test_scene_fact_extractor_sends_staged_npcs_json():
    response = {
        "scene_focus": "主角进入药铺，向孙百草问诊抓药。",
        "facts": [
            {"fact": "孙百草在柜台后称量药材，并接待了主角。"},
        ],
        "npc_mentions": [
            {
                "primary_surface": "孙百草",
                "presence": "appeared",
                "alias_relations": [
                    {
                        "surface": "老掌柜",
                        "basis": "temporary_address",
                        "evidence_fact_ids": ["f1"],
                        "evidence_paragraph_ids": ["turn_1_p001"],
                    }
                ],
                "evidence_fact_ids": ["f1"],
                "evidence_paragraph_ids": ["turn_1_p001"],
            }
        ],
    }
    gateway = _SpyGateway(response)

    staged_npcs = [
        {
            "name": "孙百草",
            "role": "百草堂掌柜",
            "appearance": "清瘦老者，穿青布长衫",
            "personality_trait": "严谨和善",
            "guidance": "主角推门进店时正在柜台后称药。",
            "source": "spontaneous",
        }
    ]

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="主角推门走进百草堂，柜台后的老掌柜正用小秤称量药材。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_1_p001",
                    text="主角推门走进百草堂，柜台后的老掌柜正用小秤称量药材。",
                )
            ],
            staged_npcs=staged_npcs,
        ),
        trace_id="trace_test",
    )

    input_data = gateway.calls[0]["input_data"]
    assert "staged_director_npcs_json" in input_data
    parsed_staged = json.loads(input_data["staged_director_npcs_json"])
    assert parsed_staged[0]["name"] == "孙百草"
    assert parsed_staged[0]["appearance"] == "清瘦老者，穿青布长衫"
    assert len(result.npc_mentions) == 1
    assert result.npc_mentions[0].primary_surface == "孙百草"
    assert len(result.npc_mentions[0].alias_relations) == 1
    assert result.npc_mentions[0].alias_relations[0].surface == "老掌柜"
