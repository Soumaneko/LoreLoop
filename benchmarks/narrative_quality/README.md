# Narrative Quality Benchmark

`fixtures.json` contains ten fixed production-contract cases covering composite
actions and every outcome band. The active benchmark calls the current
single-stage Narrative Composer and checks action coverage, outcome-band
preservation, and non-empty prose/stopping state.

```bash
python scripts/benchmark_narrative_quality.py --record
python scripts/benchmark_narrative_quality.py --check
```

Generated recordings live under `recordings/` and remain git-ignored.

## Resolved TR-Composer experiment

The completed A/B experiment keeps its compact evidence package in Git while
the large raw call recording remains local:

- `recordings/tr_composer_unification.manifest.json`: versioned recording identity;
- `recordings/tr_composer_unification.json`: ignored raw A/B calls;
- `eval_prompts/tr_composer_unification/`: versioned blind prompts and mapping;
- `judgments/tr_composer_unification/`: versioned final web judgments;
- `reports/tr_composer_unification.json` and `.metrics.json`: versioned results.

The candidate won 20 of 30 generation pairs, tied the baseline 5-5 at case
level, reduced average total tokens by 43.5%, and reduced average total latency
by 34.4%. The old TR+Composer implementation is not retained as a runtime or
benchmark compatibility path; the recordings preserve the completed comparison.

## Closed outcome-anchored Composer experiment — 2026-11-01

Compared the production Composer (A) with a candidate (B) that removed pre-prose
planning/reasoning fields, placed prose immediately after action resolutions,
and extracted involved entities afterward. Prompt and Schema changed together.
Using `deepseek-v4.1-flash`, ten fixed cases with three repetitions per variant
produced 60 successful generations and 30 pairs, with no retries.

Original blind preferences were A 8 wins, B 10 wins, and 12 ties. Mean quality
scores were 12.000/16 and 12.033/16. B increased prose characters by 3.7%, reduced
structured-output characters by 49.5%, and reduced total latency by 18.4%.
Token usage was unavailable; character savings do not measure token savings.

The candidate was not adopted: these results did not establish a stable
improvement in scene construction or resolve defensive prose. The baseline also
did not reliably exhibit that problem in these cases, limiting their usefulness
for evaluating a remedy. Judging used one Codex session rather than a fresh web
conversation per case. A subsequent assistant review recommended revising one
preference; the original scores were preserved, and that review was not a new
human blind evaluation. The combined Prompt/Schema experiment cannot isolate
the effects of individual changes.

Dedicated experimental tooling was removed; production contracts remain active.
Detailed local evidence is retained in the ignored directory
`.bench/outcome-anchored/closed-20261101/` (relative to the repository root).
