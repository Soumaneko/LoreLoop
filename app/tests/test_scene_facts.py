import json
from pathlib import Path

import pytest

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
            output_validator(validated)
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
    assert result.scene_focus == "卢西安进入酒馆。"
    assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]
    assert result.facts[0].fact == "卢西安刚刚进入酒馆。"


def test_prompt_requires_explicit_time_progression_to_be_preserved():
    prompt = Path("app/prompts/confirmation/scene_fact_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "应当准确提取时间流逝" in prompt
    assert "不得压缩成只描述早餐、出发、抵达等事件而丢失时间变化" in prompt


def test_prompt_documents_positional_fact_ids():
    prompt = Path("app/prompts/confirmation/scene_fact_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "facts 数组第 0 条是 f1" in prompt
    assert "第 1 条是 f2" in prompt


def test_prompt_npc_mentions_rules_are_concise_and_consistent():
    prompt = Path("app/prompts/confirmation/scene_fact_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "不要把玩家写成 npc_mention" in prompt
    assert "不得与 primary_surface 相同" in prompt
    assert "不要记录“他”“她”“对方”“那人”等普通代词" in prompt
    assert "萧云痕" not in prompt.split("# 示例")[1]


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


def test_scene_fact_extractor_threads_npc_mentions():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [
            {"fact": "沈孤鸿正式成为醉花楼主。"},
        ],
        "npc_mentions": [
            _mention(alias_relations=[
                {
                    "surface": "沈二爷",
                    "basis": "explicit_fixed_alias",
                    "evidence_fact_ids": ["f1"],
                    "evidence_paragraph_ids": [],
                }
            ]),
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

    assert len(result.npc_mentions) == 1
    mention = result.npc_mentions[0]
    assert mention.primary_surface == "沈孤鸿"
    assert mention.evidence_fact_ids == ["f1"]
    assert mention.evidence_paragraph_ids == ["turn_12_p001"]
    assert mention.alias_relations[0].surface == "沈二爷"


def test_scene_fact_extractor_sends_scene_paragraphs():
    gateway = _SpyGateway({
        "scene_focus": "沈孤鸿正式成为醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [],
    })

    SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="他接过印信，正式成为醉花楼主。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_12_p001",
                    text="他接过印信，正式成为醉花楼主。",
                ),
            ],
        )
    )

    input_data = gateway.calls[0]["input_data"]
    assert json.loads(input_data["scene_paragraphs_json"]) == [
        {"document_id": "turn_12_p001", "text": "他接过印信，正式成为醉花楼主。"}
    ]


def test_scene_fact_draft_rejects_out_of_range_fact_references():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="out-of-range"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=[_mention(evidence_fact_ids=["f2"])],
        )


def test_scene_fact_extractor_rejects_unknown_paragraph_ids():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [
            _mention(evidence_paragraph_ids=["turn_99_p999"]),
        ],
    }
    gateway = _SpyGateway(response)

    with pytest.raises(ValueError, match="unknown paragraph ids"):
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


def test_scene_fact_draft_rejects_over_twelve_mentions():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="at most 12 items"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=_mention_list(13),
        )


def test_scene_fact_draft_rejects_duplicate_paragraph_evidence():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="must not repeat"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=[
                _mention(evidence_paragraph_ids=["turn_12_p001", "turn_12_p001"]),
            ],
        )


def test_scene_fact_draft_rejects_duplicate_fact_evidence():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="must not repeat"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=[
                _mention(evidence_fact_ids=["f1", "f1"]),
            ],
        )


def test_scene_fact_draft_rejects_mention_without_evidence():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="at least one evidence"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=[
                _mention(evidence_fact_ids=[], evidence_paragraph_ids=[]),
            ],
        )


def test_scene_fact_set_rejects_over_twelve_mentions():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactSet

    with pytest.raises(ValidationError, match="at most 12 items"):
        SceneFactSet(
            scene_focus="测试",
            facts=[{"fact_id": "f1", "fact": "只有一条事实。"}],
            npc_mentions=_mention_list(13),
        )


def test_scene_fact_draft_rejects_relation_surface_equals_primary():
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import SceneFactDraftSet

    with pytest.raises(ValidationError, match="must not equal the primary_surface"):
        SceneFactDraftSet(
            scene_focus="测试",
            facts=[{"fact": "只有一条事实。"}],
            npc_mentions=[
                {
                    "primary_surface": "沈孤鸿",
                    "presence": "appeared",
                    "alias_relations": [
                        {
                            "surface": "沈孤鸿",
                            "basis": "explicit_fixed_alias",
                            "evidence_fact_ids": ["f1"],
                            "evidence_paragraph_ids": [],
                        }
                    ],
                    "evidence_fact_ids": ["f1"],
                    "evidence_paragraph_ids": [],
                }
            ],
        )


@pytest.mark.parametrize("surface", ["他", "她", "对方", "那人"])
def test_scene_npc_mentions_reject_generic_pronouns(surface):
    from pydantic import ValidationError

    from app.domain.scene_facts.schemas import AliasRelation, SceneNpcMention

    with pytest.raises(ValidationError, match="identity-bearing surface"):
        SceneNpcMention(
            primary_surface=surface,
            presence="appeared",
            evidence_fact_ids=["f1"],
        )
    with pytest.raises(ValidationError, match="identity-bearing surface"):
        AliasRelation(
            surface=surface,
            basis="same_scene_coreference",
            evidence_fact_ids=["f1"],
        )
