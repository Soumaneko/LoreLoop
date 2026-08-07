from __future__ import annotations


def eligible_highlight_term(name: str) -> bool:
    """Setting names must be at least two characters to match body text.

    Single-character names (e.g. "道", "阁") collide with ordinary sentences
    ("知道", "大道", "说道") under substring matching, which would falsely
    count an appearance and expose the setting's public facts.
    """
    return len(str(name).strip()) >= 2


def normalize_key(value: str | None) -> str:
    """Casefold a key while preserving internal whitespace."""
    return str(value or "").strip().casefold()


def normalize_compact_key(value: str | None) -> str:
    """Casefold a key and collapse internal whitespace entirely.

    Use for name matching where spacing should not separate the same name.
    """
    return "".join(str(value or "").casefold().split())
