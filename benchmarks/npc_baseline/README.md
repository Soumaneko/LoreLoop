# NPC Pipeline Baseline Micro-benchmark

This benchmark measures the production NPC pipeline (`batch` backend:
deterministic identity resolution + a single `npc_batch_update` LLM call)
against recorded LLM outputs. It runs the real deterministic pipeline (identity
resolution, single-call batch field update, per-field fault-tolerant admission,
CurrentState sync, store mutation, warning order) with a stub gateway. It does
not call an LLM during `--check`.

The input/gold contract and the recorded LLM outputs are deliberately split:

- `fixtures_batch.json` is the input + gold contract and the only fingerprint
  source. It never contains recorded LLM output; changing it requires deliberate
  baseline review.
- `recordings/batch.json` holds the `npc_batch_update` recorded outputs, token
  usage, and attempts. A `--record` run changes this file but NOT the contract,
  so comparison proceeds instead of being refused.

## Current checkpoint

Accepted with 17 cases covering the ticket-01 categories re-scored for the
batch architecture (mention/exact/resolution are deterministic resolver
outputs; the LLM is measured on field changes, aliases, warnings, and no-ops).
Recorded from a live API run on 2026-08-12 (second take, accepted); re-recorded
for the v8 schema on 2026-08-14:

- weighted gold recall: `150/153` (the baseline allows the model to score below
  full marks; the 9 missed points are documented exact-string gaps below;
  personality and abilities are currently curated fields outside per-turn
  update scoring)
- forbidden violations: `0`
- invariant violations: `0`
- logical gateway calls: `12` (0 calls when identity resolution finds no
  resolvable appeared NPC, e.g. player-only, org/place, or mentioned-only cases)
- provider attempts: `12` (all first-round successes)
- real provider tokens, ALL attempts included:
  `prompt_tokens=18678`, `completion_tokens=1309`

A below-full score is a feature, not a bug: the gold encodes the desired
contract and the model may fail it, so a fix scores higher and the improvement
is measurable. The `role` + `faction` linkage is genuinely exercised (both
written together on `mixed_turn`); the remaining 9 points are exact-string
scoring, not omitted or wrong field updates:

- `stable_fields_with_evidence` (9/12): role/faction exact.
- `stable_appearance_with_evidence` (3/6): appearance truncated to "左眼有一条
  旧疤" instead of the evidenced full sentence.
- `ability_append_dedup_cap` (3/6): the evidenced "隐匿身法" is now added, but
  the gold expects the ability name "隐匿" (exact-string mismatch).

A semantically tolerant scorer (accepting these values) is a candidate follow-up
improvement and does not block the baseline.

## Usage

The default backend is `batch`; bare invocations exercise the production path:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py            # compare + no regression gate
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --check    # require byte-for-byte baseline agreement
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --update-baseline
```

Refresh recorded LLM outputs and provider tokens via a real gateway (requires
`OPENAI_API_KEY` / `OPENAI_BASE_URL`, see `.env.example`). Refuses to overwrite
an existing recordings file without `--overwrite` and uses a per-run trace id,
so re-runs never read stale traces:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --record --overwrite
```

Rebuild recordings from an existing trace file without new LLM calls (recovers
retry tokens and attempts), and re-record a single case against a changed
fixture (both honour `--overwrite`):

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --replay-trace .bench/npc_baseline/run-<token>/traces.jsonl --overwrite
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --record --only mixed_turn --overwrite
```

`--only` merges into the existing recordings file instead of replacing it.
`--replay-trace` refuses stale traces: it requires a single run in the file,
every fixture case present, and verifies each case's recorded inputs against
the current fixture (the deterministic `npc_batch_json` projection), naming any
case that must be re-recorded with `--record --only <case_id>`.

## Labels

`gold` expectations are scored directly, with no judge LLM:

- `mention_recall`: identity-bearing primary/alias surfaces that must/should
  appear in Scene Facts' `npc_mentions` (deterministic resolver input). Generic
  pronouns are excluded from this contract.
- `forbidden_mentions`: names that must never be emitted (orgs, places, player).
- `exact_expectations`: existing targets that must keep their `npc_id` for a
  canonical/permanent-alias surface (deterministic resolver output).
- `resolution_expectations`: `name -> existing|new` as the deterministic
  resolver decides (existing profile hit vs minimal candidate).
- `alias_expectations` / `forbidden_aliases`: alias additions persisted to a
  profile (only `explicit_fixed_alias` relations persist; temporary descriptors
  resolve but never persist).
- `stable_field_expectations`: updater-modifiable fields that must end at a
  value (CurrentState-synced `last_known_location` / `attitude_to_player` and
  LLM `stable_changes` — `role` / `appearance` / `faction` — all included).
- `forbidden_field_changes`: fields that must remain unchanged (e.g. an
  attitude the batch updater must not invent).
- `new_npc_expectations` / `forbidden_new_names`: minimal candidates offered to
  the Director (an unmatched named person becomes a candidate; the player never
  does).
- `warning_expectations`: expected per-field drop codes from the batch
  admission.
- `no_op_expectations`: profiles that must end with no updater-modifiable field
  delta. Aliases, hit counts, `source_scene_ids` and timestamps are deliberately
  out of scope for this check.
- Call count is enforced per backend policy (batch `0-1`); it is not part of the
  shared gold.

`must` expectations weigh 3, `should` weigh 1. A `forbidden` or `invariant`
violation fails the run. Per-case `prompt_metrics` record the single
`npc_batch_update` prompt's `resolved_npcs`, `evidence_paragraphs`, and
`content_chars` so roster/evidence-driven token growth can be measured (see
`large_roster`). `prompt_content_chars` and `token_usage_total` feed the
aggregate; token figures are `null` until a real `--record` run.

## Determinism

The offline path (`--check`) replays the recorded `npc_batch_update` outputs
through a stub gateway and re-runs `apply_npc_batch_update` exactly as
production does (fixed `admitted_at`): same resolution order, admission,
warning order, and store mutation. The report is byte-for-byte comparable
across runs and across `PYTHONHASHSEED` values.

## Ticket 11 pending contract

`cases_last_scene_pending.json` and `recordings/last_scene_pending.json` are an
unaccepted benchmark-first contract for the v8 NPC model's `last_scene_*`
fields.  They do not change `fixtures_batch.json`, its fingerprint, or
`baseline_batch.json`.

The optional `last_scene_contract` scores three future signals without a judge
LLM:

- `npc_presence`: `appeared` vs `mentioned_only` emitted by Scene Facts;
- `last_scene_summary`: one 20–160 character summary for the expected turn,
  containing required terms, excluding stale terms, and citing 1–4 paragraph
  IDs from an allowed set;
- `last_scene_batch_scope`: appeared profiles enter the updater while
  mentioned-only profiles do not.

The pending recordings use the nested `scene_summary` wire key.  The parser
consumes it and admission writes the pair atomically, so the pre-implementation
gaps (presence / summary / salvage) are now expected to be green.  Run it
without accepting a baseline:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py \
  --fixtures benchmarks/npc_baseline/cases_last_scene_pending.json \
  --recordings benchmarks/npc_baseline/recordings/last_scene_pending.json \
  --baseline .scratch/npc-turn-processor/nonexistent-last-scene-baseline.json \
  --json-out .scratch/npc-turn-processor/phase0-last-scene-report.json
```
