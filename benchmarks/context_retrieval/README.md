# Context Retrieval Micro-benchmark

This deterministic benchmark measures the quality of the final Confirmed
History payload selected for Context Facts. It runs the real
`ContinuityHistoryBridge` and `ContextHistoryRetriever` against in-memory SQLite
stores. It does not call an LLM or touch a real save.

## Current checkpoint

Accepted on 2026-08-10 with 24 cases:

- must recall: `27/28`
- weighted recall: `92/97`
- forbidden evidence visible: `2`
- forbidden main evidence visible: `1`
- invariant violations: `2`

The six newest cases are benchmark-first contracts for per-evidence semantic
validation. Four encode deliberate, still-open gaps; two are regression guards
that must keep passing:

Open gaps:

- `relation_node_evidence_noise`: a generalised noise sentence mounted on the
  same lifecycle node as two strong evidence sentences still becomes a selected
  main hit. The contract is link-level: `forbidden_main_evidence_ids` requires
  the noise to lose its selection, while it may still legitimately appear as
  adjacent context of a strong neighbour. The node carries a real `state_delta`
  so per-evidence judgement has a semantic basis.
- `shared_evidence_link_specific_validity`: a paragraph shared by two matters is
  not dropped per-link; the unsupported matter's link stays on the main hit.
- `entity_overlap_without_predicate_support`: entity-only noun overlap passes
  even when the text does not support the matter's state change.
- `anonymized_spy_regression`: an archived matter matched through its later
  `current_state` inherits strong relevance onto unrelated create evidence. The
  create `premise` describes a different concrete event (清点护院人手并重整班次),
  so the generic-action create evidence ("握着信物，摩挲暗记，盘算下一步") is
  clearly unsupported. This is an anonymised reproduction of a real save; the
  case uses only generic terms (青岩镇 / 外乡客 / 信物 / 暗记 / 重振宗派).
  Production rules must never hardcode story-specific vocabulary.

Regression guards:

- `implicit_evidence_with_adjacent_context`: an implicit paragraph ("点头应下")
  whose meaning comes from the adjacent paragraph must survive validation. A
  provenance expectation pins it as a relation main hit, so deleting its link
  cannot pass by merely recalling it as the neighbour's adjacent context.
- `legacy_null_hash_fail_open`: legacy saves with a null node evidence hash must
  fail open and keep their evidence, not delete it. (A true "incomplete
  lifecycle semantic basis" case will be added once node snapshots exist.)

The earlier `late_state_delta_evidence` gap is unchanged; it concerns evidence
ordering inside one lifecycle node and is outside final-selector scope.

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
- `forbidden_main_evidence_ids` must not become a selected main hit; appearing
  as adjacent context of another main hit is allowed.
- `max_useful_evidence` marks excess alternatives in one fact group as duplicate
  payload.
- `provenance_expectations` apply to main hits and verify direct/relation route
  separation. They may also assert merged Continuity IDs and lifecycle
  operations for evidence shared by multiple matters.

Channel counts are diagnostic only. More relation-only or direct-only hits are
not inherently better.

`baseline.json` records the current accepted output and the SHA-256 of
`cases.json`. Changing cases requires deliberate baseline review.
