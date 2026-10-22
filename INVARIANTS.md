# LoreLoop（续章）— Product Invariants

This document defines the product and domain properties that must remain true
across implementation changes. Engineering choices follow the product scope
and the behavior affected by a change. The active implementation is described
in `CURRENT.md`; proposals belong in issues and important decisions in ADRs.

When code conflicts with this document, the implementation is defective unless
the invariant is explicitly changed. When `CURRENT.md` conflicts with code, the
current-state documentation is stale.

## 1. Product Boundary

LoreLoop is a local, save-scoped interactive narrative engine. A player action
is interpreted and resolved into an editable scene. Canonical campaign history
and state change only through the confirmation boundary.

- The supported use is one local owner playing one active save at a time.
  The owner controls player and creator views and may inspect or edit their
  own files and deliberately view backstage information.
- Current save and configuration formats are supported. An unreadable file
  is preserved and the application explains the failure.
- Each save is isolated from every other save.
- Generation may produce pending content, diagnostics, and staged changes, but
  it must not mutate canonical campaign state.
- Confirmation commits only the selected pending variant and the final accepted
  scene text, including player edits.
- Discarded, rerolled, rephrased, rejected, or unconfirmed variants never become
  canonical evidence or canonical world state.
- Orchestration technology must not own domain rules merely because it orders
  their execution.

## 2. Authority and Canon

- Confirmed scene prose is the primary authority for what happened in campaign
  history.
- Player input is authoritative for the player's intended action. A generated
  scene must not replace its actor, target, order, method, explicit conditions,
  or already supplied values.
- Programmatically established action outcomes and random results are
  authoritative for result direction. A generative component may realize the
  outcome but may not reverse, weaken, or secretly compensate for it.
- Success must deliver the authorized result; failure must remain a real
  failure. Dramatic additions may not disguise an outcome reversal.
- `CoreMemory` constrains world and protagonist compatibility. It is background
  authority, not evidence that a scene event occurred.
- Derived summaries, profiles, ledgers, state snapshots, plans, drafts, and the
  scene currently being checked cannot prove their own historical claims.
- Exact historical claims require confirmed evidence. Compatibility with known
  facts is not itself confirmation that an event occurred.

## 3. Creative and Player Boundaries

- The system is expected to create concrete dialogue, reactions, sensory
  detail, NPC initiative, world activity, and actionable developments rather
  than merely restate structured inputs.
- New fictional detail is legal when it is compatible with authoritative
  context and does not violate an information or permission boundary. Absence
  from existing context is not, by itself, a contradiction.
- The system must not invent a new substantive player decision, commitment,
  movement, attack, relationship choice, or other player action beyond the
  submitted input.
- NPCs and the world may act autonomously, but their actions may not steal the
  player's resolved success or force the player's next decision.
- A completed scene must leave a genuinely playable state with room for more
  than one reasonable response; suggested directions are aids, not restrictions
  on what the player may attempt.

## 4. State Ownership

- Stable world, protagonist, ability, and narrative constraints are distinct
  from evolving scene state and historical events.
- Present-state data contains only facts that are true at the current scene
  boundary, such as location, clock, participants, visible condition, and
  resources. It is not an archive of the story.
- Historical and relationship projections must remain derived from confirmed
  evidence and must not silently become independent sources of truth.
- Updating one projection must not grant it authority owned by another domain.
- A confirmation saves the accepted results for that turn together, using the
  domain validation and legal fallback behavior of each projection.

## 5. Evidence and Information Boundaries

- Evidence references must resolve to trusted confirmed material of the claimed
  turn and source type before they can support a canonical mutation.
- Structural links, retrieval matches, identifiers, scores, metadata, and model
  reasoning are routing or diagnostic information; they are not story facts.
- Missing, stale, mismatched, or unrelated evidence must not be silently
  upgraded into support for a claim.
- Private Director information and undisclosed NPC or Setting facts may guide
  backstage behavior but reach player-facing narrative only through an
  authorized disclosure or observable consequence.
- Normal player surfaces avoid accidental disclosure of backstage information;
  the owner may deliberately choose creator views. Model inputs and generated
  narrative follow the domain's information disclosure rules.
- Public projections expose only the fields appropriate to their audience.
  Local desktop diagnostics may show file and log locations needed to resolve a
  failure. Credentials, unrelated internal data, and undisclosed story material
  must not be exposed as diagnostic feedback.

## 6. Confirmation, Transactions, and Recovery

- All canonical mutations caused by one confirmed turn commit atomically or are
  rolled back together.
- A failed confirmation preserves a recoverable pending scene and does not
  partially advance canonical state.
- Repeated submission of a normal confirmation saves the turn once.
  Undo and Redo restore coherent saved progress, and subsequent operations use
  the restored data in the active save.
- After an abnormal exit, the application reopens complete saved progress or
  reports a failure while preserving the saved files. The player can explicitly
  retry unfinished work.
- Application preferences are not campaign history and must not be rewound by a
  narrative Undo or Redo.
- Save copy and export produce usable copies of saved progress. External
  archive import confines writes to the destination and preserves existing
  saves on rejection. Validation matches the source and purpose of each file
  operation.

## 7. Failure and Degradation

- User cancellation, storage errors, transaction failures, violated program
  invariants, and invalid canonical state are integrity failures; they must not
  be reported as harmless model degradation.
- Failure of an optional or derived probabilistic component should preserve
  usable player content and independently valid results when a legal fallback
  exists.
- Degradation must never fabricate successful main prose, evidence, state
  changes, or completed work.
- A fallback must be valid for the receiving domain, observable in diagnostics,
  and no more authoritative than the result it replaces.
- Retry, salvage, truncation, and item-dropping policies must be explicit at the
  owning domain boundary. They may recover valid components but must not merge
  incompatible attempts into a result that no attempt produced.
- Validate external input where needed to prevent file damage, credential
  exposure, or invalid domain writes.

## 8. Verification and Evolution

- Verification follows the behavior and risk of the change. Automated tests
  protect important product behavior, domain boundaries, data writes, and
  interfaces used by actual external consumers.
- Generated-content quality claims need recorded examples and the model/input
  context needed to assess them. Review benchmark differences for their effect
  on product behavior; baseline updates reflect reviewed, intended changes.
- Select checks for the affected behavior and risk. Keep tests and current-state
  documentation aligned with the implementation and the product requirements
  they serve.
- Infrastructure simplification preserves business behavior, including history
  retrieval, NPC handling, Director rules, action judgments, and narrative
  generation.
- Implementations evolve to satisfy these product requirements. Assess
  engineering choices by their value for the supported use and their
  maintenance cost.
- Proposed designs, experiments, and roadmaps do not belong in either domain
  document. Issues track possible work; ADRs preserve the rationale of accepted
  architectural decisions, including decisions later superseded.
