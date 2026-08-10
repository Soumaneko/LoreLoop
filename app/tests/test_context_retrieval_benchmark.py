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
    assert relation_cap["must_recalled"] == 3
    assert relation_cap["must_total"] == 4
    assert relation_cap["relation_candidates_considered"] == 4
    assert relation_cap["relation_hits_added"] == 3

    flood = cases["single_continuity_item_flood"]
    assert flood["duplicate_visible_count"] == 2
    assert flood["continuity_contributions"] == {"tower-light": 3}

    budget = cases["short_critical_vs_long_background"]
    assert budget["must_recalled"] == 0
    assert budget["payload_chars"] <= 520

    assert benchmark_report["aggregate"]["forbidden_visible_count"] == 0
    assert benchmark_report["aggregate"]["invariant_violation_count"] == 0


def test_context_retrieval_benchmark_check_command(capsys):
    assert main(["--check"]) == 0
    output = capsys.readouterr().out
    assert "aggregate:" in output
    assert "quality regression detected" not in output
