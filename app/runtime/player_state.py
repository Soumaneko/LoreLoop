from __future__ import annotations

from typing import Any

from .serialization import model_data
from .session import CampaignRuntime, get_campaign_runtime
from .settings import load_settings


PLAYER_HISTORY_PAGE_SIZE = 5


def _project_history(turns) -> list[dict[str, Any]]:
    return [
        {
            "turn_index": turn.turn_index,
            "user_input": turn.user_input,
            "scene_text": turn.scene_text,
            "closing_prompt": turn.closing_prompt,
        }
        for turn in turns
    ]


def _project_text_highlights(
    runtime: CampaignRuntime,
    *,
    texts: list[str],
    player_name: str,
) -> list[dict[str, str]]:
    folded_text = "\n".join(texts).casefold()
    if not folded_text:
        return []

    candidates: list[tuple[str, str]] = []
    clean_player_name = player_name.strip()
    if clean_player_name:
        candidates.append((clean_player_name, "player"))
    with runtime.stores.npc_profile() as store:
        for profile in store.list_all():
            candidates.extend(
                (surface, "npc")
                for surface in [profile.name, *profile.aliases]
            )
    with runtime.stores.entity() as store:
        for profile in store.list_all():
            candidates.extend(
                (surface, "entity")
                for surface in [profile.name, *profile.aliases]
            )

    projected: list[dict[str, str]] = []
    seen: set[str] = set()
    for raw_surface, kind in candidates:
        surface = str(raw_surface).strip()
        key = surface.casefold()
        if not surface or key in seen or key not in folded_text:
            continue
        seen.add(key)
        projected.append({
            "surface": surface,
            "kind": kind,
        })
    projected.sort(key=lambda item: len(item["surface"]), reverse=True)
    return projected


def _history_page(runtime: CampaignRuntime, *, before_turn_index=None, limit=5):
    with runtime.stores.short_term() as short_store:
        history, has_more = short_store.list_page(
            before_turn_index=before_turn_index,
            limit=limit,
        )
    projected_history = _project_history(history)
    with runtime.stores.current_state() as state_store:
        player_name = state_store.load_or_default().player.name
    return {
        "history": projected_history,
        "highlights": _project_text_highlights(
            runtime,
            texts=[turn["scene_text"] for turn in projected_history],
            player_name=player_name,
        ),
        "pagination": {
            "has_more": has_more,
            "oldest_turn_index": history[0].turn_index if history else None,
            "page_size": limit,
        },
    }


def load_player_state() -> dict[str, Any]:
    """Return the allow-listed campaign projection used by the player UI."""
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _load_player_state(runtime)


def load_player_history(
    *,
    before_turn_index: int | None = None,
    limit: int = PLAYER_HISTORY_PAGE_SIZE,
) -> dict[str, Any]:
    """Return an allow-listed page of confirmed prose for upward loading."""
    if limit < 1 or limit > 20:
        raise ValueError("history limit must be between 1 and 20")
    runtime = get_campaign_runtime()
    with runtime.confirmation_lock:
        return _history_page(
            runtime,
            before_turn_index=before_turn_index,
            limit=limit,
        )


def _load_player_state(runtime: CampaignRuntime) -> dict[str, Any]:
    stores = runtime.stores
    core_memory = stores.core_memory().load()
    with stores.current_state() as state_store:
        current_state = state_store.load_or_default()
    history_page = _history_page(runtime, limit=PLAYER_HISTORY_PAGE_SIZE)
    history = history_page["history"]

    settings = load_settings()
    pending_groups = []
    group_ids = list(dict.fromkeys(
        pending.group_id for pending in runtime.pending_turns.values()
    ))
    for group_id in group_ids:
        variants = runtime.pending_turns.variants(group_id)
        if not variants:
            continue
        pending_groups.append({
            "kind": "turn",
            "group_id": group_id,
            "user_input": variants[0].user_input,
            "variants": [
                {
                    "turn_id": variant.turn_id,
                    "version_index": variant.version_index,
                    "turn_index": variant.turn_index,
                    "scene_text": variant.scene_result.scene_text,
                    "closing_prompt": variant.scene_result.closing_prompt,
                }
                for variant in variants
            ],
        })

    pending_openings = [
        {
            "kind": "opening",
            "group_id": None,
            "user_input": "",
            "variants": [{
                "turn_id": opening.turn_id,
                "version_index": 1,
                "turn_index": 0,
                "scene_text": opening.scene_result.scene_text,
                "closing_prompt": opening.scene_result.closing_prompt,
            }],
        }
        for opening in runtime.pending_openings.values()
    ]
    pending = [*pending_groups, *pending_openings]
    highlight_texts = [turn["scene_text"] for turn in history]
    highlight_texts.extend(
        variant["scene_text"]
        for group in pending
        for variant in group["variants"]
    )

    scene = current_state.scene
    player = current_state.player
    resources = current_state.resources
    return {
        "save": {
            "name": runtime.campaign_id,
            "initialized": core_memory is not None,
        },
        "needs_init": core_memory is None,
        "difficulty": settings.get("difficulty", "困难"),
        "campaign": {
            "turn_index": history[-1]["turn_index"] if history else 0,
            "world_time": current_state.world_clock.label,
            "location": scene.location,
            "scene_conditions": [
                condition.condition for condition in scene.scene_condition
            ],
            "participants": scene.participant_names,
            "player": {
                "name": player.name,
                "conditions": list(player.condition),
                "visible_status": player.visible_status,
            },
            "resources": model_data(resources),
        },
        "history": history,
        "history_pagination": history_page["pagination"],
        "highlights": _project_text_highlights(
            runtime,
            texts=highlight_texts,
            player_name=player.name,
        ),
        "pending": pending,
        "history_controls": {
            key: value
            for key, value in runtime.history.status().items()
            if key in {
                "can_undo",
                "can_redo",
                "undo_count",
                "redo_count",
                "redo_pending_turn_ids",
            }
        },
    }


def player_saves_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove server paths from SaveManager responses."""
    return {
        "active": payload.get("active"),
        "saves": [
            {
                key: value
                for key, value in save.items()
                if key in {"name", "active", "initialized", "modified_at"}
            }
            for save in payload.get("saves", [])
        ],
    }
