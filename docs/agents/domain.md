# Domain Docs

How engineering skills should consume this repository’s domain documentation.

## Before exploring

1. Read `INVARIANTS.md`. It is authoritative for the product and domain
   properties that every implementation must preserve.
2. Read `CURRENT.md`. It describes the active contracts, pipelines,
   persistence, and verification mechanisms, but its implementation shape is
   not a reason to preserve a module or stage. Verify current-state claims
   against code and tests.
3. Read root `CONTEXT.md` if it exists. It may provide a compact glossary or
   additional domain orientation, but it does not replace either domain file.
4. Read relevant ADRs under `docs/adr/` before changing the affected
   architecture.

If `CONTEXT.md` or `docs/adr/` does not exist, proceed silently. Domain-modeling skills may create them later when terminology or architectural decisions need durable documentation.

## Layout

This is a single-context repository:

```text
/
├── INVARIANTS.md
├── CURRENT.md
├── CONTEXT.md          optional compact domain glossary
├── docs/
│   └── adr/            architectural decision records
└── app/
```

## Vocabulary

Use the product boundaries from `INVARIANTS.md` and the active vocabulary from
`CURRENT.md`. If `CONTEXT.md` exists, use its glossary consistently and avoid
synonyms it explicitly rejects.

`CURRENT.md` is descriptive. Do not cite the presence of a current class,
schema, prompt, graph node, store, or pipeline stage as a requirement to retain
it. Proposed designs and experiments belong in issues; architectural rationale
belongs in ADRs. After implementation, update `CURRENT.md` to match the verified
code. Change `INVARIANTS.md` only when the product contract itself changes.

If a required concept is absent, first check whether the implementation already uses an established term. Record genuine terminology gaps for later domain modeling.

## ADR conflicts

If proposed work contradicts an existing ADR, identify the conflict explicitly instead of silently overriding it:

> Contradicts ADR-0007 — worth reopening because…
