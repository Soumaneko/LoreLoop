# Context Retrieval Micro-benchmark

This deterministic benchmark measures the quality of the final Confirmed
History payload selected for Context Facts. It runs the real
`ContinuityHistoryBridge`, `NPCProfileHistoryBridge`, and
`ContextHistoryRetriever` against in-memory SQLite stores. It does not call an
LLM or touch a real save.

## Current checkpoint

Accepted on 2026-08-13 (33 cases); re-recorded for the v8 NPC schema on
2026-08-14:

- must recall: `36/37`
- weighted recall: `117/122`
- forbidden evidence visible: `0`
- forbidden main evidence visible: `0`
- invariant violations: `0`
- duplicate visible: `4`

The newest cases are benchmark-first contracts for per-evidence semantic
validation. They are closed by the node-evidence quality layer (see
`app/domain/context_facts/evidence_support.py`); two are regression guards that
keep passing:

Closed contracts:

- `relation_node_evidence_noise`: a generalised noise sentence mounted on the
  same lifecycle node as two strong evidence sentences no longer becomes a
  selected main hit. The link-level `forbidden_main_evidence_ids` contract
  passes via attribution suppression: the node carries a real `state_delta`, so
  the noise paragraph scores zero support and is de-attributed while its strong
  siblings remain. It may still legitimately appear as adjacent context.
- `shared_evidence_link_specific_validity`: a paragraph shared by two matters is
  de-attributed per link; the unsupported matter (`gate-rebuild`) has a
  zero-support link suppressed while the supported matter (`copper-handoff`)
  keeps its attribution on the same document.
- `entity_overlap_without_predicate_support`: shared entity/location mentions
  without predicate support no longer pass. The create evidence overlaps the
  premise only through the location 青岩镇, which scores as weak support, so it
  stays degraded and is barred from rescuing an intent that already has direct
  coverage. (A genuinely zero-overlap mount would instead be stripped by
  complete-basis attribution suppression, as in
  `complete_zero_single_current_context`.)
- `anonymized_spy_regression`: an archived matter matched through its later
  `current_state` no longer inherits strong relevance onto unrelated create
  evidence. The create `premise` describes a different concrete event
  (清点护院人手并重整班次), so the generic-action create evidence ("握着信物，摩挲暗记，
  盘算下一步") is unsupported and its attribution is stripped. This is an
  anonymised reproduction of a real save; the case uses only generic terms
  (青岩镇 / 外乡客 / 信物 / 暗记 / 重振宗派). Production rules must never
  hardcode story-specific vocabulary.
- `complete_zero_single_current_context`: a complete-basis zero-support link is
  stripped even when it is the node's only evidence and its current-context
  intent has no direct hit. Previously such a link failed open and could claim
  a rescue seat; unconditional stripping treats it as an error mount.
  `archived_eligibility` (pact origin) and `shared_evidence_across_continuities`
  (recruitment context) carried the same class of wrongly mounted create
  evidence, which is now stripped and no longer kept as a gold group.

Regression guards:

- `implicit_evidence_with_adjacent_context`: an implicit paragraph ("点头应下")
  whose meaning comes from the adjacent paragraph survives validation. Its
  support is computed over the main text plus same-turn adjacency excluding the
  node's own sibling evidence, so the neighbouring paragraph provides the basis
  signal. A provenance expectation pins it as a relation main hit.
- `legacy_null_hash_fail_open`: legacy saves with a null node evidence hash must
  fail open and keep their evidence, not delete it. A null hash passes pointer
  integrity; the premise basis is strong, so the evidence keeps a normal seat.

The earlier `late_state_delta_evidence` gap is unchanged; it concerns evidence
ordering inside one lifecycle node and is outside final-selector scope. Its
weighted recall is preserved.

`baseline.json` is the detailed machine-readable checkpoint. Update it only
after reviewing an intentional semantic change; a passing run by itself is not
sufficient justification.

## NPC field-history contracts

Ticket 10 adds 8 accepted cases that drive the `NPCProfileHistoryBridge` against
the production v8 field-change store. Each case declares NPC profiles, stable
field-change nodes, pointer integrity modes, and structured
`(npc_id, field_name, log_id)` provenance. A fixture-local `log_ref` is resolved
to the store-assigned integer `log_id` when the fixture is materialized; fixture
labels never masquerade as database IDs.

- `npc_role_relation_exact`: a query asks for an NPC identity, the scene prose
  uses only a pronoun, direct retrieval misses, and the `role` field-change node
  bridges the key evidence as a relation hit.
- `npc_public_summary_delta`: regression that v7 legacy `public_summary` logs
  survive as read-only audit rows but are never bridged, so a matching topic
  yields no relation and the evidence surfaces only through direct text match.
- `npc_field_zero_support`: a complete-basis field link whose evidence scores
  zero support is stripped like a Continuity zero-support mount; its attribution
  must not claim a seat.
- `npc_direct_exact_vs_relation_noise`: when direct retrieval already answers
  exactly, a weak NPC relation proposal does not steal the seat.
- `shared_npc_continuity_evidence`: the same document referenced by an NPC field
  node and a Continuity node is selected once and carries both provenance sets.
- `npc_stale_hash`: missing evidence and non-null hash mismatches are skipped
  diagnostically while valid links keep their seats.
- `npc_relation_owner_cap`: repeated field updates of one NPC cannot monopolize
  the payload; separate NPC profiles do not share the owner cap.
- `npc_inactive_profile_eligibility`: an inactive profile only reactivates
  through an explicit name/alias input, never through current-context or
  topic-only noise.

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
  operations for evidence shared by multiple matters. NPC field cases may
  additionally assert structured field-change provenance by stable `log_id`.

Channel counts are diagnostic only. More relation-only or direct-only hits are
not inherently better.

`invalid_relation_links` and `degraded_relation_links` are diagnostic counts from
the node-evidence quality layer:

- `invalid_relation_links`: lifecycle links whose relation attribution was
  suppressed because the evidence scored zero support against a complete basis.
  A complete basis with zero support is an error mount and is stripped
  unconditionally, even when it is the node's only evidence; it never removes a
  document, only stops a false link from claiming that matter.
- `degraded_relation_links`: links whose evidence did not reach `strong` but
  stay selectable. Each intent is capped at `intent_target` rescue admissions
  dominated by that intent's own degraded proposal; degraded relation
  attribution that rides in with a document admitted by another intent's
  normal evidence is not bound by this seat cap. Degraded rescues never
  accumulate normal intent coverage, and one is no longer allowed once the
  intent has an actually selected direct hit. These constraints apply
  simultaneously, not additively: per-intent degraded cap (`intent_target`),
  per-turn (`turn_cap`), per-relation-owner (`relation_owner_cap`), global
  (`max_hits`) and the serialized payload budget. `entity_cap` only constrains
  the direct route and is not a degraded relation bound.

`baseline.json` records the current accepted output and the SHA-256 of
`cases.json`. Changing cases requires deliberate baseline review.

## Node-evidence quality layer

`ContinuityHistoryBridge` now attaches a per-node semantic basis to every link:

- `create`: primary = immutable `premise`, no contextual basis (the mutable
  subject is excluded so a later-rewritten title cannot back-fill the create
  node).
- `update`: primary = `state_delta` + `reason`, context = `subject` + `premise`.
  The aggregated `current_state` is deliberately excluded so later item-level
  information can never broadcast onto an earlier update's evidence.
- `resolve` / `cancel`: primary = `reason`.
- `basis_complete` is false only for migration/replay logs, so their generic
  reasons fail open instead of pretending to be real semantics.

`ContextHistoryRetriever` scores each link over the main evidence plus same-turn
adjacency, excluding the node's own sibling evidence (one adjacency rule, shared
with the rest of retrieval). The score keeps a primary/context split:

```text
combined = max(primary_support, 0.4 * context_support)
quality  = primary_support if primary_support > weak else min(combined, weak)
```

NPC field nodes use the change's `new_value` as the primary basis. Legacy v7
`public_summary` nodes (which carry `summary_delta`) are read-only audit rows
and never reach retrieval.

### Ticket 11 last-scene nodes

`cases_last_scene_pending.json` declares three contracts for the
`last_scene_summary` field: latest-node identity recall, bounded topic
recall of an older node, and a mentioned-only fact that remains direct History
without NPC relation attribution. Since the v8 profile field and the bridge
write-set exist, the runner materializes these logs through the production
store and the bridge resolves them: identity recalls the latest node, topics
add back an older node, and mentioned-only facts stay direct.

```bash
PYTHONPATH=. uv run python scripts/benchmark_context_retrieval.py \
  --cases benchmarks/context_retrieval/cases_last_scene_pending.json \
  --baseline .scratch/npc-turn-processor/nonexistent-last-scene-context-baseline.json \
  --json-out .scratch/npc-turn-processor/phase0-last-scene-context-report.json
```

These pending files never participate in the accepted `baseline.json --check`
gate until implementation and human review are complete.

Only the primary basis (premise / state delta / closing reason) can reach
`strong`; the contextual background can lift an evidence at most to `weak`, so
item-level semantics can never broadcast a node the evidence does not change.

Quality, class and degraded status are kept per `(document, intent)`, not folded
into one document-wide maximum: a strong link on intent A never promotes a weak
link on intent B of the same document. Everything below strong is degraded: it
may only rescue an intent that has no direct hit selected yet (judged per owner
intent, and dynamically so a direct candidate that fails quota, turn cap or
payload never blocks a legitimate rescue), capped per intent at the normal
coverage depth, and it never counts toward intent coverage. No HistoryDocument
is ever deleted because of quality.
