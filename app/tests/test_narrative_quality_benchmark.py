from pathlib import Path
import pytest

from scripts.benchmark_narrative_quality import (
    DEFAULT_FIXTURES,
    CaseRecording,
    InvariantCheckResult,
    JudgeScore,
    NarrativeTestCase,
    check_invariants,
    load_fixtures,
)


def test_narrative_fixtures_load_and_validate():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    assert len(fixtures) == 10

    case_ids = [c.case_id for c in fixtures]
    assert len(case_ids) == len(set(case_ids))

    expected_categories = {
        "tavern_inquiry",
        "rogue_recruitment",
        "black_market_trade",
        "secret_investigation",
        "faction_confrontation",
        "dungeon_puzzle",
        "inn_rest_overhear",
        "moral_dilemma",
        "failed_action_recovery",
        "high_stakes_negotiation",
    }
    found_categories = {c.category for c in fixtures}
    assert found_categories == expected_categories


def test_invariant_checker_on_valid_output():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case1 = fixtures[0]

    valid_output = {
        "planning_reasoning": "根据判定，萧欢成功打听到了关于妖兽精血的消息。",
        "must_establish_facts": ["孙掌柜告知了关于精血的流向"],
        "must_not_establish_facts": ["不得否认成功结果"],
        "information_boundaries": ["孙掌柜只知道黑市流向，不知道幕后买家"],
        "scene_stopping_point": "孙掌柜说完线索，低头擦拭茶具，等待萧欢的回应。",
        "planned_events": [
            {
                "event_id": "e1",
                "event_fact": "孙掌柜收下灵石并告知萧欢黑市中有二阶雷纹豹精血的消息。",
                "linked_outcome_ids": ["act_01"],
                "involved_entities": ["孙掌柜"],
            }
        ],
        "scene_reasoning": "正文落实规划事实与对白。",
        "scene_text": "孙掌柜不动声色地将灵石扫入袖中，凑近低语道：“雷纹豹精血确实有，昨夜黑市刚到的货，就在落雁渡西巷的赵铁匠铺子后院。”",
        "closing_prompt": "得知了精血下落，你会怎么做？",
        "next_action_directions": [
            {
                "direction": "前往西巷赵铁匠铺探查",
                "rationale": "直接追查精血货物",
                "anchors": ["西巷", "赵铁匠铺"],
            }
        ],
    }

    inv = check_invariants(case1, valid_output)
    assert inv.passed is True
    assert inv.schema_valid is True
    assert inv.all_outcomes_linked is True
    assert inv.stopping_point_respected is True
    assert inv.no_forbidden_phrases is True


def test_invariant_checker_catches_missing_outcomes_and_system_leaks():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case1 = fixtures[0]

    # Missing linked_outcome_ids and leaked system phrase
    bad_output = {
        "planning_reasoning": "根据判定测试。",
        "must_establish_facts": ["事实1"],
        "must_not_establish_facts": [],
        "information_boundaries": [],
        "scene_stopping_point": "停点测试。",
        "planned_events": [
            {
                "event_id": "e1",
                "event_fact": "事件事实",
                "linked_outcome_ids": [],  # Missing act_01
                "involved_entities": [],
            }
        ],
        "scene_reasoning": "审计测试。",
        "scene_text": "根据 CoreMemory 的设定，掌柜说话了。",
        "closing_prompt": "继续？",
        "next_action_directions": [],
    }

    inv = check_invariants(case1, bad_output)
    assert inv.passed is False
    assert inv.all_outcomes_linked is False
    assert inv.no_forbidden_phrases is False
    assert any("Missing linked outcomes" in d for d in inv.details)
    assert any("System term 'CoreMemory' leaked" in d for d in inv.details)
