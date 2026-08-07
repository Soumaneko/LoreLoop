"""Shared helpers for folding LangGraph node updates into streaming state."""

from __future__ import annotations

from typing import Any


def fold_state_keys(
    state: Any,
    node_update: Any,
    *,
    exclude: frozenset[str] = frozenset(("events",)),
) -> None:
    """Overwrite ordinary node output keys onto the running state."""
    state.update({
        key: value
        for key, value in node_update.items()
        if key not in exclude
    })


def fold_node_update(
    state: Any,
    node_update: Any,
    *,
    events_key: str = "events",
    exclude: frozenset[str] = frozenset(),
) -> list[Any]:
    """Fold node output and accumulate its events into the running state."""
    fold_state_keys(
        state,
        node_update,
        exclude=frozenset({events_key, *exclude}),
    )
    events = node_update.get(events_key, [])
    state[events_key] = [*state.get(events_key, []), *events]
    return events
