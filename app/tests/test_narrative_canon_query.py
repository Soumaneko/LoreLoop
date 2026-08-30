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
    assert gateway.call["task"] == "narrative_canon_query"
    assert gateway.call["temperature"] == 0.2
    assert gateway.call["trace_id"] == "trace-query"
    input_data = gateway.call["input_data"]
    assert set(input_data) == {
        "numbered_scene_text",
        "known_entity_catalog_json",
        "output_schema_json",
    }
    assert "[P001]" in input_data["numbered_scene_text"]
    assert "[P003]" in input_data["numbered_scene_text"]
    catalog = json.loads(input_data["known_entity_catalog_json"])
    assert catalog == [
        {"name": "莱尔", "aliases": [], "kind": "player"},
        {"name": "埃利奥", "aliases": ["木偶师"], "kind": "npc"},
    ]
    assert "秘密" not in input_data["known_entity_catalog_json"]
    schema = json.loads(input_data["output_schema_json"])
    assert "query_id" not in json.dumps(schema, ensure_ascii=False)


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


def test_agent_first_invalid_retries_and_second_valid_subset_wins():
    from app.core.llm import MockLLM

    first = json.dumps({
        "thought_process": "第一轮。",
        "queries": [
            {
                "paragraph_ids": ["P001"],
                "claim": "埃利奥到过图书馆。",
                "claim_type": "location_or_presence",
                "entities": ["埃利奥"],
                "lookup_queries": ["埃利奥是否到过？"],
                "priority": "high",
            },
            {
                "paragraph_ids": ["P001"],
                "claim": "",
                "claim_type": "other",
                "entities": [],
                "lookup_queries": [],
                "priority": "low",
            },
        ],
    }, ensure_ascii=False)
    second = json.dumps({
        "thought_process": "第二轮。",
        "queries": [
            {
                "paragraph_ids": ["P001"],
                "claim": "埃利奥到过图书馆。",
                "claim_type": "location_or_presence",
                "entities": ["埃利奥"],
                "lookup_queries": ["埃利奥是否到过？"],
                "priority": "high",
            }
        ],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    agent = NarrativeCanonQueryAgent(mock.build_gateway())

    result = agent.generate(
        scene_text="埃利奥说自己去过灰烬图书馆。",
        player_name="莱尔",
        npc_profiles=[],
    )

    assert result.queries[0].claim == "埃利奥到过图书馆。"
    assert agent.last_warnings == []


def test_agent_reuse_first_preserves_second_dropped_warnings():
    from app.core.llm import MockLLM

    first = json.dumps({
        "thought_process": "第一轮。",
        "queries": [
            {
                "paragraph_ids": ["P001"],
                "claim": "埃利奥到过图书馆。",
                "claim_type": "location_or_presence",
                "entities": ["埃利奥"],
                "lookup_queries": ["埃利奥是否到过？"],
                "priority": "high",
            },
            {
                "paragraph_ids": ["P001"],
                "claim": "",
                "claim_type": "other",
                "entities": [],
                "lookup_queries": [],
                "priority": "low",
            },
        ],
    }, ensure_ascii=False)
    second = json.dumps({
        "thought_process": "",
        "queries": [{
            "paragraph_ids": ["P001"],
            "claim": "",
            "claim_type": "other",
            "entities": [],
            "lookup_queries": [],
            "priority": "low",
        }],
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([first, second])
    agent = NarrativeCanonQueryAgent(mock.build_gateway())

    result = agent.generate(
        scene_text="埃利奥说自己去过灰烬图书馆。",
        player_name="莱尔",
        npc_profiles=[],
    )

    assert result.queries[0].claim == "埃利奥到过图书馆。"
    assert any(
        warning.code == "canon_query_dropped"
        for warning in agent.last_warnings
    )


def test_agent_reports_query_count_dropped_on_salvage():
    from app.core.llm import MockLLM

    queries = [
        {
            "paragraph_ids": ["P001"],
            "claim": f"查询 {index}。",
            "claim_type": "other",
            "entities": [],
            "lookup_queries": [],
            "priority": "low",
        }
        for index in range(13)
    ]
    response = json.dumps({
        "thought_process": "超量查询。",
        "queries": queries,
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([response, response])
    agent = NarrativeCanonQueryAgent(mock.build_gateway())

    result = agent.generate(
        scene_text="埃利奥说自己去过灰烬图书馆。",
        player_name="莱尔",
        npc_profiles=[],
    )

    assert len(result.queries) == 12
    assert any(
        warning.code == "canon_query_dropped"
        and "keeping first 12" in warning.message
        for warning in agent.last_warnings
    )