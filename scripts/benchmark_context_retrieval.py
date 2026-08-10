from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.confirmed_history import (
    ConfirmedHistoryStore,
    history_document_text_hash,
)
from app.domain.context_facts import (
    ContextHistoryRetriever,
    ContextSearchPlan,
    HistoryDiversityPolicy,
)
from app.domain.context_facts.continuity_bridge import ContinuityHistoryBridge
from app.domain.context_facts.views import project_context_history
from app.domain.continuity import ContinuityItem, ContinuityStore


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "context_retrieval" / "cases.json"
DEFAULT_BASELINE = ROOT / "benchmarks" / "context_retrieval" / "baseline.json"
BENCHMARK_VERSION = 1


class TurnFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    turn_index: int = Field(ge=0)
    scene_id: str = Field(min_length=1)
    user_input: str = ""
    scene_text: str = Field(min_length=1)


class ContinuityItemFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    kind: Literal["commitment", "open_thread"] = "open_thread"
    status: Literal["active", "resolved", "cancelled"] = "active"
    subject: str = Field(min_length=1, max_length=80)
    premise: str = Field(min_length=1, max_length=240)
    current_state: str | None = None
    entities: list[str] = Field(default_factory=list)
    location: str | None = None
    deadline: str | None = None
    created_turn: int = Field(ge=0)
    updated_turn: int = Field(ge=0)
    closed_turn: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_closed_turn(self):
        if self.status == "active" and self.closed_turn is not None:
            raise ValueError("active benchmark continuity cannot have closed_turn")
        if self.status != "active" and self.closed_turn is None:
            raise ValueError("archived benchmark continuity requires closed_turn")
        return self

    def to_domain(self) -> ContinuityItem:
        return ContinuityItem.model_validate(self.model_dump())


class ContinuityLogFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    item_id: str = Field(min_length=1)
    operation: Literal["create", "update", "resolve", "cancel"]
    turn_index: int = Field(ge=0)
    evidence_ids: list[str] = Field(min_length=1)
    state_delta: str | None = None
    reason: str = "benchmark evidence"
    hash_mode: Literal["valid", "legacy", "stale"] = "valid"


class GoldGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_id: str = Field(min_length=1)
    importance: Literal["must", "should"]
    acceptable_evidence_ids: list[str] = Field(min_length=1)
    max_useful_evidence: int = Field(default=1, ge=1)


class ProvenanceExpectation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    direct: bool
    relation: bool
    linked_continuity_ids: list[str] | None = None
    linked_operations: list[
        Literal["create", "update", "resolve", "cancel"]
    ] | None = None


class BenchmarkCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    current_turn_index: int = Field(ge=0)
    policy: dict[str, int] = Field(default_factory=dict)
    bridge: dict[str, int] = Field(default_factory=dict)
    search_plan: ContextSearchPlan
    turns: list[TurnFixture] = Field(min_length=1)
    continuity_items: list[ContinuityItemFixture] = Field(default_factory=list)
    continuity_logs: list[ContinuityLogFixture] = Field(default_factory=list)
    gold_groups: list[GoldGroup] = Field(default_factory=list)
    forbidden_evidence_ids: list[str] = Field(default_factory=list)
    provenance_expectations: list[ProvenanceExpectation] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def validate_fixture_references(self):
        item_ids = {item.id for item in self.continuity_items}
        unknown_items = sorted({
            log.item_id for log in self.continuity_logs if log.item_id not in item_ids
        })
        if unknown_items:
            raise ValueError(f"continuity logs reference unknown items: {unknown_items}")
        group_ids = [group.group_id for group in self.gold_groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("gold group IDs must be unique within a case")
        return self


class BenchmarkSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    cases: list[BenchmarkCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_case_ids(self):
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("benchmark case IDs must be unique")
        return self


def load_suite(path: Path) -> BenchmarkSuite:
    return BenchmarkSuite.model_validate_json(path.read_text(encoding="utf-8"))


def _cases_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _visible_evidence_ids(hits) -> list[str]:
    visible: list[str] = []
    for hit in hits:
        visible.extend([
            hit.evidence_id,
            *hit.supporting_evidence_ids,
            *(item.evidence_id for item in hit.context_before),
            *(item.evidence_id for item in hit.context_after),
        ])
    return visible


def _score_case(case: BenchmarkCase, evidence, payload: str) -> dict:
    visible_ids = _visible_evidence_ids(evidence.hits)
    visible_set = set(visible_ids)
    selected_ids = [hit.evidence_id for hit in evidence.hits]
    selected_by_id = {hit.evidence_id: hit for hit in evidence.hits}

    must_total = sum(group.importance == "must" for group in case.gold_groups)
    must_recalled = 0
    weighted_total = 0
    weighted_recalled = 0
    duplicate_visible_count = 0
    recalled_groups: list[str] = []
    missed_groups: list[str] = []
    for group in case.gold_groups:
        weight = 3 if group.importance == "must" else 1
        weighted_total += weight
        matched = visible_set.intersection(group.acceptable_evidence_ids)
        if matched:
            weighted_recalled += weight
            recalled_groups.append(group.group_id)
            if group.importance == "must":
                must_recalled += 1
        else:
            missed_groups.append(group.group_id)
        duplicate_visible_count += max(
            0,
            len(matched) - group.max_useful_evidence,
        )

    provenance_violations: list[str] = []
    for expectation in case.provenance_expectations:
        hit = selected_by_id.get(expectation.evidence_id)
        if hit is None:
            provenance_violations.append(
                f"{expectation.evidence_id}: expected main hit is absent"
            )
            continue
        direct = bool(hit.matched_intent_ids)
        relation = bool(hit.relation_intent_ids)
        if direct != expectation.direct or relation != expectation.relation:
            provenance_violations.append(
                f"{expectation.evidence_id}: expected direct={expectation.direct} "
                f"relation={expectation.relation}, got direct={direct} "
                f"relation={relation}"
            )
        if (
            expectation.linked_continuity_ids is not None
            and sorted(hit.linked_continuity_ids)
            != sorted(expectation.linked_continuity_ids)
        ):
            provenance_violations.append(
                f"{expectation.evidence_id}: expected linked continuities "
                f"{sorted(expectation.linked_continuity_ids)}, got "
                f"{sorted(hit.linked_continuity_ids)}"
            )
        if (
            expectation.linked_operations is not None
            and sorted(hit.linked_operations)
            != sorted(expectation.linked_operations)
        ):
            provenance_violations.append(
                f"{expectation.evidence_id}: expected linked operations "
                f"{sorted(expectation.linked_operations)}, got "
                f"{sorted(hit.linked_operations)}"
            )

    route_counts = Counter()
    continuity_contributions: Counter[str] = Counter()
    turn_contributions: Counter[str] = Counter()
    for hit in evidence.hits:
        if hit.matched_intent_ids and hit.relation_intent_ids:
            route_counts["dual"] += 1
        elif hit.relation_intent_ids:
            route_counts["relation_only"] += 1
        else:
            route_counts["direct_only"] += 1
        for continuity_id in hit.linked_continuity_ids:
            continuity_contributions[continuity_id] += 1
        turn_contributions[str(hit.turn_index)] += 1

    max_payload = case.policy.get(
        "max_payload_chars",
        HistoryDiversityPolicy().max_payload_chars,
    )
    invariant_violations = list(provenance_violations)
    if len(payload) > max_payload:
        invariant_violations.append(
            f"payload has {len(payload)} chars, exceeding {max_payload}"
        )
    if len(visible_ids) != len(set(visible_ids)):
        invariant_violations.append("visible evidence IDs are not globally unique")

    forbidden_visible = sorted(
        visible_set.intersection(case.forbidden_evidence_ids)
    )
    return {
        "case_id": case.case_id,
        "selected_main_ids": selected_ids,
        "visible_evidence_ids": visible_ids,
        "recalled_groups": recalled_groups,
        "missed_groups": missed_groups,
        "must_recalled": must_recalled,
        "must_total": must_total,
        "weighted_recalled": weighted_recalled,
        "weighted_total": weighted_total,
        "forbidden_visible_ids": forbidden_visible,
        "duplicate_visible_count": duplicate_visible_count,
        "payload_chars": len(payload),
        "route_counts": {
            "direct_only": route_counts["direct_only"],
            "relation_only": route_counts["relation_only"],
            "dual": route_counts["dual"],
        },
        "continuity_contributions": dict(sorted(continuity_contributions.items())),
        "turn_contributions": dict(sorted(turn_contributions.items())),
        "relation_candidates_considered": evidence.relation_candidates_considered,
        "relation_hits_added": evidence.relation_hits_added,
        "stale_relation_links": evidence.stale_relation_links,
        "invariant_violations": invariant_violations,
    }


def run_case(case: BenchmarkCase) -> dict:
    policy = HistoryDiversityPolicy(**case.policy)
    with ConfirmedHistoryStore() as history_store, ContinuityStore() as continuity_store:
        for turn in case.turns:
            history_store.upsert_turn(**turn.model_dump())
        documents = {
            document.document_id: document
            for document in history_store.list_documents()
        }

        for item in case.continuity_items:
            continuity_store.insert(item.to_domain())
        for log in case.continuity_logs:
            evidence = []
            for evidence_id in log.evidence_ids:
                document = documents.get(evidence_id)
                if document is None:
                    raise ValueError(
                        f"{case.case_id}: unknown continuity evidence {evidence_id}"
                    )
                evidence_hash = history_document_text_hash(document)
                if log.hash_mode == "legacy":
                    evidence_hash = None
                elif log.hash_mode == "stale":
                    evidence_hash = "0" * 64
                evidence.append((evidence_id, evidence_hash))
            continuity_store.add_lifecycle_log(
                item_id=log.item_id,
                operation=log.operation,
                turn_index=log.turn_index,
                state_delta=log.state_delta,
                reason=log.reason,
                evidence=evidence,
            )

        bridge = ContinuityHistoryBridge(
            continuity_store,
            **case.bridge,
        ).retrieve(case.search_plan)
        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=policy,
        ).retrieve(
            case.search_plan,
            current_turn_index=case.current_turn_index,
            continuity_links=bridge.links,
            matched_active_continuities=bridge.matched_active_continuities,
            matched_archived_continuities=bridge.matched_archived_continuities,
        )
        payload = project_context_history(result)
        return _score_case(case, result, payload)


def run_suite(suite: BenchmarkSuite, *, fingerprint: str) -> dict:
    case_results = [run_case(case) for case in suite.cases]
    aggregate = {
        "must_recalled": sum(case["must_recalled"] for case in case_results),
        "must_total": sum(case["must_total"] for case in case_results),
        "weighted_recalled": sum(
            case["weighted_recalled"] for case in case_results
        ),
        "weighted_total": sum(case["weighted_total"] for case in case_results),
        "forbidden_visible_count": sum(
            len(case["forbidden_visible_ids"]) for case in case_results
        ),
        "duplicate_visible_count": sum(
            case["duplicate_visible_count"] for case in case_results
        ),
        "invariant_violation_count": sum(
            len(case["invariant_violations"]) for case in case_results
        ),
        "payload_chars": sum(case["payload_chars"] for case in case_results),
    }
    return {
        "benchmark_version": BENCHMARK_VERSION,
        "cases_sha256": fingerprint,
        "aggregate": aggregate,
        "cases": case_results,
    }


def _case_map(report: dict) -> dict[str, dict]:
    return {case["case_id"]: case for case in report["cases"]}


def compare_reports(current: dict, baseline: dict) -> tuple[list[str], bool]:
    if baseline.get("benchmark_version") != BENCHMARK_VERSION:
        raise ValueError("baseline benchmark version does not match runner")
    if baseline.get("cases_sha256") != current["cases_sha256"]:
        raise ValueError("cases.json changed; review cases before updating baseline")

    lines = []
    regression = False
    baseline_cases = _case_map(baseline)
    for case in current["cases"]:
        before = baseline_cases.get(case["case_id"])
        if before is None:
            raise ValueError(f"baseline is missing case {case['case_id']}")
        changed = case != before
        if (
            case["must_recalled"] < before["must_recalled"]
            or case["weighted_recalled"] < before["weighted_recalled"]
            or len(case["forbidden_visible_ids"])
            > len(before["forbidden_visible_ids"])
            or len(case["invariant_violations"])
            > len(before["invariant_violations"])
        ):
            regression = True
        lines.append(
            f"{case['case_id']:<36} "
            f"must {before['must_recalled']}/{before['must_total']}"
            f"->{case['must_recalled']}/{case['must_total']}  "
            f"weighted {before['weighted_recalled']}/{before['weighted_total']}"
            f"->{case['weighted_recalled']}/{case['weighted_total']}  "
            f"forbidden {len(before['forbidden_visible_ids'])}"
            f"->{len(case['forbidden_visible_ids'])}  "
            f"payload {before['payload_chars']}->{case['payload_chars']}"
            f"{'  CHANGED' if changed else ''}"
        )
    return lines, regression


def _write_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the deterministic Context History retrieval benchmark."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--update-baseline", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    suite = load_suite(args.cases)
    report = run_suite(suite, fingerprint=_cases_fingerprint(args.cases))
    if args.json_out:
        _write_report(args.json_out, report)

    if args.update_baseline:
        _write_report(args.baseline, report)
        print(f"updated baseline: {args.baseline}")
        return 0

    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    lines, regression = compare_reports(report, baseline)
    print("\n".join(lines))
    aggregate = report["aggregate"]
    print(
        "aggregate: "
        f"must={aggregate['must_recalled']}/{aggregate['must_total']} "
        f"weighted={aggregate['weighted_recalled']}/{aggregate['weighted_total']} "
        f"forbidden={aggregate['forbidden_visible_count']} "
        f"duplicates={aggregate['duplicate_visible_count']} "
        f"payload={aggregate['payload_chars']}"
    )
    if args.check and report != baseline:
        print("benchmark output differs from the accepted baseline")
        return 1
    if regression:
        print("quality regression detected")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
