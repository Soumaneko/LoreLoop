"""Reproduce a real context-history retrieval run and dump the competition.

Purpose
-------
This script replays the retrieval half of a recorded generation trace for the
query "回想姚沉璧第一次与我接头时，究竟警告过我什么。" (trace turn 12) against a
save directory, and prints the full selection competition: search plan, the
direct/relation proposal funnel, the selector's accepted entries and
augmentations, the final evidence order, and the issue-17 proposal diagnostics.

It is a read-only observability tool: it never writes to the save directory.
Because ``ConfirmedHistoryStore``/``ContinuityStore``/``NPCProfileStore`` run
schema validation that needs write access, the required ``*.db`` files are
copied into a fresh temp directory first, so a read-only production save can be
inspected safely.

Use cases
---------
- Verify the selector still reproduces the recorded trace output byte-for-byte
  (e.g. after refactors like issue 18, which deleted the augmentation owner
  ledger: the expected result is unchanged).
- Inspect *why* a document entered (route / intent / quality / provenance).
- Measure how the NPC field-history bridge changes retrieval, via
  ``--without-npc-field`` (the comparison used to show that
  campaign.db's field-change logs are what brings the warning paragraph
  turn_3_p006 into the results).

Usage
-----
    PYTHONPATH=. uv run python scripts/repro_trace_retrieval.py \
        --save ~/.local/share/LoreLoop/saves/<save-id> \
        [--without-npc-field] [--show-proposals]

Defaults:
    --save  ~/.local/share/LoreLoop/saves/207599e8-31e3-446b-9e13-cd219fec07d8
            (the save that produced the recorded trace)
    --current-turn 12 (the trace's turn_index)

No production code is modified.
"""
from __future__ import annotations
from app.runtime.stores import open_current_state_store
from app.runtime.stores import open_continuity_store
from app.runtime.stores import open_confirmed_history_store
from app.runtime.stores import open_npc_profile_store

import argparse
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from app.domain.action.schemas import ActionRetrievalTopic, Anchor
from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.context_facts.continuity_bridge import ContinuityHistoryBridge
from app.domain.context_facts.intent_builder import build_context_search_plan
import app.domain.context_facts.retriever as retriever_mod
from app.domain.context_facts.retriever import ContextHistoryRetriever
from app.domain.continuity import ContinuityStore
from app.domain.current_state.store import CurrentStateStore
from app.domain.npc_profile.history_bridge import NPCProfileHistoryBridge
from app.domain.npc_profile.schemas import NPCProfileDirectorView
from app.domain.npc_profile.store import NPCProfileStore

# ---------------------------------------------------------------------------
# Trace-derived inputs (from the recorded action_extract / context_extract
# traces at turn 12, save 207599e8-31e3-446b-9e13-cd219fec07d8).
# ---------------------------------------------------------------------------
USER_INPUT = "回想姚沉璧第一次与我接头时，究竟警告过我什么。"
RETRIEVAL_TOPICS = ["姚沉璧", "接头", "警告"]
# Realistic matched_anchors produced by select_context_profiles: the two
# characters mentioned in the user input / present in the scene.
ANCHOR_NAMES = ["姚沉璧", "苏念薇"]
DB_FILES = ("campaign.db",)


def build_inputs(save: Path, current_turn: int):
    """Copy the save's db file to a temp dir and open the stores read-safely.

    Returns the search plan, bridge results and evidence set, plus the
    selector's captured proposal list and SelectionResult.
    """
    workdir = Path(tempfile.mkdtemp(prefix="repro-trace-retrieval-"))
    for name in DB_FILES:
        shutil.copy2(save / name, workdir / name)

    history_store = open_confirmed_history_store(str(workdir / "campaign.db"))
    continuity_store = open_continuity_store(str(workdir / "campaign.db"))
    npc_store = open_npc_profile_store(str(workdir / "campaign.db"))
    current_state = open_current_state_store(
        str(workdir / "campaign.db")
    ).load_or_default()

    profiles = npc_store.list_all(active_only=False)
    director_views = [
        NPCProfileDirectorView(
            name=p.name,
            aliases=list(p.aliases),
            role=p.role,
            appearance=p.appearance,
            personality_trait=p.personality_trait,
            known_abilities=[a for a in p.known_abilities],
            faction=p.faction,
            last_known_location=p.last_known_location,
            attitude_to_player=p.attitude_to_player,
        )
        for p in profiles
    ]

    search_plan = build_context_search_plan(
        user_input=USER_INPUT,
        current_state=current_state,
        matched_anchors=[
            Anchor(
                text=name,
                normalized=name,
                kind="character",
                source="explicit",
                confidence=0.9,
            )
            for name in ANCHOR_NAMES
        ],
        npc_profiles=director_views,
        retrieval_topics=[
            ActionRetrievalTopic(text=t) for t in RETRIEVAL_TOPICS
        ],
    )

    continuity = ContinuityHistoryBridge(continuity_store).retrieve(search_plan)
    npc_links = NPCProfileHistoryBridge(npc_store).retrieve(search_plan)
    return (
        search_plan,
        continuity,
        npc_links,
        history_store,
        current_turn,
    )


def run_retrieval(
    history_store,
    search_plan,
    continuity,
    npc_links,
    current_turn: int,
    *,
    use_npc_field: bool,
):
    captured: dict = {}
    _real_select = retriever_mod.select_diverse_candidates

    def spy(plan, *, proposals, policy, preview, commit):
        captured["proposals"] = proposals
        result = _real_select(
            plan, proposals=proposals, policy=policy,
            preview=preview, commit=commit,
        )
        captured["result"] = result
        return result

    links = npc_links.links if use_npc_field else []
    with mock.patch.object(
        retriever_mod, "select_diverse_candidates", side_effect=spy
    ):
        evidence = ContextHistoryRetriever(history_store).retrieve(
            search_plan,
            current_turn_index=current_turn,
            continuity_links=continuity.links,
            npc_field_links=links,
            matched_active_continuities=continuity.matched_active_continuities,
            matched_archived_continuities=continuity.matched_archived_continuities,
            matched_npc_profiles=(
                len(npc_links.matched_profile_ids) if use_npc_field else 0
            ),
        )
    return evidence, captured


def dump(
    label: str,
    evidence,
    captured: dict,
    *,
    show_proposals: bool,
) -> None:
    r = captured["result"]
    print(f"\n=== [{label}] SELECTOR RESULT ===")
    print("selected:", [c.document.document_id for c in r.selected])
    for e in r.entries:
        print(
            f"  entry route={e.proposal.route} intent={e.proposal.owner_intent_id} "
            f"doc={e.candidate.document.document_id} "
            f"src={e.proposal.source_evidence_id} "
            f"q={getattr(e.proposal, 'scoped_quality', None)}"
        )
    for a in r.augmentations:
        print(
            f"  aug   intent={a.proposal.owner_intent_id} "
            f"doc={a.candidate.document.document_id} src={a.source_evidence_id} "
            f"q={getattr(a.proposal, 'scoped_quality', None)}"
        )
    if show_proposals:
        print(f"\n=== [{label}] PROPOSALS (competition input) ===")
        for p in captured["proposals"]:
            c = p.candidate
            print(
                f"  [{p.route:8s}] doc={c.document.document_id:<12s} "
                f"intent={p.owner_intent_id} turn={c.document.turn_index} "
                f"q={getattr(p, 'scoped_quality', None)} "
                f"cls={getattr(p, 'scoped_class', None)} "
                f"owners={sorted(getattr(p, 'scoped_owner_keys', set()))} "
                f"src={p.source_evidence_id}"
            )
    print(f"\n=== [{label}] EVIDENCE HITS (final order) ===")
    for h in evidence.hits:
        print(
            f"  {h.evidence_id:<12s} turn={h.turn_index} "
            f"direct={h.matched_intent_ids} relation={h.relation_intent_ids} "
            f"q={h.relation_evidence_quality} cls={h.relation_evidence_class}"
        )
    print(f"\n=== [{label}] DIAGNOSTICS ===")
    for f in (
        "indexed_documents", "indexed_turns",
        "matched_active_continuities", "matched_archived_continuities",
        "matched_npc_profiles", "valid_relation_links",
        "relation_candidates_considered", "relation_hits_added",
        "stale_relation_links", "invalid_relation_links", "degraded_relation_links",
        "npc_valid_links", "npc_stale_links", "npc_invalid_links",
        "npc_degraded_links", "npc_relation_candidates", "npc_relation_hits_added",
        "direct_proposals_considered", "relation_proposals_considered",
        "relation_zero_stripped", "relation_eligible_strong",
        "relation_eligible_degraded", "documents_selected_by_direct",
        "documents_selected_by_relation",
        "relation_augmented_on_selected_document",
    ):
        print(f"  {f} = {getattr(evidence, f, None)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--save",
        type=Path,
        default=Path.home()
        / ".local/share/LoreLoop/saves"
        / "207599e8-31e3-446b-9e13-cd219fec07d8",
        help="save directory to inspect (default: the trace-producing save)",
    )
    parser.add_argument(
        "--current-turn", type=int, default=12,
        help="current_turn_index passed to the retriever (default: 12)",
    )
    parser.add_argument(
        "--without-npc-field",
        action="store_true",
        help="also run once with NPC field-history links disabled for comparison",
    )
    parser.add_argument(
        "--show-proposals",
        action="store_true",
        help="print the full proposal funnel (can be verbose)",
    )
    args = parser.parse_args()

    if not (args.save / "campaign.db").exists():
        print(f"save not found: {args.save}")
        return 2

    search_plan, continuity, npc_links, history_store, current_turn = (
        build_inputs(args.save, args.current_turn)
    )
    print("=== SEARCH PLAN ===")
    for intent in search_plan.intents:
        print(
            f"  {intent.intent_id} focus={intent.focus} origin={intent.origin} "
            f"entities={[e.name for e in intent.entities]} topics={intent.topics}"
        )
    print(
        f"continuity links: {len(continuity.links)} "
        f"active={continuity.matched_active_continuities} "
        f"archived={continuity.matched_archived_continuities}"
    )
    print(f"npc field links: {len(npc_links.links)}")

    evidence, captured = run_retrieval(
        history_store, search_plan, continuity, npc_links, current_turn,
        use_npc_field=True,
    )
    dump("with npc field links", evidence, captured, show_proposals=args.show_proposals)

    if args.without_npc_field:
        evidence_wo, captured_wo = run_retrieval(
            history_store, search_plan, continuity, npc_links, current_turn,
            use_npc_field=False,
        )
        dump(
            "without npc field links", evidence_wo, captured_wo,
            show_proposals=args.show_proposals,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
