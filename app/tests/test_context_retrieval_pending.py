"""Pending last-scene contracts that are not part of accepted baselines."""

from pathlib import Path

from scripts.benchmark_context_retrieval import load_suite, run_suite
from scripts.benchmark_npc_baseline import (
    _fixtures_fingerprint,
    _recordings_fingerprint,
    load_fixtures,
    load_recordings,
    run_suite as run_npc_suite,
)


def test_cases_last_scene_pending_contracts():
    suite = load_suite(Path("benchmarks/context_retrieval/cases_last_scene_pending.json"))
    result = run_suite(suite, fingerprint="pending-check")

    # All must groups must be recalled, no forbidden, no invariant violations
    assert result["aggregate"]["must_recalled"] == result["aggregate"]["must_total"] == 3
    assert result["aggregate"]["forbidden_visible_count"] == 0
    assert result["aggregate"]["forbidden_main_visible_count"] == 0
    assert result["aggregate"]["invariant_violation_count"] == 0

    by_id = {case["case_id"]: case for case in result["cases"]}
    # latest-node identity recall: only latest evidence is visible via relation
    latest = by_id["npc_latest_scene_identity"]
    assert latest["must_recalled"] == 1
    assert latest["route_counts"]["relation_only"] == 1
    assert latest["route_counts"]["direct_only"] == 0
    # topic recall of older node via relation
    topic = by_id["npc_last_scene_topic"]
    assert topic["must_recalled"] == 1
    assert topic["route_counts"]["relation_only"] == 1
    # mentioned-only stays direct, no relation
    mentioned = by_id["npc_mentioned_only_not_bridged"]
    assert mentioned["must_recalled"] == 1
    assert mentioned["route_counts"]["direct_only"] == 1
    assert mentioned["route_counts"]["relation_only"] == 0


def test_npc_last_scene_pending_contracts():
    fixtures_path = Path("benchmarks/npc_baseline/cases_last_scene_pending.json")
    recordings_path = Path(
        "benchmarks/npc_baseline/recordings/last_scene_pending.json"
    )
    report = run_npc_suite(
        load_fixtures(fixtures_path),
        load_recordings(recordings_path),
        fixtures_sha256=_fixtures_fingerprint(fixtures_path),
        recordings_sha256=_recordings_fingerprint(recordings_path),
    )

    aggregate = report["aggregate"]
    assert (aggregate["weighted_passed"], aggregate["weighted_total"]) == (
        135,
        138,
    )
    assert aggregate["forbidden_violation_count"] == 0
    assert aggregate["invariant_violation_count"] == 0
    assert aggregate["categories"]["last_scene_summary"] == {
        "passed": 10,
        "total": 10,
    }
    assert aggregate["categories"]["last_scene_batch_scope"] == {
        "passed": 11,
        "total": 11,
    }
