import json
import pytest

from app.domain.narrative_canon_query import NarrativeCanonQueryAgent
from app.domain.npc_profile.schemas import NPCProfileDirectorView


class _SpyGateway:
    def __init__(self):
        self.call = None

    def run(self, **kwargs):
        self.call = kwargs
        if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
            kwargs["on_attempt_start"](1)
        result = kwargs["output_schema"].model_validate({
            "thought_process": "P002-P003 将埃利奥与灰烬图书馆描述为既往经历，需要回查。",
            "queries": [
                {
                    "paragraph_ids": ["P002", "P003"],
                    "claim": "埃利奥此前探索过灰烬图书馆。",
                    "claim_type": "location_or_presence",
                    "entities": ["埃利奥", "灰烬图书馆"],
                    "lookup_queries": [
                        "埃利奥此前是否到访或探索过灰烬图书馆？",
                        "埃利奥何时第一次得知灰烬图书馆？",
                    ],
                    "priority": "high",
                }
            ]
        })
        if "output_validator" in kwargs and kwargs["output_validator"] is not None:
            kwargs["output_validator"](result)
        return result


def test_agent_projects_only_numbered_prose_and_compact_character_catalog():
    gateway = _SpyGateway()
    result = NarrativeCanonQueryAgent(gateway).generate(
        scene_text="莱尔放下茶杯。\n\n埃利奥说自己去过灰烬图书馆。\n\n他在那里被落石挡住。",
        player_name="莱尔",
        npc_profiles=[
            NPCProfileDirectorView(
                name="埃利奥",
                aliases=["木偶师"],
                hidden_secret="这个秘密绝不能进入查询模块。",
            )
        ],
        trace_id="trace-query",
    )

    assert result.queries[0].query_id == "q1"
    assert result.queries[0].lookup_queries == [
        "埃利奥此前是否到访或探索过灰烬图书馆？",
        "埃利奥何时第一次得知灰烬图书馆？",
    ]
    assert "P002-P003" in result.thought_process
    input_data = gateway.call["input_data"]
    assert "[P001]" in input_data["numbered_scene_text"]
    assert "[P003]" in input_data["numbered_scene_text"]
    catalog = json.loads(input_data["known_entity_catalog_json"])
    assert catalog == [
        {"name": "莱尔", "aliases": [], "kind": "player"},
        {"name": "埃利奥", "aliases": ["木偶师"], "kind": "npc"},
    ]
    assert "秘密" not in input_data["known_entity_catalog_json"]


def test_agent_allows_no_queries_and_rejects_empty_scene():
    gateway = _SpyGateway()

    def _empty_result(**kwargs):
        gateway.call = kwargs
        if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
            kwargs["on_attempt_start"](1)
        result = kwargs["output_schema"].model_validate({
            "thought_process": "P001 只是本轮当下的环境变化，不需要查询。",
            "queries": [],
        })
        if "output_validator" in kwargs and kwargs["output_validator"] is not None:
            kwargs["output_validator"](result)
        return result

    gateway.run = _empty_result
    result = NarrativeCanonQueryAgent(gateway).generate(
        scene_text="风吹过窗沿。",
        player_name="莱尔",
        npc_profiles=[],
    )
    assert "不需要查询" in result.thought_process
    assert result.queries == []

    try:
        NarrativeCanonQueryAgent(gateway).generate(
            scene_text=" \n ",
            player_name="莱尔",
            npc_profiles=[],
        )
    except ValueError as exc:
        assert "non-empty scene prose" in str(exc)
    else:
        raise AssertionError("empty scene prose should fail")


def test_agent_provider_failure_propagates():
    from app.core.llm import MockLLM
    from app.core.llm.errors import LLMProviderError

    mock = MockLLM()
    mock.set_fail_provider(True)
    agent = NarrativeCanonQueryAgent(mock.build_gateway())

    with pytest.raises(LLMProviderError):
        agent.generate(
            scene_text="风吹过窗沿。",
            player_name="莱尔",
            npc_profiles=[],
        )
