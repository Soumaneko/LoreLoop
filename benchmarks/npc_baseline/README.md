# NPC Pipeline Baseline Micro-benchmark

This benchmark measures the CURRENT two-LLM-call NPC pipeline
(`npc_profile_extract` + `npc_profile_update`) before the NPCTurnProcessor
refactor, and later the single-call processor. It runs the real deterministic
pipeline (candidate building, resolution validation, merge, evidence gate,
store mutation, warning order) against recorded LLM outputs. It does not call
an LLM during `--check`.

The input/gold contract and the recorded LLM outputs are deliberately split so
the benchmark can compare across refactors:

- `fixtures.json` is the input + gold contract and the only fingerprint source.
  It never contains recorded LLM output; changing it requires deliberate
  baseline review.
- `recordings/legacy.json` holds the two old tasks' recorded outputs and token
  usage (the `legacy` backend). A `--record` run or a new processor's recording
  schema changes this file but NOT the contract, so comparison proceeds instead
  of being refused.
- Backends project recorded outputs into the same normalized signals and score
  against the same gold, so legacy vs NPCTurnProcessor quality is comparable.

## Current checkpoint

Accepted on 2026-08-12 with 17 cases covering the ticket-01 categories,
recorded from a live API run (legacy extract + update):

- weighted gold recall: `144/153` (the baseline allows the model to score
  below full marks; the 9 missed points are documented known gaps below)
- forbidden violations: `2`
- invariant violations: `0`
- logical gateway calls: `33` (extract always runs, update skipped when
  extraction finds no mentions)
- provider attempts: `34` (one retry on `exact_canonical_and_alias`)
- real provider tokens, ALL attempts included:
  `prompt_tokens=73387`, `completion_tokens=4537`

The recordings come from a live run; the single hand-aligned recording
(`mixed_turn`, re-recorded against its corrected scene) is real too. Cases
whose contracts a compliant model never triggers were removed: the evidence
gate (missing / fabricated / wrong-turn / wrong-source citations) and the
blank-string-no-clear rule are deterministic pipeline behavior covered by
`app/tests/test_npc_profile_workflows.py`; the disguise-coreference case was
removed because the extractor folds the descriptor into the main NPC's
mention, so a descriptor never becomes its own alias candidate in the
two-task pipeline.

A below-full score is a feature, not a bug: the gold encodes the desired
contract and the model may fail it, so a future processor that fixes a gap
scores higher and the improvement is measurable. Current known gaps:

- `ability_append_dedup_cap` (0/3): the model maps the evidenced "隐匿身法"
  onto the existing "轻功" and never records the new ability. It also cannot
  see existing `known_abilities` in the update prompt, so dedup is blind.
- `stable_fields_with_evidence` (6/9): the model writes role+faction with
  valid evidence but skips the `public_summary` for a major identity reveal.
- `player_identity_protected` (0/3): the player is offered as a pseudo-option,
  so the model resolves the player as `existing` (dropped by validation, the
  player is still never admitted) instead of `ignore`; `protected_player_identity`
  in ticket 02 should remove the pseudo-option.
- `mixed_turn` (forbidden): the model persists the "合欢宗宗主" title as a
  permanent alias of 沈孤鸿 (policy: titles resolve but must not be persisted,
  to avoid future mis-attribution) and writes an inferred `current_intent`
  ("吩咐关门，似有要事") from a completed action.
- Descriptor coreferences (青衣客 / 青衣人) are not extracted as separate
  mentions; the companion case enforces that a followed companion is never
  alias-matched even when extracted.

## Usage

Compare against the accepted baseline and assert no regression:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py
```

Require byte-for-byte agreement with the accepted baseline:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --check
```

After reviewing an intentional behavior change, update the snapshot explicitly:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --update-baseline
```

Refresh recorded LLM outputs and provider tokens via a real gateway (requires
`OPENAI_API_KEY` / `OPENAI_BASE_URL`, see `.env.example`). Refuses to overwrite
an existing recordings file without `--overwrite` and uses a per-run trace id,
so re-runs never read stale traces:

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --record --overwrite
```

Rebuild recordings from an existing trace file without new LLM calls
(recovers retry tokens and attempts), and re-record a single case against a
changed fixture (both honour `--overwrite`):

```bash
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --replay-trace .bench/npc_baseline/run-<token>/traces.jsonl --overwrite
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --record --only mixed_turn --overwrite
```

`--only` merges into the existing recordings file instead of replacing it.
`--replay-trace` refuses stale traces: it requires a single run in the file,
every fixture case present, and verifies each case's recorded inputs against
the current fixture, naming any case that must be re-recorded with
`--record --only <case_id>`.

## Labels

`gold` expectations are scored directly, with no judge LLM:

- `mention_recall`: names the extractor must/should surface as NPC mentions.
- `forbidden_mentions`: names that must never be extracted.
- `exact_expectations`: exact existing targets that must keep their npc_id for
  EVERY mention surface (canonical name, permanent alias, transient alias).
- `resolution_expectations`: `candidate_name -> existing|alias_match|new|ignore`,
  with an optional `target_npc_id` that pins the alias-match target (a proposal
  that alias-matches a different NPC fails even when it does not persist).
- `forbidden_resolutions`: resolution/candidate combinations that must not
  happen (e.g. a companion must never alias-match the person it follows).
- `alias_expectations` / `forbidden_aliases`: alias additions to a profile
  (persist only fixed nicknames, never temporary descriptors).
- `stable_field_expectations`: updater-modifiable fields that must end at a
  value (temporary and long-term channels both included).
- `forbidden_field_changes`: fields that must remain unchanged (e.g. an
  attitude guess with no player relationship evidence).
- `new_npc_expectations` / `forbidden_new_names`: created candidates.
- `warning_expectations`: expected `long_term_field_evidence_*` drop codes
  (missing / unknown id / wrong turn / wrong source).
- `no_op_expectations`: profiles that must end with no updater-modifiable field
  delta. Aliases, hit counts, `source_scene_ids` and timestamps are deliberately
  out of scope for this check.
- Call count is enforced per backend policy (legacy `1-2`, npcturn at most
  `1`); it is not part of the shared gold.

`must` expectations weigh 3, `should` weigh 1. A `forbidden` or `invariant`
violation fails the run. Per-case `prompt_metrics` record the update prompt's
`existing_options` count and `content_chars` so roster-driven token growth can
be measured (see `large_roster`: 110 profiles -> 111 identity options). The
backend-neutral `prompt_content_chars` and `token_usage_total` are what the
aggregate consumes: the former is the total content chars across all NPC calls
in the turn, the latter sums prompt/completion tokens across every recorded
task regardless of name, so the single-call NPCTurnProcessor totals
identically to the legacy pair. Token figures are `null` until a real
`--record` run.

The NPC gateway call count is a backend policy, not a shared gold
expectation: the legacy backend must make `1-2` calls per turn (the updater
short-circuits when extraction finds no mentions), while the NPCTurnProcessor
backend must make at most `1`. The scorer checks the policy bounds, so a
correct single-call processor is never flagged as a regression.

`baseline.json` is the detailed machine-readable checkpoint. It pins the
`fixtures.json` SHA-256 plus the backend and recordings SHA-256; comparison is
only refused when `fixtures.json` changes. A change of backend or recordings
(an intentional new snapshot) is reported as a note, not an error. Running a
recordings file under a mismatched backend fails fast instead of producing a
mislabeled report. Changing cases requires deliberate baseline review; a
passing run by itself is not sufficient justification.

## Determinism

The offline path (`--check`) replays the recorded outputs through a stub
gateway and re-runs `update_npc_profiles_workflow` exactly as production does:
same candidate order, validation, merge, evidence gate, warning order, and
store mutation. The report is byte-for-byte comparable across runs and across
`PYTHONHASHSEED` values.

## Cross-refactor comparison

Both backends emit the same normalized per-case signals (mentions, exact
targets, resolutions, aliases added, field deltas, warnings, prompt metrics)
and score against the same gold. Ticket 06 therefore compares the NPCTurn
report against the frozen legacy report at the gold-aggregate level
(weighted/forbidden/invariant per category) and reports the call-count and
token deltas. The `npcturn` backend itself lands with ticket 04.
