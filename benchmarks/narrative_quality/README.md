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
