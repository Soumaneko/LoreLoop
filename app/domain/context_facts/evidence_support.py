from __future__ import annotations

import re
from dataclasses import dataclass

from app.domain.confirmed_history import (
    HistoryDocument,
    best_trigram_match,
    document_window,
)

ZERO_SUPPORT = 0.02
WEAK_SUPPORT = 0.30
CONTEXT_BACKOFF = 0.40
CLASS_RANK = {"strong": 3, "weak": 2, "zero": 1, "incomplete": 0}


@dataclass(frozen=True)
class LinkSupport:
    """Per-link node -> evidence support verdict."""

    quality: float
    support_class: str
    primary_support: float
    context_support: float
    basis_complete: bool

    @property
    def degraded(self) -> bool:
        return self.support_class != "strong"


def support_score(basis_text: str | None, window_text: str) -> float:
    """Lexical support of a node basis against a window of evidence text.

    Combines trigram overlap with a distinctive-bigram containment ratio so
    short Chinese 2-char terms (约定, 铜牌, 追查) count even when no shared
    trigram survives. Returns 0.0 for empty basis/window.
    """
    if not basis_text or not window_text:
        return 0.0
    trigram_score, _ = best_trigram_match([basis_text], window_text)
    return max(trigram_score, _substring_support(basis_text, window_text))


def _substring_support(basis_text: str, window_text: str) -> float:
    basis = _compact(basis_text)
    window = _compact(window_text)
    grams = {
        basis[index:index + 2]
        for index in range(len(basis) - 1)
    }
    if not grams:
        return 0.0
    matched = sum(1 for gram in grams if gram in window)
    return matched / len(grams)


def _compact(text: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff]", "", re.sub(r"\s+", "", text.casefold()))


def link_window_text(
    document: HistoryDocument,
    turn_documents: list[HistoryDocument],
    node_evidence_ids: set[str],
) -> str:
    """Main evidence plus same-turn adjacency, excluding the node's own
    sibling evidence so an evidence cannot borrow the load of its node peers.

    Reuses the existing History adjacency definition (document_window); only
    sibling exclusion is applied on top, keeping one adjacency rule.
    """
    window = document_window(
        document,
        turn_documents,
        before=1,
        after=1,
    )
    return "\n\n".join(
        item.text
        for item in window
        if (
            item.document_id == document.document_id
            or item.document_id not in node_evidence_ids
        )
    )


def classify_link_support(
    *,
    basis_primary: str | None,
    basis_context: str | None,
    basis_complete: bool,
    window_text: str,
) -> LinkSupport:
    """Score one lifecycle link against its node basis.

    ``primary`` support (the operation-specific content: premise / state
    delta / closing reason) dominates; the contextual basis (subject/premise
    background) only backs off a capped fraction so the item-level semantics
    never broadcast onto evidence that does not support this node's change.
    """
    primary_support = support_score(basis_primary, window_text)
    context_support = support_score(basis_context, window_text)
    combined = max(primary_support, CONTEXT_BACKOFF * context_support)
    # Context may only lift an evidence to weak; reaching strong requires the
    # primary basis itself to clear the weak threshold, so item-level
    # background can never broadcast a node that the evidence does not change.
    quality = (
        primary_support
        if primary_support > WEAK_SUPPORT
        else min(combined, WEAK_SUPPORT)
    )

    if not basis_complete or not basis_primary:
        support_class = "incomplete"
    elif quality <= ZERO_SUPPORT:
        support_class = "zero"
    elif quality <= WEAK_SUPPORT:
        support_class = "weak"
    else:
        support_class = "strong"

    return LinkSupport(
        quality=round(quality, 6),
        support_class=support_class,
        primary_support=round(primary_support, 6),
        context_support=round(context_support, 6),
        basis_complete=basis_complete,
    )
