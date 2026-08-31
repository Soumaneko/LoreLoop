# LoreLoop（续章）— Current Implementation

This document describes the implementation that exists in the same repository
revision. It is descriptive, not a promise to preserve any particular module,
schema, prompt, pipeline stage, storage layout, limit, or framework. Verify it
against code and tests when making claims about runtime behavior; a mismatch is
documentation drift.

Product behavior that must survive implementation changes is defined separately
in `INVARIANTS.md`. Proposed and future designs do not belong in this document.

## 1. Active Product Shape

The active application is a local, save-scoped interactive narrative engine.
It turns a player action into an editable pending scene and routes the selected
scene through confirmation before writing canonical campaign state.

The current implementation maps the invariant domains as follows:

- `CoreMemory` stores stable world, protagonist, and narrative constraints;
- `CurrentState` stores the present scene, clock, player state, and resources;
- confirmed scene prose is persisted as the historical evidence source;
- generation writes pending, staged, diagnostic, and operation data, while the
  confirmation flow owns canonical business mutations;
- LangGraph orders domain-service execution;
- Pydantic schemas, prompts, stores, and public projections define the active
  runtime interfaces.

## 2. Save Runtime and Storage

Each save is an isolated UUID directory with a `save_manifest.json`. Its current
root data files are:

- `core_memory.json`
- `initialization.json`
- `settings.json`
- `campaign.db`

`campaign.db` holds every canonical domain table (`current_state`, `short_term`,
`scene_summaries`, `confirmed_history`, `continuity`, `npc_profiles`,
`director_events`, `director_log`, `director_npcs`, `world_settings`) plus a
`schema_versions` row per component. `initialization_draft.json` is an editable
setup draft, not canonical campaign state. `.runtime/control.db` durably journals
pending scenes, confirmation status, the bounded Undo/Redo snapshot chain
(`.snapshots/`), and the control state (`current_revision_id`,
`connection_epoch`, plus per-transaction `commit_id`/`revision_id`).

New saves start as an empty `campaign.db` and a fresh `control.db` with an
initial `current_revision_id` (`uuid4().hex`) and `connection_epoch=0`. Any save
directory that still contains legacy ten per-file databases
(`current_state.db` etc.), `turn_history.db`, or `.runtime/director_mutation.json`
and does not already have a valid `campaign.db`/`control.db` is rejected with
`unsupported legacy layout` and no empty database is created. Director canonical
mutations share the single `campaign.db` Confirmation transaction and have no
separate journal.
During control DB upgrade, a linear revision chain is backfilled from
`turn_snapshots.sequence` so that each snapshot's pre-revision and its
transaction's post-revision are contiguous; `current_revision_id` is set to the
revision after the last `applied` snapshot. An existing `control.db` with both
new columns already filled is left unchanged.

A real confirmation runs every canonical write of a single turn inside one
`BEGIN IMMEDIATE ... COMMIT` transaction on `campaign.db`; all ten stores share
one connection and either commit or roll back together. The turn journal in
`control.db` and its pre-confirmation snapshot provide cross-store rollback and
retry recovery, and control-plane follow-ups (audit, pending cleanup, maintenance
scheduling, finish events) happen only after the transaction commits. This
transaction does not span `control.db`, which remains a separate journal
database. On a successful confirmation, one `commit_id` and one new
post-confirm `revision_id` are generated idempotently and
`control_state.current_revision_id` is updated in the same control DB
transaction; `connection_epoch` only increments on `restore` (Undo, Redo,
failed-confirmation restore, or crash recovery), never on a normal commit.

Snapshot boundaries: `.snapshots/<id>/` contains only `campaign.db`,
`core_memory.json` (if present), `initialization.json` (if present), and
`manifest.json`. `settings.json` never travels with a snapshot. Snapshot
manifests containing any other file (including legacy ten-DB names or
`settings.json`) fail closed.

Phase 4 offline restore (complete): every canonical restore (Undo, Redo,
failed-confirmation rollback, and interrupted-recovery) runs as a durable
offline replacement. The runtime quiesces new operation UoWs, waits for
active operation UoWs and maintenance jobs, closes the session `CampaignUnitOfWork`,
stages each snapshot file to a same-directory temporary (`campaign.db` is
copied to a temporary and validated via the shared `validate_campaign_database`
schema/foreign-key/quick_check contract, JSON is validated by parse), fsyncs
the temporary, deletes the target `-wal/-shm/-journal`, atomically installs
with `os.replace`, and fsyncs the save directory; `settings.json` and
`initialization_draft.json` never participate. `turn_snapshots.status` durably
records intent (`applied→undoing→undone`, `undone→redoing→applied`) and
`turn_transactions.status` records `confirming→restoring→failed` before the
file replace, so a crash before, during, or after the replace converges
idempotently on restart and `connection_epoch` is incremented only once in the
final control transaction. Production operation UoWs are created via
`CampaignRuntime.uow()` which stamps `expected_epoch` and participates in the
quiesce barrier; a stale or closed UoW is rejected and the session UoW is
recreated with the new epoch after each restore. Confirmation failures go
through the runtime offline path (`runtime.fail_confirmation_offline`) instead
of direct `history.fail_confirmation`. Runtime startup and `reconfigure()` use
the same ordering: `ensure campaign.db/control.db → CampaignHistory.recover_interrupted() offline →
formal session UoW/Services → pending restore`.

Save copy and export require the source to have no pending content and
create a quiesced staging snapshot. SQLite files are copied through the backup
API; ZIP export records file sizes and SHA-256 hashes. Import validates archive
paths, resource limits, manifest contents, checksums, SQLite `quick_check`,
registered schema versions, and portable history paths before installing a
save. Any save or ZIP that still contains legacy ten per-file databases,
`turn_history.db`, `settings.json` inside a snapshot, or
`.runtime/director_mutation.json` is rejected with `unsupported legacy layout`.

## 3. Initialization

Initialization uses `InitConfig` version 3 and proceeds in two operations:

1. validate the player configuration, generate `CampaignSetupProposal` version
   2, and commit the proposal-derived `CoreMemory`;
2. generate a private Opening Foundation, create the initial World Settings,
   compose the Opening scene, and leave it pending for player confirmation.

Campaign setup contributes these public world baselines:

- `currency_unit`;
- `public_power_baseline`;
- `peoples_and_beings`: 1-6 entries, at most 10 characters each;
- `major_world_conflicts`: 1-2 entries, at most 50 characters each;
- `dominant_powers`: 1-3 entries, at most 10 characters each;
- `civilization_baseline`.

The initialization record advances through recoverable phases:

```text
proposal_committing
-> core_memory_committed
-> foundation_ready
-> opening_pending
-> completed
```

The Opening graph is strictly ordered:

```text
OpeningFoundation
-> OpeningSettingAgent (2-4 high-level requirements)
-> WorldSettingCreator calls (bounded parallelism, at most 3 workers)
-> Opening composition
```

The Foundation establishes the starting location, opening situation, and initial
inventory/money/fixed assets; CurrentState is persisted before Setting creation.
The Setting Agent supplies only kind and a compact requirement. The specialist
Setting Creator produces the names and facts. Initial Settings are saved as one
batch and reused if initialization resumes after they have already been created.

Opening confirmation runs the normal confirmation pipeline at turn `0`, then
clears and replaces short-term history, Confirmed History, and Continuity. The
Director base flow always runs for both opening confirmation and both Director
stages of later turns, regardless of the player/creator experience mode.

## 4. Generation Pipeline

The normal turn graph is:

```text
START
-> ActionExtractor + deterministic ActionSelfCheck
   ├─ ContextFactsService
   └─ optional RollModifierEvaluator -> deterministic RollResolver
join -> deterministic ActionJudgments
      + Director Agent generation stage (always runs for real generation)
     -> NarrativeComposerAgent (outcome realization + prose)
     -> optional Canon Query -> Confirmed History evidence retrieval
        -> Narrative Consistency Check -> at most one source rewrite
     -> durable pending turn
```

Action parsing completes first; Context extraction and roll evaluation then run
in parallel. This ordering lets the Action Extractor provide its normalized
retrieval topics to Context Facts without serializing Context extraction and roll
evaluation. Roll modifiers are only evaluated for actions selected for a roll.
Python derives one `ActionJudgment` per parsed atomic action as soon as rolls are
available; actions without a roll are automatic successes. These judgments
contain the immutable outcome bands shown immediately to the player. The single
Composer call then decides how those outcomes become concrete facts and prose;
there is no independent LLM Turn Resolution stage.

Before the graph starts, deterministic NPC selection admits at most 20 profiles
whose canonical name or alias appears in the player input or CurrentState
participants. Those profiles provide the action/Composer context and the
matched entity anchors used by Context Facts. The Action Extractor produces
atomic actions, at most eight compact retrieval topics, and any hidden Event IDs
directly touched by the player input. Retrieval topics are compact semantic
hints and represent one search dimension each. They are emitted immediately
after analysis and deliberately do not reference atomic action IDs, so a
retrieval-relevant topic does not require a corresponding action. They may use
stable canonical names grounded by CurrentState or Core Memory, including
person, item, or location names. The deterministic self-check repairs action
structure and trims and de-duplicates topics; semantic topic quality remains the
Action Extractor's responsibility rather than being filtered by surface-text
comparison.

After Context Facts are available, profile selection is expanded without loading
the full profile store:

- Composer receives the round-robin, deduplicated union of initial action
  profiles and profiles named in Context Facts, at most 20;
- Director receives those two sources plus profiles linked to active Director
  Events, at most 20.

Every non-Director consumer receives `NPCProfile.disclosure_view()`, so fields
above the profile's persistent disclosure level are physically absent.

The generation graph always includes the Director generation stage. Experience
mode never removes or downgrades Director execution: `player` and `creator` are
surface and projection modes, not narrative-quality modes. The only optional
generation branch is Canon validation; when it is disabled, composition ends
directly at pending-turn creation.

 Context Facts fail open only for LLM extraction failures: extraction failure is
 recorded diagnostically and the turn continues with an empty fact set.
 Deterministic retrieval, bridging, and NPC profile selection failures are
 integrity failures and propagate. Generation can write its pending journal
 and diagnostic/operation state. Director NPC introductions and disclosure
 upgrades remain staged inside that pending variant; generation does not update
 canonical world, profile, history, Continuity, Director, or Setting stores.

When a player triggers a **Reroll** (重新判定) on a pending turn, the pipeline
reuses the verified upstream prefix via `GenerationPrefixSnapshot` (Action
Extractor, Context Facts, and RollModifierEvaluator if non-fallback) and resumes
generation directly from the deterministic `RollResolver` node, saving upstream
LLM calls. If a pending turn is discarded and the identical player input is
resubmitted within the same canonical epoch, runtime `GenerationPrefixCache`
allows prefix reuse. Any canonical mutation (confirming a turn/opening,
undo/redo/rollback, save switch/reload, or editing CoreMemory/NPC/Setting/
Continuity records) increments `generation_cache_epoch` and clears the prefix
cache.

## 5. Confirmation Pipeline

Confirmation always projects the final accepted scene text, including any player
edit. If prepared Director NPCs or disclosure upgrades were staged by the
confirmed variant, they are promoted/applied and projected into the confirmation
context before Scene Facts are extracted. They are protected by the confirmation
snapshot and retry cache.

The confirmation retry cache (`ConfirmationCache`) isolates data into two
distinct invalidation domains:
- `preload`: read-only preloaded profiles and staged Director NPC drafts
  (`existing_npc_profiles`, `director_npc_profile_ids`,
  `confirmed_director_npc_drafts`), retained across scene text edits;
- `result`: text-derived outputs (`scene_facts`, `updated_state`,
  `turn_summary`, etc.) and step completion markers, strictly invalidated
  whenever the effective scene text changes.

```text
prepare and validate pending scene
-> first wave, serialized
   Scene Facts -> CurrentState update -> per-turn Scene Summary
-> second wave
   ├─ Action Preview
   ├─ Continuity lifecycle projection (active items preloaded)
   └─ NPC extraction/Profile update and transient-alias pruning
   join -> exact-name World Setting appearance count
-> Director subsystem
   Director Event review -> Director Agent confirmation stage
-> journaled commit/finalization
-> background summary condensation
```

The Director subsystem runs for every real confirmation (`write_db=true`); a
dry-run (`write_db=false`) skips it. All pre-existing active Events are reviewed
first, followed by the confirmation Agent. The Agent observes updated CurrentState, Scene Facts, compact
resource catalogs, selected detailed NPCs, and the current turn's newly detected
NPCs.

Finalization persists CurrentState, the turn summary, short-term scene data,
Confirmed History documents, and Continuity operations, then completes the
pending transaction. NPC updates, Setting mention counters, Event reviews,
staged disclosure changes, and Director skill writes performed earlier in the
pipeline remain protected by the same confirmation snapshot; Director Wave
mutations share the same single `campaign.db` transaction and have no separate
journal.

Accepted NPC stable field changes are committed as field-change logs in the same
finalization step, against the same confirmed scene-paragraph documents that
back Continuity (one atomic SQLite transaction per turn, idempotent per batch
hash; see §10). The `npc_field_logs_saved` completion marker guards replay and
is cleared on a confirmed-history reset.

## 6. Core Memory

`CoreMemory` version 2 is a compact list of typed items:

- `world_invariant`
- `protagonist_invariant`
- `narrative_preference`
- `system_convention`
- `ability_rule`

Each item has an ID, `hard_constraint | strong_preference` strength, and
`player | campaign_setup` source. It stores stable constraints, not scene
history, active objectives, or evolving plot state.

The `source` label is advisory provenance, not a security boundary. The editor
server preserves the source of existing items by matching their ID and marks
only newly added items as `player`, but nothing meaningfully prevents a client
from achieving the same outcome another way (for example deleting a
`campaign_setup` item and adding an equivalent `player` one, or editing the
store file directly). There is no low-cost hardening that would make the label
tamper-proof without an immutable, server-issued identity registry, which this
local, creator-only surface does not warrant.

Initialization creates identity, world style, ability system, protagonist,
story-expectation, failure-severity, currency, public power, peoples, conflict,
power-group, and civilization items from the validated setup contracts.

## 7. Current State and Scene Facts

`CurrentState` represents only the present:

- scene ID, location, and unique participants;
- world day and phase;
- protagonist name and visible status;
- inventory, money and unit, fixed assets, and temporary assets.

`SceneFactExtractor` converts confirmed prose into one structured fact set.
Profiles, Continuity, and Director confirmation consume that shared projection
instead of independently reinterpreting the prose. The StateUpdater instead
reads the confirmed prose paragraphs directly and freezes a `FinalSceneFrame`
scene-ending snapshot (final location, day offset, final phase, present and
departed NPCs) that the deterministic diff engine compiles into
location/participant/clock operations; Scene Facts remain only an auxiliary
reference for it. Raw accepted prose remains available where exact text is
required, including Confirmed History, short-term history, per-turn summary,
and the StateUpdater paragraph evidence.

## 8. Confirmed History, Context Facts, and Canon Validation

`ConfirmedHistoryStore` persists each confirmed player input and each confirmed
scene paragraph as a stable `HistoryDocument`. Its trigram FTS5 index is the raw
evidence foundation shared by Context Facts and Canon validation; Continuity
stores references to its scene-paragraph document IDs.

`ContextFactsService` owns one generation graph node and internally performs:

```text
deterministic ContextSearchPlan
-> ContinuityHistoryBridge (structured forward links only)
-> NPCProfileHistoryBridge (accepted field-change nodes only)
-> high-recall ContextHistoryRetriever
-> targeted ContinuityRetriever
-> deterministic SettingRetriever (at most 4 public World Setting views)
-> one ContextExtractor LLM call
-> at most 12 compact facts
```

Context Facts are an incremental context layer, not a restatement of inputs that
downstream consumers already receive. The extractor is softly instructed to
avoid spending fact slots on content already explicit in Core Memory,
CurrentState, or the current player input, while retaining the freedom to cite
those sources when needed to express a historical relation, knowledge boundary,
or contextual conflict.

Search intents use player input, trimmed and de-duplicated Action Parser
retrieval topics, current location and participants, deterministic
matched-profile anchors, selected NPC disclosure views, and aliases. Parser topics become
`explicit_input` topic intents; this origin records that they derive from the
player turn even when the parser uses a canonical name or compact semantic
rewrite grounded by CurrentState or Core Memory. Deterministic phrase extraction
from the raw player input is used only when the parser yields no topics after
structural cleanup. A parser topic that exactly matches a known entity name or
alias promotes that entity intent to
`explicit_input` and is not duplicated as a topic; an unresolved parser term
remains an `explicit_input` topic. History retrieval
accepts single-entity matches, FTS matches, and topic similarity, then ranks by
entity/topic fit, recency, source type, and intent diversity. The number of
retrieved history hits is not tied to the 12-fact output limit; it is bounded
independently by the history diversity policy (currently 10 hits and a 12,000
character serialized history payload).

Every search intent carries an independent `origin`: `explicit_input`,
`matched_anchor`, `current_context`, or `relationship`. NPC names/aliases
directly present in player input and all structurally accepted parser topics use
`explicit_input`; deterministic fallback topics come directly from the player
text. Anchors reported as explicit do likewise, while scene-derived anchors use
`matched_anchor`. If a current participant or location intent already exists and
is later recognized as explicit input, the existing intent is promoted instead
of being discarded by entity de-duplication.
Explicit Chinese event topics may be two characters long; generic question
wrappers such as “关于/有关/针对……的事” are removed before the topic is stored, so
the player phrase “问艾琳关于黑塔的事” produces `黑塔`, not `关于黑塔`.
Anchor kinds route semantically: `character`, `location`, and named
`item`/`organization` anchors become entity/location intents, while `concept`,
`action_keyword`, and `unknown` become topic intents. Only character anchors
may extend the player relationship intent. Direct surface promotion is
conservative for ambiguous one-character Chinese names and aliases: those
require a quoted occurrence or an explicit character anchor instead of an
arbitrary substring match.

History diversity uses one deterministic final selector over a shared pool of
document-level route proposals. Direct retrieval, the Continuity bridge, and the
NPC field-history bridge each rank their own candidates; their raw scores are
never compared. Each proposal
records one selection owner, its `direct` or `relation` route, a categorical
match-signal tier, and its ordinal rank inside that route and intent. A document
found through both routes remains one merged candidate and can consume only one
main-hit seat. Relation channel order within an intent is set by that intent's
`(document, intent)`-scoped support quality. The categorical relation signal
tier for a proposal is derived from the document's merged match-reason set (the
union across the items it is linked to). Consequently, a strong reason
contributed by one linked item can raise the categorical tier of another
proposal for the same document; this tier is distinct from the per-intent
support quality used to order candidates within a relation channel. Per-intent
match reasons (`intent_match_reasons`) are attached to every relation link and
validated so a partially structured mapping can never silently fall back to the
global reason union and inflate another intent's tier; a legacy flat link with
no per-intent mapping simply applies the global reasons to every triggering
intent.

The greedy selector recomputes utility after every accepted document. Its
lexicographic order is intent origin (`explicit_input`, `matched_anchor`,
`current_context`, then `relationship`), remaining owner-intent coverage,
semantic focus, newly covered intents, categorical match specificity, route
novelty, 128-character serialized marginal-cost band, channel-local rank,
stable intent order, recency, exact marginal characters, route, then evidence
ID. Exact direct topic and genuine two-entity matches form the strongest direct
tier; exact entity matches form the next tier; FTS/fuzzy-only matches remain a
lower tier. Continuity subject/premise/current-state topic and two-entity
signals form its strongest tier, followed by exact entity/alias and then
location. Confirmed scene paragraphs are preferred to otherwise equivalent
player-input candidates. This is a deterministic final-selection order, not a
universal retrieval score.

The shared currency of this selector is the public, route-independent layers
that come before any quality dimension: intent origin, remaining coverage,
semantic focus, and newly covered intents. Direct and relation proposals are
never compared by a unified cross-route score, and no direct/relation quota or
round-robin is used: the leading layers decide "which document is most worth
serving" (which intent it serves, how under-covered that intent is, and how
prioritized its origin is), while the categorical match tier and channel-local
rank only order candidates *inside* a route once the public layers tie. The
route itself (`direct` before `relation`) sits near the end of the key purely as
a deterministic tie-break, so it rarely decides the first pick. The quality
dimension therefore stays route-local; cross-route competition happens only on
the shared public layers.

Both direct and relation selections update the selector's independent intent
coverage counters, so a relation route can add useful marginal coverage after a
direct mention. Provenance remains separate: relation-only candidates populate
only `relation_intent_ids`, never direct `matched_intent_ids`. Route novelty is
therefore a selection feature rather than a claim that relation evidence
matched the History text directly. Origin remains the leading key, so lower
origin candidates cannot pre-empt eligible explicit-input evidence.

Selection admits at most two main hits from one turn. The entity cap (currently
three) applies only when a direct entity proposal owns the seat; a document may
still be selected for a genuine topic, location, relationship, or relation
reason. Relation-owned documents instead use a per-relation-owner cap
(`relation_owner_cap`, currently three) that counts against every linked
relation owner — a Continuity item or an NPC profile — without any channel-wide
relation cap. The cap applies only when a relation proposal admits a new main
document; augmentations on already selected documents never consume it. A shared
relation document occupies one global seat but counts against every linked
owner; direct-owned documents are never blocked by this owner cap. Within a
relation channel, proposals rank by node-evidence support quality first, then
by categorical signal tier and owner-local rank; recency (an age-based
`max(0, current_turn - turn)` value for pure-NPC nodes) is only a tie-break
after equal support quality, so a newer weak node never outranks an older
strong one.
Relationship ownership and coverage require at least two entities from that
relationship intent to match the document directly; a document mentioning only
one NPC cannot use the automatically generated player/NPC relationship intent
to bypass that NPC's entity-owner cap.

The character budget is checked against the exact deterministic JSON produced
for `ContextExtractor`, after global evidence de-duplication. The LLM projection
omits empty lists, null values, false truncation markers, and opaque internal
intent IDs from individual History records; non-empty adjacent/supporting
evidence and direct entity/topic match fields remain visible. Internal
Continuity-link IDs, operations, intent IDs, and relation reasons stay out of the
active LLM payload. Continuity and Setting projections
likewise omit empty optional fields, while an empty top-level selection remains
the explicit JSON array `[]`. These compact payloads are built by the dedicated
History, Continuity, and Setting projection functions in
`app/domain/context_facts/views.py`; the full internal retrieval models remain
unchanged for ranking, diagnostics, de-duplication, and evidence-ID validation.
For each eligible candidate the selector previews the exact payload that would
result from admitting it. When a candidate does not fit, optional adjacent and
supporting evidence are removed first. If the main evidence still does not fit
or exceeds the per-evidence text bound
(currently 2,400 characters), the retriever emits a deterministic head/tail
bounded excerpt with `text_truncated=true` and `original_text_chars`; it never
silently passes partial text as complete evidence. If even the minimum marked
excerpt (currently 160 characters) cannot fit, that candidate is skipped and
selection continues with later candidates. A budget rejection is not permanent:
if admitting another main evidence removes a larger duplicate adjacent context
and shrinks the serialized payload, every still-unselected candidate is
reevaluated against the new payload state on the next greedy iteration.

### 8.1 Relation log-bridge design contract

Ordinary admission into the candidate set remains direct-match only: an entity,
FTS, or topic signal must land on the document's own text. Two trusted relation
channels provide the additional entry: the `ContinuityHistoryBridge` and the
`NPCProfileHistoryBridge`. Together they are the **relation log bridges**:
Continuity projects lifecycle-log nodes, while NPC Profile projects accepted
field-change-log nodes. Both normalize into the same internal relation link and
are judged by the same node-evidence quality layer, so their evidence competes
for one shared payload budget while keeping per-source provenance.

The log bridges exist to cross a boundary that text retrieval cannot cross.
Direct retrieval owns all query-to-History lexical competition: if a name,
entity, or topic from the player turn occurs in a History document, the direct
entity/FTS/topic routes are responsible for finding it. A log bridge adds recall
when the relevant History prose uses a pronoun, an indirect reference, a
paraphrase, reported speech, or another expression that cannot be reached by
repeating the query terms. A bridge may still attach structural provenance to a
document already found directly, but that duplicate attribution is not its
reason for adding another copy of the prose.

Bridge retrieval therefore has three deliberately separate stages:

```text
query/SearchPlan intent
  -> trigger the relevant structural unit and log node
     (Continuity matter/lifecycle node or NPC/field-change occurrence)
  -> validate and rank that node's persisted evidence pointers by
     node-to-evidence support
  -> let the validated History documents enter the shared final selector
```

The first arrow is **triggering**. Query text may match the unit's structured
identity, entities, subject, premise, current state, field-change basis, or
other declared trigger surfaces. This match only answers “which matter,
character, field change, or occurrence is relevant?” It is neither evidence nor
a claim that any attached History paragraph contains the query terms.

The second arrow is **association**. Once a node is triggered, its evidence is
judged only against that node's own immutable change/event basis. The relation
route must not compare the player query or retrieval topic with the evidence
text: if such a lexical comparison succeeds, direct retrieval already owns that
signal; if it fails, that failure is precisely why the structural bridge is
needed. Trigger type and recency may break ties between equally supported
candidates, but must not outrank node-to-evidence support. An aggregate current
snapshot must never be used as a historical node basis when it contains facts
retained from other changes.

Node-to-evidence support is a pure function of two immutable inputs — the node's
persisted change/event basis and the confirmed History window text — and is
independent of the query, intent, turn, and payload budget. The same
`(log node, evidence document)` pair therefore always yields the same
strong/weak/zero verdict, so the result is in principle precomputable and
lazily cacheable keyed on the evidence hash. This is a deliberate non-goal for
now: link counts are already bounded by the bridges, per-turn re-evaluation is
negligible, and an undo/redo re-confirmation rewrites a turn's documents (and
thus its evidence hash), so any cache would need that same hash in its key. If
link volume ever grows enough to matter, revisit lazy caching before widening
the bridges.

The third arrow is **selection**, not retroactive validity. Missing/stale
pointers and complete-basis zero-support links are removed before selection.
Every remaining structurally triggered link is eligible to compete; final hit,
turn, owner-diversity, de-duplication, and serialized-payload budgets may still
decide that a valid candidate is not returned. Resource bounds must not silently
delete already-triggered nodes before node-evidence support is evaluated. Direct
and relation eligibility is scoped per `(document or cluster, intent, route)`:
a direct match on one intent is not proof that another intent's structural
relation is redundant, and direct coverage is not a proxy for whether a
non-zero relation evidence deserves to compete.

The bridge transports relationships and trusted pointers, not prose or truth.
Structural fields select a route to Confirmed History; only the resolved
`HistoryDocument` is exposed to `ContextExtractor`. When direct and relation
routes resolve to the same document, it is serialized once and retains both
provenance sets. No relation metadata, internal owner, field name, node ID,
match reason, or support score becomes a Context Fact merely because the bridge
carried it.

Eligibility is scoped per `(document or cluster, intent, route)`: a direct
match on one intent is not proof that another intent's structural relation is
redundant, and direct coverage is not a proxy for whether a non-zero relation
evidence deserves to compete. Relation proposals are generated per source
evidence document and intent (source-scoped, pre-cluster) and point their
payload at the cluster representative; every semantic verdict (quality,
class, degraded, match reasons, owner keys, attribution) travels on the
proposal itself, never read from the merged representative admission. The
selector's bookkeeping is proposal-scoped: a direct entry credits all its
directly matched intents, a relation entry or augmentation credits only its
own intent, so one winning proposal never marks another intent as covered.
Relation provenance is materialized only from accepted proposals whose source
evidence survives the final payload (main plus supporting evidence) — a ghost
slice whose evidence was stripped by budget compression never owns a seat,
spends owner budget, advances coverage, or appears in the output.

The precise current mechanics of relation admission: a document found
directly is admitted through its direct proposal and, once selected, accepts
zero-cost relation augmentations for other intents (deduplicated per
`(document, intent, source evidence)`, accounted once per
`(document, intent, route)`). A purely relation-backed document without any
direct match competes normally through its own relation proposal. Weak
(degraded) relation evidence competes like any other proposal — there is no
direct-coverage veto and no per-intent rescue quota; it is bounded only by
owner/turn/hit/payload budgets and final ranking.

An augmentation adds attribution to an already selected document; it never
adds another main-hit seat or another copy of the payload.
`relation_owner_cap` is checked and counted only when a relation proposal
admits a new main document. Once a document is selected, valid augmentations
are free: they do not check, consume, or evict owner budget, regardless of
whether the document entered directly or through a relation proposal.
Augmentations still require their source evidence to survive the final payload,
are suppressed for zero-support links, deduplicate per
`(document, intent, source evidence)`, and credit coverage at most once per
`(document, intent, route)`.

The following paragraphs describe the source-specific trigger structures and,
where explicitly stated, the current implementation mechanics.

The Continuity bridge structurally
matches SearchPlan intents against the complete current Continuity structure:
entity/alias, subject, premise, the compact current-state snapshot, and location. These
fields only find a route to History; they are not evidence or historical truth.
Archived eligibility is evaluated separately from ranking. Active items may be
recalled by contextual structure; resolved/cancelled items require an explicit
or matched strong entity/event-topic signal, or at least two matched entities.
Location may improve ranking but cannot establish archived eligibility, even
when the same location string is repeated in subject, premise, or current state.
For active items the lifecycle skeleton is the latest update plus create; for
archived items it is terminal plus create. The database selects those nodes
directly and batch-loads their evidence, so 100 lifecycle updates do not cause
100 log reads or 100 evidence queries. Every evidence pointer on a selected node
is proposed in its persisted position order; the unified History selector and
per-owner cap decide which documents survive. Missing documents and non-null hash
mismatches are skipped and counted diagnostically; legacy null hashes remain
usable. Bridge candidates enter the same final proposal pool as direct
candidates while retaining their own route-local order.

The NPC bridge matches SearchPlan intents against accepted field-change nodes:
identity through canonical names and aliases, and semantics through the node's
field basis topic. Only bridgeable stable fields (`last_scene_summary`, `role`,
`appearance`, `faction`) are eligible; CurrentState-synced fields and frozen
personality/ability fields never enter retrieval. Each intent matches a fixed subset of NPC identities, so topic matching is scoped
("AND"): an entity-bearing intent only scans nodes of its identity set, a pure
topic intent scans all active profiles, and an entity intent whose identity did
not resolve is not allowed to scan other profiles. Transient aliases are
excluded from the identity surface and identity conflicts emit all candidate
profiles. Inactive profiles reactivate only through an explicit exact name/alias
match. The current implementation uses the latest node of each open field as the
identity skeleton (the current-state default) and emits **every** deterministically
topic-matched node per `(intent, npc, field)` — there is no pre-support add-back
or link cap, so no legitimately triggered node is deleted before node-evidence
support. `matched_profile_ids` is recomputed from surviving links. Every link
carries its `intent_match_reasons`; the node basis is the change's
**this-change basis**: the batch field updater persists a short free-text
`change_basis` (written by the same LLM call that produced the change) that
describes only what this change actually added / removed / rewrote — never
retained facts. That persisted basis is the node's primary basis, so retained
old facts neither trigger nor support a later node; `last_scene_summary`, which
is replaced once per appeared scene, keeps its whole new value as a
`basis_complete=True` basis. Logs recorded before v9 carry no `change_basis`;
they degrade to the complete `new_value` with `basis_complete=False`
(incomplete-not-false-delta: they fail open instead of pretending a delta),
which keeps legacy rows readable without fabricating history. Legacy v7
`public_summary` logs remain read-only audit rows and are never bridged.

Retrieval diagnostics expose the relation funnel separately: matched active and
archived Continuity counts, valid relation links, deduplicated relation
candidates considered, and final relation hits added, plus two node-evidence
quality counts — `invalid_relation_links` (complete-basis zero-support links
whose attribution was suppressed) and `degraded_relation_links` (links that did
not reach `strong` but remain selectable without a direct-coverage veto or
rescue quota). The NPC channel reports the same shape on its own counts: matched
NPC profiles, and valid/stale/invalid/degraded NPC links plus NPC candidates
considered and hits added. A proposal-level funnel additionally records
`direct_proposals_considered`, source/intent-scoped
`relation_proposals_considered`, `relation_zero_stripped`, eligible strong and
degraded relation proposal counts, documents selected by each entry route, and
accepted relation augmentations on already selected documents. These counters
separate trigger/support eligibility from final seat ownership and
augmentation; none changes selection behavior. The
active/archived limits (`6/4`) therefore apply to Continuity candidates; final
relation-only History output has no channel-wide limit and is constrained by the
shared hit/payload bounds, turn diversity, and the three-document
per-relation-owner cap.

Every Continuity relation link carries a node-specific semantic basis used to
judge node-to-evidence support before any selection. `ContinuityHistoryBridge`
derives it per lifecycle node: `create` uses the immutable `premise` as primary
with no contextual basis; `update` uses `state_delta` plus `reason` as primary
and `subject` + `premise` as context; `resolve`/`cancel` use `reason`. The
aggregated `current_state` is deliberately excluded from these bases so later
item-level information can never broadcast onto an earlier node's evidence, and
the mutable `subject` never back-fills a create node. `basis_complete` is false
only for migration/replay logs, whose generic reasons fail open.

`ContextHistoryRetriever` scores each link over the main evidence plus the
same-turn adjacency window, excluding the node's own sibling evidence (one
adjacency rule, shared with the rest of retrieval). Support keeps a
primary/context split:

```text
combined = max(primary_support, 0.4 * context_support)
quality  = primary_support if primary_support > weak else min(combined, weak)
```

Only the primary basis (premise / state delta / closing reason) can reach
`strong`; contextual background lifts an evidence at most to `weak`, so
item-level semantics never broadcast a node the evidence does not change.
Support class is `strong | weak | zero | incomplete`. A complete-basis link
whose evidence scores zero support has an invalid relation attribution; that
attribution is stripped unconditionally, even when it is the node's only
evidence. This never deletes a HistoryDocument; it only prevents a false link
from claiming that matter.
Incomplete bases fail open. Every non-strong link is degraded. Degraded
(weak) relation evidence competes like any other proposal: there is no
direct-hit eligibility veto and no per-intent rescue quota — non-zero
node-supported evidence expresses its lower quality through ranking rather
than a route hard gate. `entity_cap` only constrains the direct route; the
ordinary turn/owner/hit/payload budgets bound selection for every route.
Quality class is tracked per `(document, intent)` at the source proposal
level, so a strong link on intent A never promotes a weak link on intent B of
the same document.

The bridge stores and transports relationships, not prose. It never copies raw
History text or turns relation metadata into a fact. The active projection does
not expose its match explanation to ContextExtractor. The compact LLM
payload still contains only the resolved HistoryDocument. A document with both
direct and relation signals is merged once by document ID and retains both
provenance sets regardless of which proposal wins its seat. There is no active
`direct History -> Continuity -> sibling History` path.

Adjacent paragraphs only contribute
structured `context_before`/`context_after` records (each carrying its own
`evidence_id` and text, so every visible neighbor fact is also a citeable
evidence ID) and a context-aware score boost — a neighbor hit never independently
creates a candidate. Candidates are still aggregated by `document_id` across
intents. Near-duplicate merging is deliberately the most conservative possible to
maximize recall: `scene_paragraph` candidates merge only when they share the same
turn and scene, are directly adjacent paragraphs, have exactly identical
normalized text (only whitespace, punctuation, and case may differ), match
exactly the same non-empty entity set, overlap on topics, and show no semantic
conflict; `player_input` documents are never merged, and repeated events across
turns always stay separate. This neutralizes sub-word semantic drift (time words,
materials, or Chinese numerals such as "十二"/"二十"), and a shared location
cannot mask different subjects because entity sets must be identical. Merging is
additionally refused on any divergence in negation content, numeric quantity
(Arabic and Chinese numerals), time, state-predicate, or ownership signals
(fail-safe). A representative carries the union of its members' matched
intents/entities/topics and the highest member score, plus
`supporting_evidence_ids` and `supporting_evidence` with the members' full text.
Cluster members are excluded from the representative's adjacent context, and a
final global pass keeps every HistoryDocument at most once across all hits (main
hits and supporting evidence take priority; adjacent context is kept only when
its document has not appeared anywhere yet, assigned in hit order), so the same
document never appears twice in the LLM input — enforced by the evidence-set
schema. The citeable
supporting IDs are exactly the ones whose text is projected, so the extractor
never cites evidence it has not seen; every such ID (including adjacent-context
IDs) is a valid citation target for Context Facts. Clustering only compresses the
LLM projection and never modifies Confirmed History data.

The extractor receives CoreMemory, CurrentState, all summaries, the search plan,
retrieved history evidence, selected active Continuity items, relevant NPC views,
and a bounded selection of public World Setting views. Each output fact
identifies its holder, information source, compact content, and up to four known
evidence IDs.

Canon validation has a narrower purpose. It extracts checkable assertions from
new scene text, retrieves Confirmed History evidence for each assertion, and asks
the consistency agent whether the scene passes or needs one complete source
rewrite. The current draft cannot serve as evidence for itself.

## 9. Continuity Ledger

Continuity stores only:

- `commitment`: an explicit promise, debt, agreement, or obligation;
- `open_thread`: an unfinished goal, threat, mystery, or pending event.

An item contains a compact subject, immutable creation-time `premise`, one
compact mutable `current_state` text snapshot (nullable before the first
update), entities, optional location and deadline, lifecycle status, and turn
metadata. Status is `active | resolved | cancelled`. An `update` supplies both
a concise `state_delta` for the append-only lifecycle log and a
`new_current_state` that completely replaces the previous snapshot. The
replacement preserves still-valid facts and removes or rewrites superseded
claims. Operation-specific schemas expose only the fields valid for that
operation: creation owns `kind/premise`, update owns mutable metadata plus the
delta/snapshot pair, and terminal operations only identify the item. Entity
metadata is monotonic for lifecycle updates: update-provided entities are
additions merged in first-seen order, and an update can never remove an
existing entity. This model-operation rule does not apply to an authorized
manual archive edit, which replaces the entity list and may remove entries.
Creation and each individual lifecycle update accept at most 12 entities, but
the persisted aggregate has no 12-entity ceiling: a long-lived item may continue
accumulating new entities across valid updates. A null update location or
deadline means unchanged; only a non-null value replaces the stored value. The
Projector sees the complete active snapshot
including premise and current state; it does not receive old lifecycle logs or
old raw evidence. If the confirmed turn is unrelated or makes no substantive
change, the correct projection is `operations=[]`.

Creation remains evidence-gated and additionally requires bounded scope: an
item tracks one concrete matter with an observable completion, release, or
failure condition. Open-ended aspirations, abstract development directions,
and indefinitely recurring duties are not Continuity. The Projector must not
invent smaller phase items by decomposing such a goal; a phase item is created
only when the confirmed turn itself explicitly establishes it and it
independently meets the normal creation threshold.

On confirmation, one `ContinuityProjector` call receives the current turn's
deterministically prepared history documents and the complete active ledger,
then outputs a top-level JSON array of `create`, `update`, `resolve`, or `cancel`
operations. The wire contract has no single-field object envelope; after the
array and every discriminated-union item pass schema validation, the Projector
places the validated operations in the internal projection value used by the
confirmation pipeline. Finalization first writes those documents to Confirmed
History, then `ContinuityService`
validates every referenced evidence ID against that turn's persisted
scene-paragraph documents before applying the operations. Each applied
create/update/resolve/cancel operation writes one lifecycle log containing the
item ID, operation, turn, optional update delta, and reason. A separate relation
table stores each reference's stable position, `evidence_id`, and the SHA-256
hash of the trusted HistoryDocument text; Continuity never duplicates the raw
prose. Hashing uses exact `HistoryDocument.text` encoded as UTF-8, without trim
or punctuation/whitespace normalization. Version 1 ledgers migrate their last
`statement` into `premise`, start with a null current-state snapshot, and
preserve legacy evidence relations with an unknown hash. Synthetic logs created
by migration carry `migration_created=true`. The v1 migration rebuilds active
FTS entity text with the same space-joined representation used by normal runtime
writes rather than indexing the raw JSON array. Unreleased list-based development
databases are converted in place under schema version 3; their deltas are kept
as a temporary legacy snapshot until the next real update replaces it.
The relation is indexed and readable in both directions (`item -> lifecycle
logs -> evidence` and `evidence -> lifecycle logs -> item`). Context Facts
consume only the forward direction through `ContinuityHistoryBridge`; the
indexed reverse direction has no active consumer.

Runtime invariants: a Continuity item may be changed at most once per confirmed
turn, and applying the same confirmed turn twice is idempotent (the second
apply is a no-op). Every evidence reference on an operation must name a
`scene_paragraph` document from exactly that turn whose `document_id` matches
the reference; unknown IDs, wrong-turn paragraphs, wrong source types, or
mismatched document IDs reject the whole batch and atomically roll back every
operation of that turn. Evidence order within one lifecycle log is preserved by
a `(log_id, position)` primary key, and a `(log_id, evidence_id)` unique
constraint prevents duplicate references; together with the
`continuity_processed_turns` marker these constraints make re-application and
interrupted retries deterministic.

During generation, `ContinuityRetriever` selects at most eight relevant active
items using action anchors, participants, location, deadline, lexical terms, and
the ledger's FTS index. The ledger stores structured state and references; it
does not duplicate long scene passages.

## 10. NPC Profiles

NPC Profiles separate:

- public identity and continuity fields used by player-facing narrative;
- Director-only motivation, secret, behavior, provenance, and note fields.

Ordinary public extraction and updates are grounded in Scene Facts. Scene Facts
additionally emit `npc_mentions` — one identity-bearing `primary_surface` plus
explicit alias relations (`explicit_fixed_alias` / `temporary_address` /
`same_scene_coreference`) with fact and scene-paragraph evidence. Generic pronouns are used only for local coreference while extracting and never enter
the downstream identity contract, so the NPC pipeline avoids both a separate
extraction call and pronoun-driven evidence expansion. Newly introduced spontaneous NPCs
from the generation Director (`introduce_npc(spontaneous=...)`) are automatically
backfilled in confirmation via `auto_backfill_spontaneous_npc_profiles` with deep
character motivations anchored to a drawn `desire_pattern`, while locking their
public name and shell.

Per turn the pipeline is: deterministic identity resolution
(`resolve_npc_identities`) maps mention clusters to existing profiles (unique
name/alias hits, alias-relation joins, and unambiguous text-scan union fallback;
unmatched named people in prose remain unpromoted ephemeral scene mentions; the player
cluster is always skipped; ambiguous groups are omitted rather than risk mis-attribution),
then a single batch field-updater LLM call (`npc_batch_update`) emits only `npc_id → field
changes` for already-resolved NPCs — it never judges mention/alias/new/ignore and
never fabricates full new profiles.

The batch updater splits existing-profile changes into two channels:

- CurrentState sync: `last_known_location` and `attitude_to_player` are
  deterministically synced from the updated CurrentState (never written by the
  updater).
- `stable_changes`: stable identity fields (`role`, `appearance`, `faction`).
  Each change is atomic and must carry structurally valid evidence referencing
  this turn's confirmed scene paragraphs (the same `build_turn_documents`
  documents that back Continuity, referenced by `document_id`, matching the
  current `turn_index`) and/or Scene Fact ids. `None` means unchanged and
  whitespace-only strings are normalized to `None`, so fields cannot be cleared;
  explicit clearing is not supported.

`last_scene_summary` is a dedicated program + evidence record written for each
appeared NPC on every confirmed turn (see Ticket 12). The updater emits an
independent per-turn summary draft in the `scene_summary` slot, and admission
writes `last_scene_summary` together with the program-derived `last_scene_turn`
in one atomic step; mentioned-only NPCs never receive the pair. The updater
never projects the previous summary back into the LLM input, and it never
fabricates a summary for `mentioned_only` NPCs. The pair is projected
read-only to the player surfaces (records drawer and clickable NPC highlight
card); it is never editable through record updates or any other player surface.

Admission is per-entry / per-field fault tolerant (宁漏勿错): an unknown or
unresolved `npc_id`, an invalid change, or a bad evidence reference drops only
that entry or field and is recorded as a diagnostic plus a confirmation event;
CurrentState synchronization runs independently; only program errors (scene
mismatch, unknown profiles, blank aliases, cross-profile alias collisions) fail
the whole batch. Accepted stable fields carry their evidence
(fact ids and paragraph ids) and are persisted as a field-change log during
confirmation finalization (below).

`personality_trait` remains a curated characterization field consumed by the
player record and face-to-face narrative disclosure, but ordinary per-turn NPC
updates cannot overwrite it. It is written only during NPC creation, Director
enrichment/backfill, or explicit player editing; a single scene's behavior is
not treated as a stable personality change.

`known_abilities` remains part of the NPC profile and is written by Director NPC
creation/enrichment, backfill, and explicit player editing. Ordinary per-turn
`npc_batch_update` cannot mutate it because single-scene behavior and uncertain
expertise are not accepted as permanent append-only abilities.

Deliberate trade-off: the generation Director curates the roster. NPCs are introduced
exclusively in the generation phase via `introduce_npc` (either from prepared drafts or
as a spontaneous face-to-face creation). Background extras and unnamed characters in prose
that are not introduced by the Director are not persisted as permanent NPC profiles,
preventing unbounded roster bloat. Spontaneous NPCs introduced by the Director are
automatically backfilled and persisted during confirmation. Staged Director draft NPCs
are promoted into `NPCProfile` with `source_director_npc_id` provenance.

Known limitation: the evidence gate is structural only. It verifies that each
cited `document_id` is a confirmed scene paragraph of the current turn (with a
matching `turn_index`) and that fact ids reference the current Scene Facts, but
it does not judge whether the evidence semantically supports the field or the
proposed value (nor whether it mentions the NPC), so a real but irrelevant
paragraph about the same NPC can still back a change. Program-level semantic
support judgment is intentionally absent at admission: paragraph-to-field
correctness relies on LLM instruction. Hash integrity and per-field provenance
are enforced when the field-change log is persisted; a dropped or applied
field is diagnosable per-turn via the pipeline diagnostics log and confirmation
events. Diagnostics and dropped-field confirmation events are emitted exactly
once per computed update, even when the confirmation retry cache later serves
the same result.

### 10.1 Field-change log (schema v8)

Accepted stable changes are written into the NPC profile database during
confirmation finalization, in the same commit step as Confirmed History and
Continuity, using the confirmed scene paragraphs as evidence. The v7 schema
added three tables (additive v6→v7 migration); v8 keeps the same tables, adds
`last_scene_summary` to the writable field-name set, and keeps legacy
`public_summary` rows as read-only audit data (they are never re-written and
never bridged):

```text
npc_field_change_logs
  log_id PK, npc_id FK ON DELETE CASCADE, field_name, turn_index,
  old_value, new_value, summary_delta nullable,
  change_basis nullable (this-change basis; v9),
  source (only "proposal"), UNIQUE(npc_id, field_name, turn_index)

npc_field_change_evidence
  log_id FK ON DELETE CASCADE, position, evidence_id, evidence_hash,
  PRIMARY KEY(log_id, position), UNIQUE(log_id, evidence_id)

npc_field_change_processed_turns
  turn_index PK, batch_hash
```

- Accepted `role` / `appearance` / `faction` stable changes persist a short
  free-text `change_basis` describing only what this change added / removed /
  rewrote (written by the same LLM call that produced the change);
  `last_scene_summary` persists its whole new value as basis. The basis is used
  at retrieval time as the node's primary evidence basis; it is never derived
  from a character diff, so retained facts cannot trigger or support a later
  node.
- The write API requires a non-empty `change_basis` (at most 80 characters) for
  every accepted `role` / `appearance` / `faction` change — enforced at the
  accepted-change model, the prepared write model, and defensively in
  `prepare_accepted_field_changes`. `NULL` is therefore un-creatable through
  current writes; it can only appear on rows migrated from v8, which read back
  with `basis_complete=False`.
- v8→v9 migration adds the nullable `change_basis` column in place (additive,
  idempotent); legacy rows simply read `NULL` and degrade to
  `basis_complete=False` at retrieval instead of fabricating a delta.

- Evidence hashes reuse `history_document_text_hash` over the exact UTF-8 text
  (no trim or normalization), so later text edits break the pointer instead of
  silently re-attributing semantics.
- One turn's logs, evidence, and processed marker are written atomically in a
  single SQLite transaction; any unknown ID, non-scene paragraph, wrong
  turn/scene, or duplicate reference rolls back the whole batch.
- The same turn and batch hash replay idempotently; a same-turn different batch
  outside the confirmed-history reset path fails closed. Exactly one node exists
  per `(npc, field, turn)`.
- Only `source="proposal"` changes with non-empty paragraph evidence that
  actually changed the snapshot are logged. The writable set is
  `role` / `appearance` / `faction` / `last_scene_summary`; CurrentState-synced
  fields and the frozen personality/ability fields are never logged. Nothing is
  backfilled for pre-v8 history because no trusted evidence attribution exists.
- v7→v8 migration rewrites each `npc_profiles.profile_json`: when `role` is
  empty (null, blank, or whitespace-only) and a legacy `public_summary` exists,
  the summary initializes `role`; otherwise `role` is kept; legacy
  `public_summary` / `current_intent` keys are dropped; the new
  `last_scene_summary` / `last_scene_turn` pair starts null and no last-scene
  history is fabricated. The field-history table is rebuilt (SQLite CHECK cannot
  be altered in place) with the v8 field-name set while preserving existing
  `log_id` values and evidence pointers, then a `PRAGMA foreign_key_check`
  guards against orphaned rows.

### 10.2 Field-history retrieval

`NPCProfileHistoryBridge` turns these logs into relation candidates for Context
Facts under the trigger/association/selection contract in §8.1. It matches
SearchPlan intents against the identity surface
(canonical names, permanent aliases; transient aliases excluded) and the field
basis topic and scopes topic matching to the intent's resolved identities.
Identity and topic matching trigger field-change nodes; they never claim a
lexical match against the attached History evidence. Inactive profiles require
an explicit exact name/alias input. Every link carries its per-intent match
reasons, and missing evidence or stale hashes are skipped diagnostically. The
trigger matcher and node-evidence support are deterministic and add no LLM call;
no internal NPC IDs, field names, reasons, or scores enter the ContextExtractor
payload. All deterministically topic-triggered nodes enter node-evidence
support (no pre-support add-back or link cap); the primary node basis is the
persisted `change_basis` for `role` / `appearance` / `faction` (with no
full-value context backfeed, so a later change's aggregate value can never
support an earlier node), `last_scene_summary` keeps its whole-value
`basis_complete=True` basis, and legacy no-basis logs degrade to
`basis_complete=False` (incomplete-not-false-delta, fail open) instead of
claiming a delta; relation ranking
uses node-evidence support before trigger tier and recency (recency only as an
equal-quality tie-break), per the §8.1 contract.

CurrentState participant matching, canonical names, aliases, and transient
aliases drive profile selection. `name_hit_count` and per-alias hit counters mark
whether a profile has appeared in confirmed prose; transient aliases left behind
by renames are pruned automatically after a few turns.

Every profile has a persistent disclosure level
`remote < face_to_face < intimate`. Remote exposes identity plus the
`last_scene_summary`/`last_scene_turn` pair, social style, and attitude;
face-to-face additionally exposes appearance,
personality, conflict style, and known abilities; intimate exposes the full
Director view. Generation may stage a one-way upgrade for immediate use, and the
upgrade is persisted only when that pending variant is confirmed.

## 11. Director Agent Common Layer

The common layer consists of:

- `DirectorObservation`: phase-neutral world and resource observation;
- `DirectorSkillSpec`: phase, mutation permission, description, and argument
  schema;
- `DirectorDecisionBatch`: at most six ordered skill requests;
- `DirectorSkillService`: registration, argument/resource validation, and
  execution;
- `DirectorAgent`: phase-specific generation or confirmation decision call;
- `DirectorStageResult`: read-only materials admitted to composition.

Generation and confirmation use separate Chinese decision prompts and trace task
names. Every non-empty skill request requires request-level `guidance`; empty
request batches are valid. Mutating skills cannot be registered for generation.

### Recent Director Log

A bounded per-save `director_log` record keeps the last three confirmed turns of
each phase's decision (`reasoning` plus programmatically assembled measure
summaries, with resource names resolved from stores).
The generation decision is attached to its pending turn variant
(`director_log_entry`), so rerolls and rephrases each carry their own entry and
only the confirmed variant reaches the durable log. At confirmation commit the
confirmed variant's generation entry and the confirmation result are written as
one `director_log` transaction inside the same `campaign.db` transaction that
commits the rest of the turn. Generation
records requested measures; confirmation prefers actual execution results such
as created IDs, titles, and actor names. Log persistence retries three times and
then fails open with a diagnostic rather than invalidating an otherwise committed
turn. Older turns are pruned.

Both Director observations receive the recent log (`recent_director_log`, newest
first, at most six entries): generation reads confirmed cross-turn history, while
confirmation also prepends the current variant's generation entry. The log is
advisory continuity context — agents must not re-execute logged measures,
confirmation must not mirror an already-introduced NPC, and generation should not
prematurely surface assets confirmation is still cultivating.

The Agent chooses and coordinates skills. Specialist Event, NPC, Setting, and
profile services retain ownership of their schemas and content generation.

## 12. Director Agent Generation Skills

Generation exposes four read-only skills:

- `present_event(event_id, hook_id)` for one unconsumed hidden-event Hook;
- `intrude_engaged_event(event_id)`, at most once per decision batch;
- `introduce_npc(npc_id=None, spontaneous=None, view_mode="face_to_face")`: accepts either a prepared Director NPC draft ID (`npc_id`) under Draft-First policy, or a lightweight JIT spontaneous character card (`spontaneous`) when no draft matches; spontaneous creation strictly requires `view_mode` of `face_to_face`;
- `upgrade_disclosure(npc_id, new_disclosure_level)` for an existing profile.

The observation includes player input, projected Action Judgments, CoreMemory,
CurrentState, Context Facts, recent summaries/logs, active hidden and engaged
Events, prepared Director NPC drafts, and selected NPC Profiles. NPC Profiles are
the bounded union described in section 4 rather than
the full store. Introduced Director NPCs are no longer observable as drafts; they
remain visible through their promoted profiles.

An Action Extractor hit on a hidden Event creates a read-only
`touched_event_candidates` projection. The Director observes that Event in its
engaged list with `touched_this_turn=true`; this does not change the database and
does not automatically expose the Event to Composer. Persisted engaged Events and
touched candidates reach Composer only when the Director selects
`intrude_engaged_event`. This per-turn foreground admission is separate from the
Event's persistent lifecycle stage.

Each successful request produces one `DirectorStageMaterial`:

```text
kind: event | engaged_event | npc
resource_id
guidance
payload
```

The schema continues to accept legacy `kind=setting` materials only so pending
turns created before the skill removal can still be restored; generation no
longer produces them.

`guidance` tells Composer how to use the admitted material without deciding the
protagonist's response or prewriting prose. For `introduce_npc`, the Agent selects
a `view_mode` — `remote`
(remote/indirect contact), `face_to_face` (in-person or combat), or `intimate`
(deep exchange or mind-reading) — instead of picking individual fields. The
backend enforces a hard-coded whitelist per mode as a physical firewall:
`remote` sends identity context plus `last_scene_summary`, `social_style`, and
`attitude_to_player`; `face_to_face` additionally sends `appearance`,
`personality_trait`, `conflict_style`, and `known_abilities`; `intimate` sends
the full view including `core_motivation` and `hidden_secret`.
`upgrade_disclosure` uses the same whitelist, makes the higher view available in
the current composition, and stages the persistent level change for confirmation.
Only fields that exist and are non-null reach Composer.

Materials are permissions and constraints, not mandatory plot beats. Generation
does not consume Hooks, change Event stages, promote NPC drafts, persist
disclosure upgrades, or alter Setting state.

## 13. Director Agent Confirmation Skills

Confirmation exposes three mutating skills:

- `create_npc(setting_requirements[1])`
- `create_event(role, setting_requirements[1], actors)`
- `bind_event_actor(event_id, npc_id)`

`create_event` is the only skill that creates an Event. Its `actors` list is a
discriminated union: `source=existing` references an active Director NPC or NPC
Profile by `npc_id`, while `source=new` carries the guidance used to create a new
Director NPC. Existing and new actors may be mixed; there are at most six actors
and at most three new actors. An empty list creates an environmental,
institutional, or natural event without a concrete NPC cast.
The skill is registered only when no hidden or engaged Event exists and permits
at most one request per confirmation batch. Other confirmation skills remain
available while the Event slot is occupied.

The backend assigns every requested actor a stable request-local `actor_key` and
passes resolved existing-NPC Director views plus new-NPC guidance to the Event
Selector as mandatory inputs. The Selector must return exactly one
`role_in_event` for every supplied key; an output validator rejects missing,
duplicate, or invented actor keys. The backend, not the Selector, assembles the
final actor references. New NPC creation additionally receives its validated
event role. `subject` is the Event's core causal identity; the actor list is the
concrete cast. Independent NPC assets use `create_npc` alone.

The confirmation observation exposes compact identity rows for all profiles,
but full Director detail for at most eight profiles selected from Scene Facts and
active Event actors. It separately carries prepared drafts,
active Events, and compact Setting references. This keeps resource matching
possible without importing every full NPC profile.

The confirmation decision prompt requires `create_npc` to use
structured guidance with `name`, `purpose`, and `description`; `create_event` and
the other skills use free text. When structured guidance is supplied, the common
skill layer validates the full object, but specialist handlers receive only
`name: purpose`; `description` is intentionally discarded as a honeypot for
unbounded model elaboration.

Every `create_npc` and `create_event` request carries a
`setting_requirements` array of exactly 1 entry. Each entry declares a `kind`
(`faction | location | item | lore`), a `setting_description` describing the
Setting itself (not the NPC or event), and either an existing `matched_setting_id`
(resolved against the turn-start Settings snapshot) or `matched_setting_id` left
`null` when no adequate Setting exists yet.

Confirmation executes Director requests in two waves. Wave A resolves the one
Setting requirement attached to each dependent request. A valid turn-start
`matched_setting_id` is reused; `null`, an unknown ID, or a kind mismatch causes
Setting creation. Missing requirements with the same `(kind,
setting_description)` share one Setting Creator call; distinct groups are created
independently with at most three workers. Each call receives the same compact
turn-start Setting catalog for deduplication. Generated Settings remain in memory
while their IDs are injected back into every originating request.

Wave B then executes all Director requests concurrently with at most three workers.
`create_npc` and `create_event` receive their resolved Setting,
including facts, in the same confirmation turn. Request ordering is preserved in
the execution result. Semantic conflict prevention belongs to the upstream
Director contract. Handlers read through the confirmation's operation-level
stores and shared SQLite connection, but no Wave worker writes to a store. After
all requests succeed, the accumulated Settings, NPC drafts, Events, profile
updates, and actor bindings are committed in deterministic order inside the
single `campaign.db` Confirmation transaction. A handled failure rolls the whole
batch back via the surrounding transaction; there is no separate durable
before-image journal.

The Event Selector requires exactly one resolved Setting and receives each
still-unused fact from it as
`{fact_id, content}`. Fact IDs are stable within a static Setting (`fact_1`,
`fact_2`, ...). Its structured result must return at least one
`selected_setting_fact_ids` through the required
`DirectorEventSelectionOutput` schema; the output validator rejects duplicate or
unavailable IDs. The selected facts must ground `intent` or `method` and derive
observable Hooks. Public facts may be presented directly; hidden facts may drive
backstage causality but Hooks expose only their observable consequences or traces.
The confirmation Director's compact Setting catalog marks
`event_creation_available`; a dynamic `create_event` argument validator rejects
an existing Setting whose Facts are all used, while leaving that Setting available
to other skills. A genuinely missing Setting remains valid and becomes eligible
after Wave A creates it with concrete Facts.
For conflict avoidance it also receives every existing Event's compact causal
core; resolved Events additionally expose `resolved_reason` and
`final_world_impact`, so a new Event can respect the actual settled outcome.

## 14. Director Event

`DirectorEvent` is a long-lived world event with a single stage
`hidden | engaged | resolved` and:

- role `arc | ambient`;
- a persistent Core Intent: `subject`, `intent`, `method`, and one-line
  `macro_intent`;
- a consumable Hook Pool: each hook has a kind (auditory/visual/rumor/
  item/environment), content, trigger anchors, and a `consumed` flag;
- an actor list: each entry carries a `name` and an optional `DirectorResourceRef`
  to a bound NPC;
- `source_setting_ids` and `source_setting_fact_ids`, recording the Setting and
  exact Facts used to instantiate the Event; legacy Events default to no Fact IDs;
- `world_impact`: the permanent world change applied if the player ignores the
  event; it is stored on the Event and is not applied to Setting persistence;
- `deadline_clock`: an absolute `WorldClock(day, phase)` deadline computed by
  the backend from the Selector's bounded `duration_phases` (`1-140`); turn indexes are
  never used for expiry;
- `final_world_impact`: the actual settled impact, or null when no automatic
  impact is applied;
- resolution reason and turn.

The store can read legacy saves containing multiple active Events, but the
confirmation creation gate allows no new Event while any hidden or engaged Event
exists and creates at most one per batch. Legacy active Events are not deleted or
forcibly resolved; they drain through the normal lifecycle. Events follow a
strict three-stage lifecycle:

```text
hidden -> engaged -> resolved
```

After normal confirmation review, the updated CurrentState World Clock is
compared with each active Event's `deadline_clock`:

- a hidden Event that remains hidden at expiry resolves with its pre-generated
  `world_impact`;
- a hidden Event engaged in the expiry scene is immediately included in the
  same turn's engaged-review batch;
- an engaged Event explicitly resolved by review closes without automatic
  impact;
- an unresolved engaged Event at expiry closes without impact when the player
  is still concretely intervening, or closes with a reviewer-derived
  `final_world_impact` when the player has disengaged.

Merely remaining nearby or mentioning an Event is not continued intervention.
`world_impact` is available to Director/reviewer judgment but is not included in
the Composer's admitted engaged-event payload.

During generation, the Action Extractor's single LLM call judges whether the
player action directly targets a hidden Event's visible traces. A hit produces
the read-only `touched_event_candidates` projection described in section 12; no
database write or automatic Composer admission occurs.

All Event database writes are owned by the confirmation-phase Director
subsystem: mandatory event-review workflows update existing Events, while
mutating Agent skills create Events or bind actors. Review runs before the Agent
whenever eligible active Events exist. The review is not a skill: the
confirmation pipeline reviews each
pre-existing active event (created before the current turn) in two batched LLM
calls — one covering all hidden events and one covering all engaged events — so
the number of review calls per turn is bounded regardless of how many events
exist. A hidden event's review detects which hooks were adopted or presented
this turn (marking them consumed) and whether the protagonist contacted the
event — `keep_hidden` leaves it hidden, `engage` advances it to engaged. An
engaged event's review judges whether its core conflict resolved — `engage` keeps
tracking, `resolve` closes the event immediately, including before its deadline.
Defeat or capture of the subject, permanent loss of the method, and an achieved or
objectively impossible intent are terminal; temporary setbacks are not. Because
review precedes the confirmation Agent, a resolution reopens the single Event slot
in the same confirmation turn. Hook consumption and lifecycle action are
independent decisions. Each batch result must cover every supplied Event exactly
once. There is no automatic stage flip or hook consumption derived from the
pending turn. Generic `DirectorResourceRef` keeps Event ownership independent
from the Director NPC domain.

## 15. Director NPC and Creative Brief

`DirectorNPCDraft` is a reusable private character asset:

```text
prepared -> introduced -> retired
```

Creation receives CoreMemory, existing/reserved names, the resolved source
Setting including facts, request-level Director guidance, the current turn, and
a programmatically drawn `CreativeBrief`. The brief varies world connection,
life phase, desire pattern, and baseline temperament. Guidance owns the intended
identity/function; random dimensions may enrich internal characterization but
cannot override confirmed facts or guidance. In the Director confirmation
workflow, creating an independent draft or a new Event actor always requires one
resolved Setting.

Generation observes and may stage only `prepared` drafts. Confirming a staged
draft marks it `introduced`, creates or merges its NPCProfile, records the
selected initial disclosure level, and adds its identity to the confirmation
projection without exposing private information to player-facing projections.
The staged NPC is not pre-added to CurrentState participants: whether the
character is actually present is decided by the normal StateUpdater from the
confirmed prose's scene-ending snapshot, so a Director NPC that is merely
mentioned in dialogue is not registered as a scene participant.

## 16. World Setting

World Setting is the reusable non-character world-knowledge layer. Ordinary
one-off objects, places, and organizations remain in confirmed prose/history
unless initialization or the confirmation Director explicitly creates a durable
Setting.

`WorldSetting` contains:

- kind: `faction | location | item | lore`;
- unique name (at most 10 characters) and summary (at most 100 characters);
- public and Director-visible hidden facts, each at most 100 characters;
- optional explicit relationships to existing Settings;
- creation turn, timestamps, and a persistent exact-name `hit_count`.

Settings are static in the current contract. They are created by the Opening
Setting flow or confirmation Director through the same specialist Setting
Creator; ordinary scene text never semantically extracts, creates, or evolves a
Setting. Confirmation does perform one deterministic appearance operation: when
an eligible Setting name occurs in accepted scene text, its `hit_count` is
incremented once for that turn. A Setting created later in the same confirmation
is initialized with `hit_count=1` if its generated name already occurs in the
accepted text.

Static Fact strings remain the canonical Setting storage format. Event selection
assigns IDs only in its input projection. Before a later Event is instantiated,
the Selector collects `source_setting_fact_ids` from prior Events using the same
`source_setting_id`. Used Facts remain visible as `{content}` context but lose
their `fact_id`; unused Facts remain `{fact_id, content}`. This preserves factual
context while preventing the same Setting Fact from grounding multiple Events.

During generation, a deterministic SettingRetriever may admit at most four
relevant Settings into Context Facts as public views only. Hidden facts never
reach Context Facts through retrieval. Confirmation Director observations and
Setting Creator deduplication receive compact references without facts;
NPC/Event specialists receive the one resolved Setting including facts. Composer
receives public Setting views only through Context Facts; hidden Setting facts
must first become Event causality and can reach the scene only through observable
Event Hooks or an admitted engaged Event.

## 17. Narrative Composition and Rewrite

`NarrativeComposerAgent` performs outcome realization, planning, and prose in
one structured LLM call.
Its context contains:

- player input and deterministic Action Judgments;
- CoreMemory and CurrentState;
- Context Facts;
- selected NPC disclosure views;
- `DirectorStageResult` (which may contain no admitted material);
- only the engaged Events admitted by `intrude_engaged_event`;
- the most recent confirmed short-term scene.

The output contains one concrete resolution fact per action, a stopping point,
one to four planned events, scene reasoning, scene prose, closing prompt, and up
to five next-action directions. Every Action Judgment must be covered exactly
once without changing its outcome band, and every action must be represented by
a planned event. `director_reason` records how admitted Director material
affected the plan and may be empty when the Director decision admits no
material. An empty `DirectorStageResult` does not mean that the Director stage
was skipped.

All structured LLM tasks receive a compact JSON Schema with Pydantic-generated
`title` metadata removed but validation constraints preserved. `LLMGateway`
appends a generic anti-copy contract whenever `output_schema_json` is supplied,
forbidding Schema definition keys in the returned instance. If a weak model
still wraps otherwise valid data in `$defs/properties/type` scaffolding, the
gateway extracts same-named root data fields when present, otherwise fields
nested under `properties`, validates the candidate normally, and records the
effective validated shape in the trace; unsalvageable output follows the normal
retry path.

Rephrase reruns the entire Composer with the saved authoritative context; prior
resolution facts, events, and prose are not frozen as an intermediate contract.
Feedback rewrite first
chooses `revise_from_source`, `recompose`, or `no_rewrite`; it cannot change the
player action or deterministic outcome band. When Canon validation is enabled, any
new composition/rephrase/rewrite passes through the same Canon tail and may
receive at most one additional source rewrite.

System Prompt templates strictly adhere to a **Static-First 4-Tier layout**
(Tier 1: static instructions, schemas, anti-copy and format rules; Tier 2:
semi-static CoreMemory and world baselines; Tier 3: dynamic Context Facts,
current state, and NPC views; Tier 4: turn-specific player inputs and scene
text) to maximize server-side KV cache hit rates across LLM providers.
`PromptLoader` provides thread-safe in-memory template compilation caching with
per-dependency `_FileStamp(mtime_ns, size)` hot-reloading and dynamic file
discovery, ensuring zero content reads on warm calls.

`TokenUsage` captures provider `cached_tokens` (supporting OpenAI/DeepSeek,
Anthropic, and Gemini usage payloads) and exposes computed `cache_hit_rate`
across pipeline operations and traces.

## 18. Player, Debug, and Operation Surfaces

The player surface provides:

- initialization;
- generation, reroll, rephrase, feedback rewrite, scene editing, confirmation,
  and pending-scene discard;
- paginated confirmed history, NPC highlights, Action Preview, dice
  visibility, and token usage;
- save create/switch/rename/delete, ZIP import/export, and Undo/Redo;
- clickable public NPC/Setting highlights in current and historical prose;
- a live Records drawer for Continuity, NPC Profiles, World Settings, and (in
  creator mode) a read-only Director Event browser.

The HTTP player API uses explicit allow-list projections and never exposes server
file paths. Highlights contain only public NPC/Setting fields. The player HTTP
routes derive their projection tier from the server-owned `experience_mode`; the
lower-level runtime helpers are not themselves an HTTP authorization boundary.

Experience mode is the single surface switch, toggled from the settings drawer
in both modes. It changes interface visibility and the server-owned records
projection tier only; it never disables or downgrades the shared Director
generation/confirmation quality flow. It maps one-to-one onto the records tier:
player mode always projects `general`; creator mode projects `advanced`. The
`view` parameter is no longer accepted from the client: both the records load and
record-update routes derive the projection tier from `experience_mode`
server-side, so no request can reach `advanced` (Director-only fields or hidden
Setting facts) outside creator mode:

- general lists only NPCs with a positive name/alias hit count and Settings with
  `hit_count>0`; it exposes editable NPC public content and Setting
  `name/summary/public_facts`;
- advanced lists all NPCs and Settings, additionally exposing editable NPC
  motivation/secret/behavior/note fields and Setting `hidden_facts`;
- the `last_scene_summary`/`last_scene_turn` pair is a program + evidence driven
  derived record: it is projected read-only in both views (records drawer and
  clickable NPC highlight card) and is never editable — ordinary field edits
  cannot clear or rewrite it, and submitting either key is rejected;
- Continuity is identical in both views. Both premise and lifecycle state are
  displayed, but only active items may edit `subject/entities/location`;
  premise, current state, status, kind, deadline, evidence, and turn metadata
  remain program-owned;
- NPC/Setting IDs, provenance, counters, timestamps, lifecycle fields, source
  links, and disclosure level are never editable. NPC name conflicts are checked
  against canonical names and aliases before save.

Token usage is a normal, reader-controlled surface in both modes: the
projection always carries `token_usage` on confirmed history turns and pending
variants, and an application-level `show_token_usage` preference stored in the
local reader-preferences file decides whether the pipeline/token footers are
displayed. It is not save-scoped or device-identified. Model-service
configuration is infrastructure and is likewise available in both modes. The
player UI hides creator-only surfaces (`data-creator-only` elements: the Event
tab and the Core Memory editor) via a `body[data-ui-mode="player"]` rule.

Creator mode adds two read-only-to-write surfaces backed by dedicated endpoints:

- `GET /api/player/events` lists all Director Events with full detail (role,
  stage, subject/intent/method, hook pool, actors, impacts, deadline, turn
  stamps). It is a browse-only projection; Events remain Director-owned.
- `GET`/`POST /api/player/core-memory` read and replace the Core Memory item
  list. Each item is validated against the `CoreMemoryItem` schema; newly added
  items are always marked `source="player"` with a server-generated ID, while
  existing items keep their original source so campaign-setup provenance is
  never rewritten. Both endpoints reject requests while `experience_mode` is not
  `creator`.

Routine automatic record refresh does not redraw while form controls differ
from their original values, preventing polling from discarding unsaved edits.
Explicit lifecycle refreshes may intentionally redraw the projection. The mode
switch prompts before discarding dirty record edits; callers must save or
explicitly discard edits before actions that intentionally replace the records
projection.

Cost-and-quality controls live in a dedicated settings-drawer section with one
independent switch per capability. Each switch shows a compact one-line hint in
the drawer; a per-switch "ⓘ" button opens a shared popover with the full impact
text:

- Canon post-validation (`canon_validation_enabled`, per-save, default off):
  gating the confirmation tail of generation/rephrase/feedback rewrites. The
  full note explains that disabling it can produce content that conflicts with
  the established world or facts while saving tokens.
- Token usage display (`show_token_usage`, application-level reader preference):
  display-only; the note states it has no effect on generation quality or
  actual cost.
- One-click confirmation (`one_click_confirm`): see below.

Essential NPC/Setting continuity maintenance runs inline in the confirmation
pipeline and has no toggle; it can never be disabled. Budgeting, monetary
estimation, and a model price table are not part of this surface.

One-click confirmation is an opt-in, application-level reader preference
(`one_click_confirm`, default off) surfaced as a toggle whose "ⓘ" popover
carries the risk note (Token consumption, LLM randomness on prose quality,
results entering the confirmation flow directly). When enabled, only the
*user-input generation* path
auto-confirms the default candidate (the latest variant) through the ordinary
`/api/player/confirm` operation after a successful generation — it never fires
for opening generation, rephrase, reroll, feedback rewrite, cancellation, or
generation failure. Recovery may resume an interrupted ordinary user-input
generation; if that resume successfully produces its pending scene, the same
one-click policy runs once after recovery. Initialization, confirmation, and
non-generation recovery never trigger it. A failed auto-confirmation falls
back to the full pending card (multi-version selection and manual editing), so
recovery, retry, and rollback remain available; confirmed turns stay undoable.

Each Pending Turn and Pending Opening also stores the `experience_mode` captured
when it was generated. Changing the current surface mode changes subsequent
records projection and future operations only; it does not rewrite or
re-generate an existing pending variant.

Generation, confirmation, initialization, maintenance, and save work run through
tracked Operations. SSE events may be replayed for recoverable operations. After
confirmation, the already-saved per-turn summary is maintained asynchronously:
each group of ten uncondensed summaries at one level is condensed into the next
level until no level reaches the threshold. Maintenance failure is diagnostic
and does not undo the confirmed turn. The debug surface additionally exposes
graph-step payloads, trace inputs/outputs, Context search/evidence, Continuity
lifecycle operations, state changes, maintenance jobs, pending variants, and all
Director/Setting stores. The Context Facts debug card shows both relation
channels' retrieval funnels: matched/archived Continuity counts, valid/stale/
invalid/degraded links, and candidates/hits for both the Continuity and NPC
field-history bridges; these counts are diagnostic only and never reach the
player surface or the LLM payload.

## 19. Verification

Minimum verification for this contract:

```bash
python3 -m compileall -q app
node --check app/debug_static/app.js
node --check app/player_static/app.js
UV_CACHE_DIR=/tmp/uv-cache PYTHONPATH=. uv run pytest -q
PYTHONPATH=. uv run python scripts/benchmark_context_retrieval.py --check
PYTHONPATH=. uv run python scripts/benchmark_npc_baseline.py --check
```

The Context History benchmark must agree byte-for-byte with the accepted
`benchmarks/context_retrieval/baseline.json` (37 cases, benchmark version 3,
must recall `41/41`, weighted recall `132/134`, and zero forbidden evidence or
invariant violations). The NPC pipeline benchmark agrees with
`benchmarks/npc_baseline/baseline_batch.json` (25 cases, weighted `285/291`,
20 logical calls). `baseline.json` records the
SHA-256 of `cases.json`, so changing cases requires deliberate baseline review;
a passing run by itself is not sufficient justification to update the snapshot.
The byte-for-byte check is also run under at least three `PYTHONHASHSEED`
values to guard against hash-order drift.

Focused contract tests must cover:

- always-on Director generation/confirmation and conditional Canon graph
  compilation;
- phase-restricted Director skill registration and resource validation;
- request `guidance`, NPC disclosure whitelists/upgrades, and deferred
  persistence;
- Event/NPC/Setting independence and explicit coupling;
- Event Selector Fact-ID selection, invalid-ID rejection, persistence, and
  ID removal from already-used Setting Facts;
- Director Event stage transitions, independent hook consumption, deadline
  settlement, touched-candidate projection, and explicit engaged-event intrusion
  (generation never writes event state), plus the single-active creation gate and
  same-confirmation slot reopening after resolution;
- bounded NPC selection for action, Context Facts, Composer, and both Director
  phases;
- two-wave Director request execution, shared missing-Setting groups, deferred
  mutation commit, outer-transaction rollback without a Director journal,
  fail-closed legacy-journal handling, and recent Director logs;
- Context History retrieval versus the 12-fact output boundary;
- the Context History benchmark contracts: gold-group recall,
  `forbidden_evidence_ids` and `forbidden_main_evidence_ids`, direct/relation
  provenance route separation, global evidence-ID uniqueness,
  serialized-payload bounds, and byte-level baseline agreement;
- per-link node-evidence quality: support classification
  (`strong/weak/zero/incomplete`), primary/context basis split, same-turn
  adjacency with sibling-exclusion, complete-basis zero-support attribution
  stripping, and source-scoped proposal eligibility (no direct-coverage veto);
- NPC field-history contracts: identity/alias/conflict resolution, transient
  alias exclusion, inactive explicit-exact eligibility, AND topic scoping,
  per-intent match reasons (partial mappings rejected), complete trigger-funnel
  diagnostics before support, per-change node bases, support-before-recency
  relation ordering, source-scoped direct/relation proposal independence,
  zero-cost augmentation of selected documents, proposal-scoped provenance and
  coverage, final-payload ghost suppression, and the per-relation-owner
  final-selection cap that applies only to new main relation documents;
- Continuity lifecycle application and evidence validation, including the
  runtime invariants (once-per-turn change, idempotent re-apply, current-turn
  `scene_paragraph` evidence with whole-batch rollback) and schema migrations
  v1/v2 -> v3 (`statement` -> `premise`, legacy evidence -> positioned lifecycle
  logs, null legacy hashes, current-state snapshot rebuild, FTS rebuild) with
  reopen persistence;
- Opening Foundation/Setting/scene recovery and normal confirmation recovery;
- Setting appearance counts, player-safe highlights, general/advanced record
  projections and edit permissions, save archives, and Undo/Redo;
- compact Schema rendering, schema-copy defense, Prompt discovery/includes, and
  JavaScript syntax.

## 20. Failure Policy

This section defines the shared failure judgment basis for LLM-dependent
modules. Module-specific fallback values are owned by each module and are
added as modules migrate to this policy; this section does not replace domain
boundaries.

Core principles:

- player content has priority over derived-state completeness: an auxiliary
  LLM module failure must not prevent the player from confirming already
  accepted prose;
- user cancellation and integrity failures may abort; Provider timeouts follow
  the LLM failure policy and degrade; process-crash recovery is governed by
  the recovery mechanisms described elsewhere;
- rollback protects state consistency and never negates player-confirmed
  content;
- a generation main model with no output does not fabricate prose; the system
  preserves player input and offers an actionable path instead;
- after retries are exhausted, soft-truncating free-text fields is preferred
  over dropping the field entirely. Length contracts use three levels per
  field: Prompt Limit H (the quality target shown in the prompt schema),
  Accept Limit A (the first-attempt receive bound — output within `(H, A]` is
  accepted without any length retry), and Salvage Limit S (soft_text only:
  the final keep bound after retries are exhausted; output above S is
  truncated with a `*_truncated` warning). Invariant `H <= A <= S`.
  `strict_name/strict_label` fields have no truncation (no effective S):
  first attempt above A triggers the single feedback retry; a second attempt
  still above A drops the containing item or applies a module-legal fallback.
  identifier/reference/enum fields get no buffer and no truncation and keep
  exact-value validation. The gateway wire schema must let
  `output_validator` see at least S (or leave the string unbounded) so the
  domain layer can classify by attempt; the prompt schema displays H. The
  test-side registry `app/tests/test_llm_length_policy.py` records every
  constrained field as `task + field path + kind + H/A/S` and fails when a
  new constrained field lacks classification.

Processing decisions (not exception types):

- `no_effect`: the failure has no state impact; continue. A warning is
  recorded only when an abnormal item is actually ignored, not for normal
  short-circuits (empty input, no roll needed, no atomic actions).
- `correctable`: the output/evidence/semantics can be corrected; retry once
  with feedback.
- `degradable`: the module returns its legal fallback value.
- `integrity`: storage/transaction/program invariant failure; abort and
  rollback while preserving Pending.

Base policy:

```text
first attempt
  -> no_effect      -> continue (warning only if an abnormal item was ignored)
  -> correctable    -> retry with feedback once
                        -> second failure -> degrade
  -> degradable     -> degrade
  -> integrity      -> abort and rollback, preserve Pending
```

Permanent Provider errors are not a processing decision: the Provider
classifier decides “do not retry”, and the module then chooses `degradable`.
A correctable failure is granted at most one feedback retry; nested retry
stacks are avoided before module migration is complete. This is the Gateway
default (`max_retries=1`), not a global hard limit; the generation H1–H7 call
paths do not override it, so they run at most two model calls per task.

### Scene Facts (Module A)

- `SceneFactSet` carries `degraded` and `degraded_reasons`; normal extraction
  is `degraded=false`, any salvage/fallback result is `degraded=true` with the
  affected component reasons recorded.
- Scene Facts uses a loose top-level container plus component validation:
  - `facts` and `npc_mentions` are validated separately;
  - missing/extra top-level fields, empty `scene_focus`, or more than 12
    `facts` / `npc_mentions` are correctable and enter the one feedback retry;
  - invalid evidence references are reported as correctable, not silently
    removed.
- After the single feedback retry is exhausted:
  - if only `npc_mentions` failed, keep valid `facts` and drop `npc_mentions`;
  - if `facts` failed, keep only `npc_mentions` with valid paragraph evidence
    and strip all fact references;
  - if no usable component remains, return an empty degraded `SceneFactSet`.
- Salvage uses components from one validation attempt only; it never merges
  components across attempts.
- Provider failures, unparseable output, and final validation failure degrade
  through the same rules. Other exceptions are integrity failures.
- Input invariants: non-empty `scene_text` requires `scene_paragraphs`, and
  `scene_paragraphs` document IDs must be unique.
- Downstream conditional inputs:
  - StateUpdater always receives `paragraph_texts` and its operations and
    frame may cite `evidence_paragraph_ids` instead of `evidence_fact_ids`;
  - ActionPreview receives `scene_text` only when `SceneFactSet.facts` is
    empty;
  - the StateUpdater prompt treats confirmed paragraph prose as the primary
    source and Scene Facts as auxiliary reference; other consumers treat
    Scene Facts and fallback paragraph/scene text as equal authoritative
    sources when facts are empty.

### StateUpdater (Module B)

- StateUpdater always receives the full ID-tagged `paragraph_texts` of the
  confirmed turn as primary observation material; `scene_facts_json` stays as
  auxiliary compressed reference and never gates the paragraph input.
- The LLM contract is `StateResolutionOutputLoose` (`decision` + raw `frame` +
  `operations`). The frame is parsed into `FinalSceneFrame`
  (`final_location`, `day_offset >= 0`, `final_phase`,
  `active_present_npcs`, `departed_npcs`, evidence IDs); operations are parsed
  per item into `StateOperation`. Only attitude, visible-status, and
  resource/asset operation types are accepted from the model; location,
  participant, and clock operations emitted by the model are dropped with a
  `state_update_operation_compiled_by_frame` warning because they are compiled
  deterministically from the frame.
- Evidence tolerance is strip-first and never retried: unknown fact/paragraph
  IDs are removed with a warning, and a frame or operation left without valid
  evidence is ignored (`frame_ignored_no_evidence` /
  `state_update_operation_dropped`). Structural/schema problems (missing type,
  duplicate `operation_id`, unexpected top-level fields, malformed frame)
  still trigger the Gateway's single feedback correction (at most two model
  calls).
- After retries are exhausted, the updater salvages one attempt only:
  prefer the latest attempt with valid operations, otherwise the latest
  attempt with a valid frame, otherwise keep the previous `CurrentState`.
- The deterministic diff engine (`compile_frame_operations`) compiles the
  frame into auditable atomic operations inside the final `StateChangeSet`:
  relocation emits `location_change`; the relocation eviction guard removes
  every previous participant absent from `active_present_npcs`;
  `departed_npcs` remove explicitly departed participants; new entries of
  `active_present_npcs` emit `participant_add`. Names are normalized through
  the NPC alias map, the player is never added or removed, presence wins over
  a conflicting departure, and alias variants deduplicate to one canonical
  add.
- `calculate_phase_advance(current_phase, day_offset, target_phase)` resolves
  the clock step count by table lookup over the seven-phase ring instead of
  trusting model arithmetic; inputs that cannot form a non-negative advance
  leave the clock unchanged with a `frame_time_ignored` warning.
- No-op operations (absent participant removal, unchanged location, unchanged
  attitude, etc.) remain skipped with a warning and never retried. Provider
  failures and exhausted validation/parse failures degrade; user cancellation
  and integrity failures (including final `CurrentState` validation)
  propagate.

### Scene Summary (Module C)

- `SceneSummarizer` keeps the prompt schema strict (`content` max 300 for turn
  summaries, 400 for condensation) while the Gateway validation schema allows
  up to 150% (450/600) so over-limit content can be salvaged.
- `output_validator` uses `on_attempt_start` to save each attempt's usable
  candidate (non-empty after trimming and within 150%).
  - first attempt above the hard limit triggers the normal feedback retry;
  - second attempt above the hard limit but within 150% is accepted and kept as
    is;
  - second attempt above 150% is truncated to 150%.
- After retries are exhausted, candidates are chosen second-first, then first,
  then:
  - `summarize_turn` returns a deterministic placeholder;
  - `condense` returns `None`; maintenance call sites do not save or mark
    sources condensed, leaving them active for a later retry.
- `SceneSummarizer.last_warnings` reports `scene_summary_truncated`,
  `scene_summary_fallback`, and `scene_summary_condense_skipped`; confirmation
  and maintenance nodes emit these as diagnostics.
- Blank scene text skips the LLM and returns a deterministic no-content
  summary. Empty `source_summaries` for condensation remains an integrity
  failure.
- Only LLM parse/validation/provider failures degrade; cancellation and
  integrity failures propagate.

### NPC Update (Module D)

- `NPCBatchUpdate` is a loose wire envelope: extra top-level fields are allowed
  and the 256-entry cap is enforced inside `output_validator`, so a bad
  top-level shape does not discard valid entries.
- `NPCBatchUpdater.process` uses `output_validator` + `on_attempt_start` to
  call `parse_batch_updates` per attempt and save each attempt's valid entries
  and warnings.
- Correctable failures (invalid entries/fields, missing required
  `scene_summary`, top-level extras, over-limit) trigger one feedback retry;
  the validator also checks that every expected appeared NPC produced a
  `scene_summary`. After retries are exhausted, the second attempt's usable
  subset is preferred, then the first attempt's, then an empty batch with
  `npc_batch_fallback`; an entry is usable only when it belongs to an expected
  appeared NPC and has a scene summary or at least one stable change.
- Provider/parse/output-validation failures at the LLM boundary return an
  empty batch with `npc_batch_fallback`; deterministic alias acceptance,
  CurrentState→NPC sync, mention hits, and new NPC candidates still run.
- Soft truncation: `scene_summary.summary` hard 160 / soft 240,
  `change_basis` hard 80 / soft 120. First attempt enforces hard limits;
  second attempt keeps up to soft limits and truncates only above them.
- Persistent limits for `last_scene_summary` and `change_basis` were raised to
  the soft limits (240/120) to match accepted values.
- Existing per-entry/per-field salvage remains unchanged for non-length
  errors.

### Continuity (Module E)

- `ContinuityProjector` keeps the prompt schema strict (hard limits) while the
  Gateway uses a loose top-level array (`RootModel[list[Any]]`) and
  `output_validator` parses each operation.
- Per-operation schema/evidence errors, references to non-active `item_id`, and
  multiple operations for the same `item_id` are correctable on the first
  attempt; on the second attempt the conflicting operations are dropped while
  unrelated valid operations are kept.
- After retries are exhausted, the second attempt's valid operations are
  preferred, then the first attempt's, then an empty projection with
  `continuity_fallback`.
- Free-text fields use soft limits internally and persist with soft limits:
  `premise` 240/360, `state_delta` 240/360, `new_current_state` 800/1200,
  `reason` 160/240, `subject` 80/120. Hard display schema remains for the
  prompt. `evidence_basis_primary` was raised to accommodate
  `state_delta + reason`.
- `ContinuityProjector.last_warnings` reports `continuity_fallback`,
  `continuity_operation_dropped`, and `continuity_truncated`;
  `continuity_project_node` emits them as diagnostics. When both attempts have
  no usable operations, the last attempt's warnings are preserved and
  `continuity_fallback` is appended.
- Free-text fields are stripped before parsing/truncation so pure-whitespace
  values fail validation naturally.
- No confirmed scene paragraphs skips the LLM and returns an empty projection.

### Action Preview (Module F)

- Three-layer schema:
  - Gateway: `ActionPreviewResultLoose(previews: list[Any])`, extra allowed;
  - prompt: `ActionPreviewResultDisplay` with exactly 4 unique labels;
  - internal `ActionPreviewResult`: 0–4 unique previews.
- `ActionPreviewAgent` uses `output_validator` + `on_attempt_start`; schema
  errors, duplicate labels, or count != 4 trigger feedback retry on every
  attempt; the second attempt drops invalid/duplicate/over-cap previews and
  keeps valid unique previews up to 4.
- Text fields are trimmed before parsing; pure whitespace is correctable.
- Extra fields inside a preview are preserved before validation so Pydantic
  rejects them; they are not silently removed.
- After retries, the second attempt's valid previews are preferred, then the
  first attempt's, then an empty result with `action_preview_fallback`; last
  attempt warnings are preserved when nothing is usable.
- Top-level extra fields are reported but do not discard valid previews.
- `ActionPreviewAgent.last_warnings` reports `action_preview_fallback`,
  `action_preview_dropped`, `action_preview_count`, and
  `action_preview_top_level`; `action_preview_node` emits diagnostics only when
  computed.

### Director (Module G)

- `DirectorAgent` uses the same per-request salvage strategy for generation and
  confirmation: loose Gateway container, strict prompt schema, `output_validator`
  reuses `DirectorSkillService` for validation, and conflicts keep earlier
  requests in original order.
- First attempt with invalid/conflicting requests triggers feedback retry;
  second attempt drops invalid/conflicting requests and keeps valid unique
  requests. If nothing remains, returns an empty decision batch with
  `director_decision_fallback`.
- Missing/invalid or over-500-character `reasoning` uses a fixed fallback and
  does not discard valid requests.
- `DirectorConfirmationExecutor` returns immediately for empty decisions
  (no skill execution and no Director mutation batch).
- Decision LLM, Setting/NPC creation LLM, and pre-write preprocessing failures
  discard the mutation plan and return empty executions with
  `director_execution_fallback`. Mutation-plan validation, canonical DB write,
  outer transaction commit/rollback, and runtime recovery failures propagate.
- `director_event_review_node` degrades hidden and engaged reviews
  independently: hidden success is preserved; engaged failure skips only
  engaged changes and emits `director_event_review_fallback`.
- Hidden review validates all `consume_hook_ids` before any state write; the
  node catches only LLM errors, so workflow/domain `ValueError`s propagate as
  integrity failures instead of being misreported as safe degradation.
- Diagnostics are aggregated by `DirectorConfirmationExecutor` and emitted once
  by the node; `DirectorGenerationExecutor` also aggregates generation warnings
  and its node emits them into diagnostics.

### Generation: Context Facts (H1)

- `ContextFactsService` uses a loose `ContextFactDraftSetLoose` container with
  `turn_focus: str | None` and `facts: list[Any]`; the prompt still shows the
  strict `ContextFactDraftSet`.
- `output_validator` trims each `ContextFactDraft`, validates known evidence IDs,
  and caps facts at 12; unknown evidence IDs are correctable on the first
  attempt and dropped on the second while valid facts are preserved.
- After retries, the second attempt's valid facts are preferred, then the first
  attempt's, then an empty `ContextFactSet` with `context_extract_failed`.
- Blank `user_input` is a no-effect short-circuit before constructing the domain
  input; HTTP entry validation remains the primary boundary.
- Deterministic history/continuity/setting/NPC-bridge failures and NPC profile
  selection failures propagate as integrity failures; only LLM
  parse/validation/provider failures degrade.
- `ContextExtractor.last_warnings` reports `context_extract_failed`,
  `context_fact_dropped`, and `context_fact_count`; the node emits them once.

### Generation: Action Parser (H2)

- `ActionParser` uses `ActionExtractionResultLoose`: `analysis` stays narrow,
  `retrieval_topics` and `atomic_actions` are `list[Any]` for per-item salvage,
  and `touched_event_ids` stays `list[str]` with filtering only.
- `output_validator` rejects blank `action_id` / `raw_text` / `verb` / `actor`,
  validates retrieval topics (max 8 and length/blank rules), and accepts
  `touched_event_ids` only for hidden events visible to the parser.
- Unknown touched event IDs trigger feedback retry; on the second attempt only
  valid hidden event IDs are kept.
- After retries, second-attempt valid actions are preferred, then first-attempt,
  then an empty `ActionExtractionResult` with `action_extract_fallback`; the
  player input remains available for the Composer. Actions are the primary
  product: if the second attempt has no valid actions, the first attempt's
  actions are reused; second-attempt topics are not merged with first-attempt
  actions across attempts.
- Duplicate `action_id` and non-contiguous `sequence` are repaired by
  `ActionSelfCheck`, not treated as retry errors.
- Blank user input is no-effect and does not call the LLM.

### Generation: Roll Modifier (H3)

- `RollModifierEvaluator` uses `LLMRollModifierBatchLoose` with per-modifier
  salvage; the prompt still shows the strict batch schema.
- `output_validator` validates action IDs, trims `label`/`reason`, keeps at most
  two modifiers per action, and trims totals above 8 by dropping the
  lower-absolute-value modifier.
- After retries, second-attempt valid modifiers are preferred, then first-attempt,
  then an empty `RollModifierBatch` with `roll_modifier_fallback`; deterministic
  rolls still run with no modifiers.
- No roll actions short-circuit without calling the LLM.
- `RollModifierEvaluator.last_warnings` reports `roll_modifier_fallback` and
  `roll_modifier_dropped`; the node emits them.

### Generation: Action Judgments (H4)

- `build_action_judgments` deterministically maps every parsed action and roll
  to an immutable `outcome_band`; non-roll actions are automatic successes.
- The judgments are emitted to the player immediately after roll resolution and
  do not wait for narrative composition.
- Missing roll results remain integrity failures and propagate. No LLM fallback
  invents an action outcome.

### Generation: Canon Query / Consistency / Revision (H6)

- `NarrativeCanonQueryAgent` uses a loose draft set and per-query salvage;
  invalid queries are correctable once, then valid second-attempt queries are
  preferred, then first-attempt, then an empty query set with
  `canon_query_fallback`.
- Empty canon queries still proceed through evidence retrieval and a
  non-canon-only consistency check.
- Blank scene prose remains an integrity failure owned by the Composer (H5); the
  canon tail never wraps it into a successful empty-query result.
- `paragraph_ids` on canon queries are projection metadata only; the retriever
  does not depend on them, so no additional validation is introduced.
- `check_narrative_consistency_node` catches LLM errors and returns a `pass`
  review as the continue-generation control signal, but marks
  `validation_skipped=true` in the event/diagnostics so “not checked” is not
  confused with “passed”.
- Consistency is not salvaged assertion-by-assertion; an unavailable check skips
  revision and preserves the original composition.
- `revise_narrative_node` catches LLM errors, keeps the original composition,
  and emits `narrative_revision_fallback` with the revision instruction for
  visibility.

### Generation: Composer (H5)

- The prompt exposes an 80-character hard target for each `resolved_fact`; the
  Gateway accepts up to 120 and the domain validator soft-truncates only above
  180. Invalid, missing, duplicate, or outcome-changing action resolutions are
  rejected as a whole rather than silently fabricating replacements.
- The Composer must return nonblank scene prose, one resolution per Action
  Judgment, and planned-event coverage for every action. Parse or validation
  failures propagate after the Gateway retry, so no invalid pending turn is
  created.
- Normal generation, Reroll, and Rephrase all invoke this same full Composer.
  Rephrase preserves authoritative inputs and deterministic outcome bands, not
  a previous LLM-generated plan.
- Opening uses `OpeningSceneAgent`, not `NarrativeComposerAgent`; H5 does not
  cover it.

### Generation: Rewrite Strategy Selector (H7)

- `RewriteReasonCode` adds `strategy_unavailable`; the frontend shows
  “暂时无法判断修改方式，原文已保留，可重试”.
- If strategy selection fails after the Gateway feedback retry, the stream
  returns a conservative `no_rewrite` decision with `strategy_unavailable` and
  emits `rewrite_strategy_fallback`; it does not enter the rewrite graph.
- Empty or over-long input remains an integrity-style `ValueError` at the stream
  entry; `source_scene_text` length is bounded by the Pending turn's `max_chars`,
  and `user_feedback` by 2000 characters. The fallback is only for LLM failures.
