import json

import pytest

from scripts.benchmark_context_retrieval import (
    DEFAULT_BASELINE,
    DEFAULT_CASES,
    _cases_fingerprint,
    compare_reports,
    load_suite,
    main,
    run_suite,
)


@pytest.fixture(scope="module")
def benchmark_report():
    suite = load_suite(DEFAULT_CASES)
    return run_suite(suite, fingerprint=_cases_fingerprint(DEFAULT_CASES))


def test_context_retrieval_benchmark_is_deterministic(benchmark_report):
    suite = load_suite(DEFAULT_CASES)
    repeated = run_suite(suite, fingerprint=_cases_fingerprint(DEFAULT_CASES))

    assert repeated == benchmark_report


def test_context_retrieval_benchmark_matches_accepted_baseline(benchmark_report):
    baseline = json.loads(DEFAULT_BASELINE.read_text(encoding="utf-8"))
    lines, regression = compare_reports(benchmark_report, baseline)

    assert not regression
    assert benchmark_report == baseline
    assert len(lines) == len(benchmark_report["cases"])


def test_context_retrieval_benchmark_captures_current_quality_signals(
    benchmark_report,
):
    cases = {
        case["case_id"]: case
        for case in benchmark_report["cases"]
    }

    assert cases["direct_exact_answer"]["must_recalled"] == 1
    assert cases["dual_route_document_merge"]["route_counts"]["dual"] == 1

    relation_cap = cases["relation_fourth_is_critical"]
    assert relation_cap["must_recalled"] == 4
    assert relation_cap["must_total"] == 4
    assert relation_cap["relation_candidates_considered"] == 4
    assert relation_cap["relation_hits_added"] == 4

    flood = cases["single_continuity_item_flood"]
    assert flood["duplicate_visible_count"] == 2
    assert flood["continuity_contributions"] == {"tower-light": 3}

    budget = cases["short_critical_vs_long_background"]
    assert budget["must_recalled"] == 1
    assert budget["payload_chars"] <= 520

    late_delta = cases["late_state_delta_evidence"]
    assert late_delta["must_recalled"] == 0
    assert late_delta["must_total"] == 1
    assert late_delta["turn_contributions"] == {"1": 1, "5": 2}
    assert late_delta["relation_candidates_considered"] == 4

    shared = cases["shared_evidence_across_continuities"]
    assert shared["visible_evidence_ids"].count("turn_1_p001") == 1
    assert shared["continuity_contributions"] == {
        "a-recruitment": 1,
        "b-signal": 2,
    }

    action_outcome = cases["player_input_vs_scene_outcome"]
    assert action_outcome["selected_main_ids"] == ["turn_1_p001"]
    assert action_outcome["forbidden_visible_ids"] == []

    mirrored_origin = cases["explicit_relation_vs_context_direct"]
    assert mirrored_origin["must_recalled"] == 1
    assert mirrored_origin["route_counts"] == {
        "direct_only": 1,
        "relation_only": 1,
        "dual": 0,
    }

    relation_answer = cases["direct_noise_vs_relation_exact"]
    assert relation_answer["must_recalled"] == 1
    assert relation_answer["must_total"] == 1
    assert relation_answer["relation_candidates_considered"] == 1
    assert relation_answer["relation_hits_added"] == 1

    zero_relation = cases["direct_exact_vs_relation_noise_and_zero"]
    assert zero_relation["must_recalled"] == 1
    assert zero_relation["relation_candidates_considered"] == 2
    assert zero_relation["relation_hits_added"] == 0
    assert zero_relation["forbidden_visible_ids"] == []

    long_relation = cases["cross_channel_long_relation_critical"]
    assert long_relation["must_recalled"] == 1
    assert long_relation["must_total"] == 1
    assert long_relation["route_counts"] == {
        "direct_only": 1,
        "relation_only": 1,
        "dual": 0,
    }
    assert long_relation["payload_chars"] <= 430

    long_direct = cases["cross_channel_long_direct_critical"]
    assert long_direct["must_recalled"] == 1
    assert long_direct["selected_main_ids"] == ["turn_6_p001"]
    assert long_direct["relation_candidates_considered"] == 2
    assert long_direct["relation_hits_added"] == 0
    assert long_direct["forbidden_visible_ids"] == []

    # Per-link node evidence support: complete-basis zero links are stripped
    # before the selector (attribution suppression); weak links stay degraded
    # and may only rescue uncovered intents (one-time, per-intent cap).
    noise = cases["relation_node_evidence_noise"]
    assert noise["must_recalled"] == 1
    assert noise["forbidden_visible_ids"] == []
    assert noise["forbidden_main_visible_ids"] == []

    link_specific = cases["shared_evidence_link_specific_validity"]
    assert link_specific["must_recalled"] == 1
    assert len(link_specific["invariant_violations"]) == 0

    entity_overlap = cases["entity_overlap_without_predicate_support"]
    assert entity_overlap["must_recalled"] == 1
    assert entity_overlap["forbidden_visible_ids"] == []

    spy = cases["anonymized_spy_regression"]
    assert spy["must_recalled"] == 1
    assert spy["forbidden_visible_ids"] == []

    zero_single = cases["complete_zero_single_current_context"]
    assert zero_single["forbidden_visible_ids"] == []
    assert zero_single["relation_hits_added"] == 0
    assert zero_single["relation_candidates_considered"] == 0
    assert zero_single["invalid_relation_links"] == 1

    # Regression guards that must keep passing.
    implicit = cases["implicit_evidence_with_adjacent_context"]
    assert implicit["must_recalled"] == 1
    assert implicit["forbidden_visible_ids"] == []
    assert implicit["invariant_violations"] == []

    legacy = cases["legacy_null_hash_fail_open"]
    assert legacy["must_recalled"] == 1
    assert legacy["relation_candidates_considered"] == 1
    assert legacy["relation_hits_added"] == 1
    assert legacy["forbidden_visible_ids"] == []

    assert benchmark_report["aggregate"]["forbidden_visible_count"] == 0
    assert benchmark_report["aggregate"]["forbidden_main_visible_count"] == 0
    assert benchmark_report["aggregate"]["invariant_violation_count"] == 0


def test_context_retrieval_benchmark_check_command(capsys):
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "aggregate:" in output
    assert "quality regression detected" not in output
