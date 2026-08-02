import json
from pathlib import Path

from app.domain.narrative_canon_query import NarrativeCanonQueryAgent
from app.domain.npc_profile.schemas import NPCProfileDirectorView


class _SpyGateway:
    def __init__(self):
        self.call = None

    def run(self, **kwargs):
        self.call = kwargs
        return kwargs["output_schema"].model_validate({
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
        return kwargs["output_schema"].model_validate({
            "thought_process": "P001 只是本轮当下的环境变化，不需要查询。",
            "queries": [],
        })

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


def test_prompt_keeps_query_generation_separate_from_retrieval_and_checking():
    prompt = Path(
        "app/prompts/generation/narrative_canon_query_v1.md"
    ).read_text(encoding="utf-8")

    assert "不检索证据" in prompt
    assert "不判断陈述正确或错误" in prompt
    assert "不提出修改建议" in prompt
    assert "纯粹发生在本轮正文当下" in prompt
    assert "空列表是正常结果" in prompt
    assert "先规划、再输出查询" in prompt
    assert "最小可独立判真的历史命题" in prompt
    assert "只保留一个核心谓词" in prompt
    assert "即使多个动作发生在同一时间、地点" in prompt
    assert "必须拆分或只保留最关键的一个" in prompt
    assert "亚瑟拔出了在黑水镇买的铁剑" in prompt
    assert "亚瑟在黑水镇买过一把铁剑" in prompt
    assert "重新淬火、磨剑次数和之后如何携带" in prompt
