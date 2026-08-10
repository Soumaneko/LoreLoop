# Context Retrieval Micro-benchmark

This deterministic benchmark measures the quality of the final Confirmed
History payload selected for Context Facts. It runs the real
`ContinuityHistoryBridge` and `ContextHistoryRetriever` against in-memory SQLite
stores. It does not call an LLM or touch a real save.

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
  separation.

Channel counts are diagnostic only. More relation-only or direct-only hits are
not inherently better.

`baseline.json` records the current accepted output and the SHA-256 of
`cases.json`. Changing cases requires deliberate baseline review.
