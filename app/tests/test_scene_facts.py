import json
from pathlib import Path

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


def test_scene_fact_extractor_returns_empty_for_empty_string_text():
    gateway = _SpyGateway({})

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="",
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


def test_scene_fact_extractor_keeps_mentions_when_facts_degrade():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": ""}],
        "npc_mentions": [
            _mention(
                evidence_fact_ids=[],
                evidence_paragraph_ids=["turn_12_p001"],
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
    assert result.facts == []
    assert len(result.npc_mentions) == 1
    assert result.npc_mentions[0].primary_surface == "沈孤鸿"
    assert result.npc_mentions[0].evidence_paragraph_ids == ["turn_12_p001"]


def test_scene_fact_extractor_empties_when_both_components_fail():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": ""}],
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
    assert result.facts == []
    assert result.npc_mentions == []


def test_scene_fact_extractor_strips_fact_refs_when_facts_degrade():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": ""}],
        "npc_mentions": [
            _mention(
                evidence_fact_ids=["f1"],
                evidence_paragraph_ids=["turn_12_p001"],
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
    assert result.facts == []
    assert len(result.npc_mentions) == 1
    assert result.npc_mentions[0].evidence_fact_ids == []
    assert result.npc_mentions[0].evidence_paragraph_ids == ["turn_12_p001"]


def test_scene_fact_extractor_reports_cleaned_invalid_evidence_as_degraded():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [
            _mention(
                evidence_paragraph_ids=["turn_12_p001", "turn_99_p999"],
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
    assert len(result.npc_mentions) == 1
    assert result.npc_mentions[0].evidence_paragraph_ids == ["turn_12_p001"]


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


def test_scene_fact_extractor_rejects_missing_top_level_fields():
    gateway = _SpyGateway({})

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
    assert result.facts == []
    assert result.npc_mentions == []


def test_scene_fact_extractor_rejects_over_twelve_facts_after_retry():
    response = {
        "scene_focus": "测试",
        "facts": [{"fact": f"事实{i}"} for i in range(13)],
        "npc_mentions": [],
    }
    gateway = _SpyGateway(response)

    result = SceneFactExtractor(gateway).extract(
        SceneFactExtractInput(
            scene_text="测试正文",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_paragraphs=[
                SceneParagraphInput(
                    document_id="turn_1_p001",
                    text="测试正文",
                )
            ],
        )
    )

    assert result.degraded is True
    assert len(result.facts) == 12


def test_scene_fact_extractor_rejects_extra_top_level_fields():
    response = {
        "scene_focus": "沈孤鸿接任醉花楼主。",
        "facts": [{"fact": "沈孤鸿正式成为醉花楼主。"}],
        "npc_mentions": [],
        "unexpected": True,
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


def test_scene_fact_extractor_rejects_nonempty_text_without_paragraphs():
    with pytest.raises(ValueError, match="requires scene_paragraphs"):
        SceneFactExtractor(_SpyGateway({})).extract(
            SceneFactExtractInput(
                scene_text="沈孤鸿正式成为醉花楼主。",
                current_state=_state(),
                core_memory=CoreMemory(items=[]),
            )
        )


def test_scene_fact_extractor_rejects_duplicate_paragraph_ids():
    with pytest.raises(ValueError, match="document_id values must be unique"):
        SceneFactExtractor(_SpyGateway({})).extract(
            SceneFactExtractInput(
                scene_text="沈孤鸿正式成为醉花楼主。",
                current_state=_state(),
                core_memory=CoreMemory(items=[]),
                scene_paragraphs=[
                    SceneParagraphInput(
                        document_id="turn_12_p001",
                        text="第一段",
                    ),
                    SceneParagraphInput(
                        document_id="turn_12_p001",
                        text="第二段",
                    ),
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


def test_prompt_staged_director_npcs_rules_and_alignment():
    prompt = Path("app/prompts/confirmation/scene_fact_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "## StagedDirectorNPCs" in prompt
    assert "{staged_director_npcs_json}" in prompt
    assert "导演本轮引入角色对齐" in prompt
    assert "primary_surface" in prompt
    assert "身份解析" in prompt


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


def test_extract_staged_npc_materials_extracts_from_materials_only():
    from app.domain.director.schemas import DirectorStageMaterial, DirectorStageResult
    from app.graph.confirmation_nodes import _extract_staged_npc_materials

    class MockPending:
        def __init__(self, materials):
            self.director_stage = DirectorStageResult(reasoning="引入角色与披露升级", materials=materials)

    materials = [
        DirectorStageMaterial(
            kind="npc",
            resource_id="spontaneous_npc_1",
            payload={
                "name": "孙百草",
                "role": "药铺掌柜",
                "appearance": "清瘦老者",
                "personality_trait": "严谨",
                "source": "spontaneous",
            },
            guidance="在柜台后称药。",
        ),
        DirectorStageMaterial(
            kind="npc",
            resource_id="director_npc_zhou_01",
            payload={
                "name": "周行之",
                "role": "灵材商人",
                "appearance": "富态商人",
                "personality_trait": "精明",
                "source": "director_npc",
            },
            guidance="在路边摊位叫卖。",
        ),
        DirectorStageMaterial(
            kind="npc",
            resource_id="npc_gu_01",
            payload={
                "name": "顾晚棠",
                "role": "同门师姐",
                "source": "npc_profile",
                "disclosure_upgrade": True,
                "new_disclosure_level": "intimate",
            },
            guidance="披露深度秘密。",
        ),
        DirectorStageMaterial(
            kind="event",
            resource_id="event_1",
            payload={"event_name": "闹市喧哗"},
            guidance="街上传来喧哗。",
        ),
    ]

    pending = MockPending(materials)
    staged = _extract_staged_npc_materials(pending)

    assert len(staged) == 2
    assert staged[0]["name"] == "孙百草"
    assert staged[0]["source"] == "spontaneous"
    assert staged[1]["name"] == "周行之"
    assert staged[1]["source"] == "director_npc"
    # 验证严格排除了 upgrade_disclosure (source == "npc_profile")
    assert all(item["name"] != "顾晚棠" for item in staged)


def test_project_scene_text_with_mixed_staged_npcs_preserves_both():
    from app.domain.director.schemas import DirectorStageMaterial, DirectorStageResult
    from app.domain.director_npc.schemas import DirectorNPCDraft
    from app.graph.confirmation_helpers import project_scene_text_with_staged_director_npc

    class MockPending:
        def __init__(self, materials):
            self.director_stage = DirectorStageResult(reasoning="混合引入", materials=materials)

    materials = [
        DirectorStageMaterial(
            kind="npc",
            resource_id="spontaneous_npc_sun_01",
            payload={
                "name": "孙百草",
                "role": "药铺掌柜",
                "source": "spontaneous",
            },
            guidance="在柜台后称药。",
        ),
        DirectorStageMaterial(
            kind="npc",
            resource_id="director_npc_zhou_01",
            payload={
                "name": "周行之",
                "role": "灵材商人",
                "source": "director_npc",
            },
            guidance="在路边摊叫卖。",
        ),
    ]

    confirmed_drafts = [
        DirectorNPCDraft(
            draft_id="director_npc_zhou_01",
            name="周行之",
            role="灵材商人",
            appearance="富态商人",
            personality_trait="精明",
            core_motivation="逐利",
            hidden_secret="倒卖禁药",
            conflict_style="息事宁人",
            social_style="圆滑",
            status="introduced",
            created_turn=1,
        )
    ]

    pending = MockPending(materials)
    annotated = project_scene_text_with_staged_director_npc(
        "正文开始。",
        pending=pending,
        confirmed_director_npc_drafts=confirmed_drafts,
    )

    assert "孙百草" in annotated
    assert "周行之" in annotated
    assert "spontaneous_npc_sun_01" in annotated
    assert "director_npc_zhou_01" in annotated
