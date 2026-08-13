import json

import pytest

from scripts.benchmark_context_retrieval import (
    DEFAULT_BASELINE,
    DEFAULT_CASES,
    PENDING_NPC_FIELD_CASES,
    NPCFieldAttributionExpectation,
    _expected_npc_attribution_key,
    _materialize_npc_field_fixtures,
    _cases_fingerprint,
    compare_reports,
    load_suite,
    main,
    run_suite,
)
from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    history_document_text_hash,
)
from app.domain.npc_profile.store import NPCProfileStore


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


def test_pending_npc_field_contracts_capture_pre_bridge_gaps():
    suite = load_suite(PENDING_NPC_FIELD_CASES)
    report = run_suite(
        suite,
        fingerprint=_cases_fingerprint(PENDING_NPC_FIELD_CASES),
    )
    repeated = run_suite(
        suite,
        fingerprint=_cases_fingerprint(PENDING_NPC_FIELD_CASES),
    )

    assert suite.version == 2
    assert len(suite.cases) == 8
    assert repeated == report
    assert all(
        case["npc_bridge_status"] == "pending_not_wired"
        for case in report["cases"]
    )

    cases = {case["case_id"]: case for case in report["cases"]}
    assert cases["npc_role_relation_exact"]["missed_groups"] == [
        "guqing_role"
    ]
    assert cases["npc_stale_hash"]["missed_groups"] == [
        "valid_role_pointer"
    ]
    assert cases["npc_relation_owner_cap"]["missed_groups"] == [
        "qin_faction",
        "qin_appearance",
        "qin_summary",
        "lu_role",
    ]
    assert cases["npc_inactive_profile_eligibility"]["missed_groups"] == [
        "explicit_inactive_alias"
    ]

    # Existing retrieval already closes the direct and Continuity halves; the
    # pending contract specifically records the missing NPC provenance.
    assert cases["npc_direct_exact_vs_relation_noise"][
        "invariant_violations"
    ] == []
    shared = cases["shared_npc_continuity_evidence"]
    assert shared["must_recalled"] == 1
    assert shared["visible_evidence_ids"] == ["turn_1_p001"]
    assert any(
        "expected linked NPC changes" in violation
        for violation in shared["invariant_violations"]
    )
    aggregate = report["aggregate"]
    assert aggregate["must_recalled"] == 3
    assert aggregate["must_total"] == 10
    assert aggregate["weighted_recalled"] == 9
    assert aggregate["weighted_total"] == 30
    assert aggregate["invariant_violation_count"] == 6
    assert aggregate["forbidden_visible_count"] == 0
    assert aggregate["forbidden_main_visible_count"] == 0
    assert aggregate["duplicate_visible_count"] == 0


def test_pending_npc_field_command_writes_separate_report(tmp_path, capsys):
    report_path = tmp_path / "pending-report.json"

    assert main([
        "--pending-npc-field",
        "--json-out",
        str(report_path),
    ]) == 0

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["benchmark_version"] == 2
    assert len(report["cases"]) == 8
    assert "pending NPC field-history contracts" in capsys.readouterr().out


def test_pending_npc_log_ref_resolves_to_store_assigned_integer_id():
    expectation = NPCFieldAttributionExpectation(
        npc_id="npc-a",
        field_name="role",
        log_ref="role-change",
    )

    assert _expected_npc_attribution_key(
        expectation,
        {"role-change": 17},
    ) == ("npc-a", "role", "17")


def test_pending_npc_fixtures_materialize_real_logs_and_pointer_modes():
    suite = load_suite(PENDING_NPC_FIELD_CASES)
    case = next(item for item in suite.cases if item.case_id == "npc_stale_hash")

    with (
        ConfirmedHistoryStore() as history_store,
        NPCProfileStore(":memory:") as npc_store,
    ):
        for turn in case.turns:
            history_store.upsert_turn(**turn.model_dump())
        documents = {
            item.document_id: item
            for item in history_store.list_documents()
        }
        ids_by_ref = _materialize_npc_field_fixtures(
            case,
            documents,
            npc_store,
        )
        logs = {
            (item.field_name, item.turn_index): item
            for item in npc_store.list_field_change_logs()
        }
        final_profile = npc_store.get_by_id("npc-shenyan")

    assert all(isinstance(value, int) for value in ids_by_ref.values())
    assert len(set(ids_by_ref.values())) == 3
    assert logs[("role", 1)].evidence[0].evidence_hash == (
        history_document_text_hash(documents["turn_1_p001"])
    )
    assert logs[("faction", 2)].evidence[0].evidence_hash != (
        history_document_text_hash(documents["turn_2_p001"])
    )
    assert logs[("appearance", 3)].evidence[0].evidence_id == "turn_3_p999"
    assert "turn_3_p999" not in documents
    assert final_profile is not None
    assert final_profile.appearance == "常穿灰布长衫"
