import json
from pathlib import Path

import pytest

from scripts.benchmark_npc_baseline import (
    _fixtures_fingerprint,
    _finalize_report,
    _expected_batch_input,
    _replay_trace,
    _recordings_fingerprint,
    compare_reports,
    load_fixtures,
    load_recordings,
    main,
    run_suite,
)

BATCH_DIR = Path("benchmarks/npc_baseline")
BATCH_FIXTURES = BATCH_DIR / "fixtures_batch.json"
BATCH_RECORDINGS = BATCH_DIR / "recordings" / "batch.json"
BATCH_BASELINE = BATCH_DIR / "baseline_batch.json"
LAST_SCENE_FIXTURES = BATCH_DIR / "cases_last_scene_pending.json"
LAST_SCENE_RECORDINGS = BATCH_DIR / "recordings" / "last_scene_pending.json"


def _report():
    fixtures = load_fixtures(BATCH_FIXTURES)
    recordings = load_recordings(BATCH_RECORDINGS)
    return run_suite(
        fixtures,
        recordings,
        fixtures_sha256=_fixtures_fingerprint(BATCH_FIXTURES),
        recordings_sha256=_recordings_fingerprint(BATCH_RECORDINGS),
    )


@pytest.fixture(scope="module")
def benchmark_report():
    return _report()


def test_npc_baseline_benchmark_is_deterministic(benchmark_report):
    assert _report() == benchmark_report


def test_npc_baseline_benchmark_matches_accepted_baseline(benchmark_report):
    baseline = json.loads(BATCH_BASELINE.read_text(encoding="utf-8"))
    lines, regression = compare_reports(benchmark_report, baseline)

    assert not regression
    assert benchmark_report == baseline
    assert len(lines) == len(benchmark_report["cases"])


def test_npc_baseline_benchmark_captures_quality_signals(benchmark_report):
    cases = {case["case_id"]: case for case in benchmark_report["cases"]}

    assert benchmark_report["aggregate"]["weighted_passed"] == 153
    assert benchmark_report["aggregate"]["weighted_total"] == 159
    assert benchmark_report["aggregate"]["forbidden_violation_count"] == 0
    assert benchmark_report["aggregate"]["invariant_violation_count"] == 0
    assert benchmark_report["aggregate"]["call_count"] == 12
    assert benchmark_report["aggregate"]["provider_attempts"] == 14

    assert cases["new_persistent_vs_ignore_oneoff"]["call_count"] == 0
    assert cases["player_identity_protected"]["call_count"] == 0
    assert cases["org_place_ignored"]["call_count"] == 0

    exact = cases["exact_canonical_and_alias"]
    assert exact["exact_mentions"] == [
        ["npc_001", "沈孤鸿"],
        ["npc_001", "沈二"],
    ]
    assert exact["field_deltas"]["npc_001"]["last_known_location"] == "醉花楼"

    snapshot = cases["current_fields_snapshot"]
    assert snapshot["field_deltas"] == {
        "npc_001": {
            "last_known_location": "醉花楼",
            "current_intent": "查明信物来历",
        }
    }

    near = cases["near_name_no_alias"]
    assert near["new_profile_names"] == ["陆川"]

    alias_fixed = cases["alias_fixed_title"]
    assert alias_fixed["aliases_added"] == {"npc_001": ["沈二爷"]}

    companion = cases["companion_is_distinct"]
    assert companion["aliases_added"] == {}

    roster = cases["large_roster"]
    assert roster["prompt_metrics"]["npc_batch_update"]["resolved_npcs"] == 1
    assert roster["exact_mentions"] == [["npc000", "npc000"]]

    # Remaining 9 points are exact-string scoring against the ideal gold:
    # - stable_fields: role/faction exact; public_summary written as
    #   "沈孤鸿，合欢宗宗主。" instead of the evidenced fuller sentence.
    assert cases["stable_fields_with_evidence"]["gold"]["weighted_passed"] == 9
    # - appearance truncated from the evidenced full sentence.
    assert cases["stable_appearance_with_evidence"]["field_deltas"] == {
        "npc_001": {
            "last_known_location": "醉花楼",
            "appearance": "左眼有一条旧疤",
        }
    }
    # Per-turn batch updates cannot mutate curated abilities while admission is frozen.
    ability = cases["ability_append_dedup_cap"]
    assert ability["field_deltas"]["npc_001"].get("known_abilities") is None
    assert "invalid_stable_change" in {
        warning[2] for warning in ability["warnings"]
    }
    # Per-turn batch updates cannot overwrite curated personality traits.
    personality = cases["stable_personality_with_evidence"]
    assert personality["field_deltas"]["npc_001"].get("personality_trait") is None
    assert "invalid_stable_change" in {
        warning[2] for warning in personality["warnings"]
    }
    # - mixed_turn: role+faction+public_summary linkage now works end to end.
    assert cases["mixed_turn"]["gold"]["weighted_passed"] == 42
    assert cases["mixed_turn"]["field_deltas"]["npc_001"]["role"] == "合欢宗宗主"
    assert cases["mixed_turn"]["field_deltas"]["npc_002"]["last_known_location"] == "醉花楼"


def test_npc_baseline_benchmark_check_command(capsys):
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "aggregate:" in output
    assert "quality regression detected" not in output


def test_last_scene_pending_contract_is_deterministic_and_records_only_expected_gaps():
    fixtures = load_fixtures(LAST_SCENE_FIXTURES)
    recordings = load_recordings(LAST_SCENE_RECORDINGS)

    report = run_suite(
        fixtures,
        recordings,
        fixtures_sha256=_fixtures_fingerprint(LAST_SCENE_FIXTURES),
        recordings_sha256=_recordings_fingerprint(LAST_SCENE_RECORDINGS),
    )
    repeated = run_suite(
        fixtures,
        recordings,
        fixtures_sha256=_fixtures_fingerprint(LAST_SCENE_FIXTURES),
        recordings_sha256=_recordings_fingerprint(LAST_SCENE_RECORDINGS),
    )

    assert report == repeated
    assert len(report["cases"]) == 8
    aggregate = report["aggregate"]
    assert aggregate["weighted_passed"] == 111
    assert aggregate["weighted_total"] == 141
    assert aggregate["forbidden_violation_count"] == 0
    assert aggregate["invariant_violation_count"] == 0
    assert aggregate["categories"]["resolution"] == {"passed": 11, "total": 11}
    assert aggregate["categories"]["stable_field"] == {"passed": 2, "total": 2}
    assert aggregate["categories"]["npc_presence"] == {"passed": 11, "total": 11}
    assert aggregate["categories"]["last_scene_summary"] == {"passed": 0, "total": 9}
    assert aggregate["categories"]["last_scene_batch_scope"] == {
        "passed": 11,
        "total": 11,
    }
    assert aggregate["categories"]["evidence_warning"] == {"passed": 0, "total": 1}

    cases = {case["case_id"]: case for case in report["cases"]}
    mentioned = cases["mentioned_only_no_overwrite"]
    assert mentioned["last_scene_contract_signals"]["batch_npc_ids"] == [
        "npc-yao",
    ]
    assert mentioned["last_scene_contract_signals"]["scene_summaries"] == {}
    assert mentioned["gold"]["forbidden_violations"] == []

    # The current parser deliberately ignores the future nested scene_summary
    # key.  Phase 3 should turn only the second entry into a valid summary and
    # emit an invalid_scene_summary warning for the first.
    salvage = cases["per_npc_scene_summary_salvage"]
    assert salvage["warnings"] == []
    assert salvage["last_scene_contract_signals"]["scene_summaries"] == {}

    no_accumulation = next(
        case
        for case in fixtures.cases
        if case.case_id == "summary_does_not_accumulate"
    )
    projected = _expected_batch_input(no_accumulation)
    assert projected is not None
    entries = json.loads(projected["npc_batch_json"])
    assert entries[0]["name"] == "苏念薇"
    assert "last_scene_summary" not in entries[0]["current"]
    assert "last_scene_turn" not in entries[0]["current"]


def _trace_entry(trace_id: str, npc_batch_json: str) -> dict:
    return {
        "trace_id": trace_id,
        "task": "npc_batch_update",
        "schema_valid": True,
        "parsed_output": {"updates": []},
        "input_data": {"npc_batch_json": npc_batch_json},
        "token_usage": {},
    }


def test_replay_trace_rejects_stale_input(tmp_path):
    fixtures = load_fixtures(BATCH_FIXTURES)
    trace = tmp_path / "stale.jsonl"
    trace.write_text(
        json.dumps(
            _trace_entry("bench_aaaaaaaaaaaa_exact_canonical_and_alias", "stale"),
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="stale"):
        _replay_trace(trace, fixtures, only={"exact_canonical_and_alias"})


def test_replay_trace_rejects_multiple_runs(tmp_path):
    fixtures = load_fixtures(BATCH_FIXTURES)
    case = next(
        case for case in fixtures.cases if case.case_id == "exact_canonical_and_alias"
    )
    trace = tmp_path / "multi.jsonl"
    trace.write_text(
        "\n".join(
            json.dumps(
                _trace_entry(f"bench_{token}_exact_canonical_and_alias", "stale"),
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
    fixtures = load_fixtures(BATCH_FIXTURES)
    trace = tmp_path / "missing.jsonl"
    trace.write_text(
        json.dumps(_trace_entry("bench_aaaaaaaaaaaa_large_roster", "stale"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing cases"):
        _replay_trace(trace, fixtures, only={"exact_canonical_and_alias"})


def test_finalize_report_handles_missing_baseline(capsys, tmp_path):
    report = _report()
    missing = tmp_path / "missing.json"

    assert _finalize_report(report, missing, check=False) == 0
    assert _finalize_report(report, missing, check=True) == 1
    out = capsys.readouterr().out
    assert "no accepted baseline" in out
    assert "aggregate:" not in out


def test_finalize_report_compares_when_baseline_exists(tmp_path):
    report = _report()
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    assert _finalize_report(report, baseline_path, check=False) == 0
