"""Creator-mode Core Memory item editor.

The Core Memory is the committed world foundation built during initialization.
Creator mode may curate its items directly (add / edit / delete); player mode is
rejected at the API boundary. Newly added items are always marked
``source="player"`` and existing items keep their original source so the
campaign-setup provenance is never rewritten.
"""

from __future__ import annotations

from typing import Any

from app.core.client_error import ClientValidationError
from app.core.ids import new_hex_id
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.runtime.session import get_campaign_runtime
from app.runtime.settings import experience_mode


def load_core_memory_items() -> dict[str, Any]:
    """Return the current Core Memory items for the creator editor."""
    if experience_mode() != "creator":
        raise ClientValidationError("Core memory editing is only available in creator mode")
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        with runtime.uow() as uow:
            core_memory = uow.core_memory.load()
    return {
        "initialized": core_memory is not None,
        "items": (
            [item.model_dump(mode="json") for item in core_memory.items]
            if core_memory is not None
            else []
        ),
    }


def save_core_memory_items(payload: dict[str, Any]) -> dict[str, Any]:
    """Replace the Core Memory items with a validated, curated item list."""
    if experience_mode() != "creator":
        raise ClientValidationError("Core memory editing is only available in creator mode")
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        raise ClientValidationError("items must be a list")
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        with runtime.uow() as uow:
            core_memory = uow.core_memory.load()
            if core_memory is None:
                raise ClientValidationError("core memory is not initialized yet")
            existing = {item.id: item for item in core_memory.items}
            seen: set[str] = set()
            items: list[CoreMemoryItem] = []
            for raw in raw_items:
                if not isinstance(raw, dict):
                    raise ClientValidationError("each core memory item must be an object")
                item_id = str(raw.get("id") or "").strip()
                existing_item = existing.get(item_id)
                source = existing_item.source if existing_item is not None else "player"
                try:
                    item = CoreMemoryItem.model_validate(
                        {
                            "id": item_id or new_hex_id(prefix="core_memory", length=12),
                            "kind": raw.get("kind"),
                            "strength": raw.get("strength"),
                            "source": source,
                            "content": raw.get("content"),
                        }
                    )
                except Exception as exc:
                    raise ClientValidationError(f"invalid core memory item: {exc}") from exc
                if item.id in seen:
                    raise ClientValidationError(f"duplicate core memory item id: {item.id}")
                seen.add(item.id)
                items.append(item)
            uow.core_memory.save(
                CoreMemory(version=core_memory.version, items=items)
            )
        runtime.invalidate_generation_cache()
    return {
        "initialized": True,
        "items": [item.model_dump(mode="json") for item in items],
    }
