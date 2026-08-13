from scripts.replay_scene_facts_presence import (
    _evaluate_presence,
    _expected_groups,
)


def test_exact_presence_match_passes():
    expected = _expected_groups({"林舟": "appeared"}, None)
    result = _evaluate_presence(
        expected,
        [{"primary_surface": "林舟", "presence": "appeared"}],
    )
    assert result["presence_matches"] == 1
    assert result["presence_total"] == 1
    assert result["presence_mismatches"] == []
    assert result["unexpected_mentions"] == []


def test_alias_surface_does_not_false_mismatch():
    scene_facts = {
        "npc_mentions": [
            {
                "primary_surface": "沈孤鸿",
                "alias_relations": [
                    {"surface": "沈二爷", "basis": "same_scene_coreference"},
                ],
            }
        ]
    }
    expected = _expected_groups({"沈孤鸿": "appeared"}, scene_facts)
    result = _evaluate_presence(
        expected,
        [{"primary_surface": "沈二爷", "presence": "appeared"}],
    )
    assert result["presence_mismatches"] == []
    assert result["unexpected_mentions"] == []


def test_mentioned_only_mismatch_reported():
    expected = _expected_groups({"苏念薇": "mentioned_only"}, None)
    result = _evaluate_presence(
        expected,
        [{"primary_surface": "苏念薇", "presence": "appeared"}],
    )
    assert result["presence_mismatches"] == [{
        "surface": "苏念薇",
        "expected": "mentioned_only",
        "got": "appeared",
    }]
    assert result["presence_matches"] == 0


def test_unexpected_mention_flagged():
    expected = _expected_groups({"秦霜": "appeared"}, None)
    result = _evaluate_presence(
        expected,
        [
            {"primary_surface": "秦霜", "presence": "appeared"},
            {"primary_surface": "萧云痕", "presence": "appeared"},
        ],
    )
    assert result["presence_mismatches"] == []
    assert result["unexpected_mentions"] == [{
        "primary_surface": "萧云痕",
        "presence": "appeared",
    }]


def test_normalized_duplicate_mention_appeared_wins():
    expected = _expected_groups({"沈孤鸿": "appeared"}, None)
    result = _evaluate_presence(
        expected,
        [
            {"primary_surface": "沈 孤 鸿", "presence": "mentioned_only"},
            {"primary_surface": "沈孤鸿", "presence": "appeared"},
        ],
    )
    assert result["presence_mismatches"] == []
    assert result["unexpected_mentions"] == []
