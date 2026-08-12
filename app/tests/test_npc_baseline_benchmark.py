import json

import pytest

from scripts.benchmark_npc_baseline import (
    DEFAULT_BASELINE,
    DEFAULT_FIXTURES,
    DEFAULT_RECORDINGS,
    _fixtures_fingerprint,
    _replay_trace,
    _recordings_fingerprint,
    compare_reports,
    load_fixtures,
    load_recordings,
    main,
    run_suite,
)


@pytest.fixture(scope="module")
def benchmark_report():
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    recordings = load_recordings(DEFAULT_RECORDINGS)
    return run_suite(
        fixtures,
        recordings,
        backend="legacy",
        fixtures_sha256=_fixtures_fingerprint(DEFAULT_FIXTURES),
        recordings_sha256=_recordings_fingerprint(DEFAULT_RECORDINGS),
    )


def test_npc_baseline_benchmark_is_deterministic(benchmark_report):
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    recordings = load_recordings(DEFAULT_RECORDINGS)
    repeated = run_suite(
        fixtures,
        recordings,
        backend="legacy",
        fixtures_sha256=_fixtures_fingerprint(DEFAULT_FIXTURES),
        recordings_sha256=_recordings_fingerprint(DEFAULT_RECORDINGS),
    )

    assert repeated == benchmark_report


def test_npc_baseline_benchmark_matches_accepted_baseline(benchmark_report):
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    lines, regression = compare_reports(benchmark_report, baseline)

    assert not regression
    assert benchmark_report == baseline
    assert len(lines) == len(benchmark_report["cases"])


def test_npc_baseline_benchmark_captures_current_quality_signals(benchmark_report):
    cases = {
        case["case_id"]: case
        for case in benchmark_report["cases"]
    }

    assert benchmark_report["aggregate"]["weighted_passed"] == 144
    assert benchmark_report["aggregate"]["weighted_total"] == 153
    assert benchmark_report["aggregate"]["forbidden_violation_count"] == 2
    assert benchmark_report["aggregate"]["invariant_violation_count"] == 0
    assert benchmark_report["aggregate"]["call_count"] == 33

    exact = cases["exact_canonical_and_alias"]
    assert exact["call_count"] == 2
    assert exact["exact_mentions"] == [
        ["npc_001", "沈孤鸿"],
        ["npc_001", "沈二"],
        ["npc_001", "沈二爷"],
    ]
    assert exact["field_deltas"] == {"npc_001": {"last_known_location": "醉花楼"}}

    snapshot = cases["current_fields_snapshot"]
    assert snapshot["field_deltas"] == {
        "npc_001": {
            "last_known_location": "醉花楼大堂",
            "current_intent": "查明信物来历",
        }
    }
    assert snapshot["gold"]["forbidden_violations"] == []

    attitude = cases["attitude_change_explicit"]
    assert attitude["field_deltas"]["npc_001"]["attitude_to_player"] == "信任"

    near = cases["near_name_no_alias"]
    assert near["new_profile_names"] == ["陆川"]
    assert near["field_deltas"] == {"npc_001": {"last_known_location": "茶馆"}}

    org_place = cases["org_place_ignored"]
    assert org_place["call_count"] == 1
    assert org_place["extract_mentions"] == []
    assert org_place["candidates"] == []
    assert org_place["gold"]["forbidden_violations"] == []

    protected = cases["player_identity_protected"]
    assert protected["new_profile_names"] == []
    # Known gap: the player is offered as a pseudo-option, so the model
    # resolves it as "existing" (dropped by validation) instead of "ignore".
    assert protected["gold"]["weighted_passed"] == 0

    emotion = cases["transient_emotion_not_recorded"]
    assert emotion["field_deltas"] == {"npc_001": {"last_known_location": "醉花楼"}}
    assert "attitude_to_player" not in emotion["field_deltas"]["npc_001"]
    assert "current_intent" not in emotion["field_deltas"]["npc_001"]

    ability = cases["ability_append_dedup_cap"]
    assert "known_abilities" not in ability["field_deltas"].get("npc_001", {})
    assert ability["gold"]["forbidden_violations"] == []
    # Known gap: the model maps "隐匿身法" onto the existing "轻功" and never
    # records the evidenced new ability, so the strict final set fails.
    assert ability["gold"]["weighted_passed"] == 0

    stable = cases["stable_fields_with_evidence"]
    # Known gap: the model writes role+faction but not public_summary.
    assert stable["gold"]["weighted_passed"] == 6

    alias_fixed = cases["alias_fixed_title"]
    assert alias_fixed["aliases_added"] == {"npc_001": ["沈二爷"]}
    assert "public_summary" in alias_fixed["field_deltas"]["npc_001"]

    appearance = cases["stable_appearance_with_evidence"]
    assert appearance["field_deltas"] == {
        "npc_001": {"appearance": "左眼有一条旧疤，据说是少年时留下的。"}
    }

    companion = cases["companion_is_distinct"]
    assert companion["aliases_added"] == {}
    assert companion["gold"]["forbidden_violations"] == []

    roster = cases["large_roster"]
    assert roster["prompt_metrics"]["npc_profile_update"]["roster_total"] == 110
    assert roster["prompt_metrics"]["npc_profile_update"]["existing_options"] == 111
    assert roster["exact_mentions"] == [["npc000", "npc000"]]

    mixed = cases["mixed_turn"]
    assert mixed["new_profile_names"] == ["陆川"]
    assert mixed["field_deltas"]["npc_001"]["role"] == "合欢宗宗主"
    assert mixed["field_deltas"]["npc_002"]["last_known_location"] == "醉花楼"
    # Policy B: the title may resolve but must not be persisted as an alias,
    # and an inferred current_intent must not be written. Both are known gaps.
    assert mixed["gold"]["forbidden_violations"] == [
        "forbidden alias added: npc_001 + 合欢宗宗主",
        "forbidden field change: npc_001.current_intent",
    ]


def test_npc_baseline_benchmark_check_command(capsys):
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "aggregate:" in output
    assert "quality regression detected" not in output


def _trace_entry(trace_id: str, task: str, scene_facts: str) -> dict:
    return {
        "trace_id": trace_id,
        "task": task,
        "schema_valid": True,
        "parsed_output": (
            {"mentions": []} if task == "npc_profile_extract" else {"results": []}
        ),
        "input_data": {"scene_facts_json": scene_facts},
        "token_usage": {},
    }


def test_replay_trace_rejects_stale_input(tmp_path):
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    trace = tmp_path / "stale.jsonl"
    trace.write_text(
        json.dumps(_trace_entry("bench_aaaaaaaaaaaa_exact_canonical_and_alias", "npc_profile_extract", "stale"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="stale"):
        _replay_trace(trace, fixtures, only={"exact_canonical_and_alias"})


def test_replay_trace_rejects_multiple_runs(tmp_path):
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    case = next(
        case for case in fixtures.cases if case.case_id == "exact_canonical_and_alias"
    )
    trace = tmp_path / "multi.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(
                _trace_entry(f"bench_{token}_exact_canonical_and_alias", "npc_profile_extract", case.scene_facts),
                ensure_ascii=False,
            )
            for token in ("aaaaaaaaaaaa", "bbbbbbbbbbbb")
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="runs"):
        _replay_trace(trace, fixtures, only={"exact_canonical_and_alias"})


def test_replay_trace_rejects_missing_case(tmp_path):
    fixtures = load_fixtures(DEFAULT_FIXTURES)
    trace = tmp_path / "missing.jsonl"
    trace.write_text(
        json.dumps(_trace_entry("bench_aaaaaaaaaaaa_large_roster", "npc_profile_extract", "{}"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing cases"):
        _replay_trace(trace, fixtures, only={"exact_canonical_and_alias"})
