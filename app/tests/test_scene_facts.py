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
        "npc_mentions": [],
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
    result = {"primary_surface": "沈孤鸿", "evidence_fact_ids": ["f1"],
              "evidence_paragraph_ids": ["turn_12_p001"]}
    result.update(overrides)
    return result


@pytest.mark.parametrize("evidence", [
    [],
    [_mention(evidence_fact_ids=[], evidence_paragraph_ids=["turn_99_p999"])],
    [_mention(primary_surface="陌生人")],
])
def test_scene_fact_extractor_retains_used_identity_when_evidence_missing(evidence):
    gateway = _SpyGateway({
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": evidence,
    })
    result = SceneFactExtractor(gateway).extract(SceneFactExtractInput(
        scene_text="他接过印信，正式成为醉花楼主。",
        current_state=_state(), core_memory=CoreMemory(items=[]),
        used_npcs=[{"name": "沈孤鸿", "surfaces": ["楼主"], "presence": "mentioned_only"}],
        scene_paragraphs=[SceneParagraphInput(document_id="turn_12_p001", text="他接过印信。")],
    ))
    assert [fact.fact_id for fact in result.facts] == ["f1"]
    assert len(result.npc_mentions) == 1
    assert result.npc_mentions[0].primary_surface == "沈孤鸿"
    assert result.npc_mentions[0].presence == "mentioned_only"
    assert result.npc_mentions[0].surfaces == ["楼主"]
    assert result.npc_mentions[0].evidence_paragraph_ids == []
    assert result.degraded == bool(evidence and evidence[0]["primary_surface"] == "沈孤鸿")


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
                used_npcs=[{"name": "沈孤鸿", "presence": "appeared", "surfaces": []}],
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












@pytest.mark.parametrize("valid_fact", [True, False])
def test_scene_fact_extractor_sends_used_profiles_and_finds_pronoun_evidence(valid_fact):
    gateway = _SpyGateway({
        "scene_focus": "掌柜为主角称药。",
        "facts": [{"fact": "孙百草为主角称药。"}] if valid_fact else [{}],
        "npc_mentions": [{"primary_surface": "孙百草", "evidence_fact_ids": ["f1"],
                          "evidence_paragraph_ids": ["turn_1_p001", "turn_1_p002"]}],
    })
    used = [{"name": "孙百草", "role": "百草堂掌柜", "appearance": "清瘦老者",
             "personality_trait": "严谨和善", "surfaces": ["老掌柜"], "presence": "appeared"}]
    result = SceneFactExtractor(gateway).extract(SceneFactExtractInput(
        scene_text="老掌柜接待你。\n\n他拿出小秤称药。", current_state=_state(),
        core_memory=CoreMemory(items=[]), used_npcs=used,
        scene_paragraphs=[SceneParagraphInput(document_id=f"turn_1_p{i:03d}", text=text)
                          for i, text in enumerate(["老掌柜接待你。", "他拿出小秤称药。"], 1)],
    ))
    data = gateway.calls[0]["input_data"]
    assert json.loads(data["used_npcs_json"]) == used
    schema = json.loads(data["output_schema_json"])
    assert set(schema["$defs"]["SceneNpcEvidence"]["properties"]) == {
        "primary_surface", "evidence_fact_ids", "evidence_paragraph_ids",
    }
    assert result.npc_mentions[0].presence == "appeared"
    assert result.degraded == (not valid_fact)
    assert result.npc_mentions[0].evidence_fact_ids == (["f1"] if valid_fact else [])
    assert result.npc_mentions[0].evidence_paragraph_ids == ["turn_1_p001", "turn_1_p002"]
