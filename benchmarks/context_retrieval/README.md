# Context Retrieval Micro-benchmark

This deterministic benchmark measures the quality of the final Confirmed
History payload selected for Context Facts. It runs the real
`ContinuityHistoryBridge` and `ContextHistoryRetriever` against in-memory SQLite
stores. It does not call an LLM or touch a real save.

## Current checkpoint

Accepted on 2026-08-10 with 18 cases:

- must recall: `18/22`
- weighted recall: `63/77`
- forbidden evidence visible: `0`
- invariant violations: `0`

The accepted checkpoint deliberately preserves four known quality gaps so a
selector change can demonstrate an improvement instead of silently redefining
the expected output:

- `relation_fourth_is_critical`: the fourth necessary relation-only fact is
  blocked by the current global relation cap.
- `short_critical_vs_long_background`: long recent background displaces the
  short critical fact under a tight payload budget.
- `late_state_delta_evidence`: the decisive late paragraph in a Continuity
  update is not selected.
- `direct_noise_vs_relation_exact`: irrelevant direct entity mentions crowd out
  relation evidence that answers the question.

The five newest cases cover the missing explicit-relation/current-context
mirror, direct-noise versus relation-answer competition, zero relation
selection when direct evidence is exact, and payload pressure in both
direct-critical and relation-critical directions.

`baseline.json` is the detailed machine-readable checkpoint. Update it only
after reviewing an intentional semantic change; a passing run by itself is not
sufficient justification.

Run and compare with the accepted baseline:

```bash
PYTHONPATH=. uv run python scripts/benchmark_context_retrieval.py
```

Require byte-for-byte agreement with the accepted baseline:

```bash
PYTHONPATH=. uv run python scripts/benchmark_context_retrieval.py --check
```

After reviewing an intentional quality change, update the snapshot explicitly:

```bash
PYTHONPATH=. uv run python scripts/benchmark_context_retrieval.py --update-baseline
```

## Labels

Cases provide a fixed `ContextSearchPlan` so selector changes are isolated from
intent-building changes. `gold_groups` describe semantic facts rather than one
mandatory document. A group is recalled when any listed evidence ID is visible
to ContextExtractor as a main, supporting, or adjacent evidence record.

- `must` groups have weight 3.
- `should` groups have weight 1.
- `forbidden_evidence_ids` must not become visible.
- `max_useful_evidence` marks excess alternatives in one fact group as duplicate
  payload.
- `provenance_expectations` apply to main hits and verify direct/relation route
  separation. They may also assert merged Continuity IDs and lifecycle
  operations for evidence shared by multiple matters.

Channel counts are diagnostic only. More relation-only or direct-only hits are
not inherently better.

`baseline.json` records the current accepted output and the SHA-256 of
`cases.json`. Changing cases requires deliberate baseline review.
