from __future__ import annotations

from typing import Any

from app.core.text import eligible_highlight_term
from .current_state_access import load_effective_current_state
from .serialization import model_data
from .session import CampaignRuntime, get_campaign_runtime
from .settings import (
    canon_validation_enabled,
    experience_mode,
    load_settings,
    normalize_dice_display,
)


PLAYER_HISTORY_PAGE_SIZE = 5
HISTORY_NAVIGATION_EXCERPT_LENGTH = 32


def _project_history(turns) -> list[dict[str, Any]]:
    return [
        {
            "turn_index": turn.turn_index,
            "user_input": turn.user_input,
            "scene_text": turn.scene_text,
            "closing_prompt": turn.closing_prompt,
            "action_preview": model_data(turn.action_preview),
            "player_resolution": model_data(turn.player_resolution),
            "token_usage": turn.token_usage,
        }
        for turn in turns
    ]


def _user_input_excerpt(user_input: str) -> str:
    text = " ".join(user_input.split())
    if len(text) <= HISTORY_NAVIGATION_EXCERPT_LENGTH:
        return text
    return f"{text[:HISTORY_NAVIGATION_EXCERPT_LENGTH - 1]}…"


def _history_navigation(runtime: CampaignRuntime) -> list[dict[str, Any]]:
    with runtime.uow() as uow:
        with uow.short_term as short_store:
            entries = short_store.list_navigation_entries()
    return [
        {
            "turn_index": turn_index,
            "user_input_excerpt": _user_input_excerpt(user_input),
        }
        for turn_index, user_input in entries
    ]


def _project_text_highlights(
    runtime: CampaignRuntime,
    *,
    texts: list[str],
    player_name: str,
) -> list[dict[str, Any]]:
    folded_text = "\n".join(texts).casefold()
    if not folded_text:
        return []

    candidates: list[tuple[str, str, dict[str, Any] | None]] = []
    with runtime.uow() as uow:
        with uow.npc_profile as store:
            for profile in store.list_all():
                public_profile = {
                    "kind": "npc",
                    "name": profile.name,
                    "aliases": list(profile.aliases),
                    "role": profile.role,
                    "appearance": profile.appearance,
                    "personality_trait": profile.personality_trait,
                    "known_abilities": [
                        ability.model_dump(mode="json")
                        for ability in profile.known_abilities
                    ],
                    "faction": profile.faction,
                    "last_known_location": profile.last_known_location,
                    "attitude_to_player": profile.attitude_to_player,
                    "last_scene_summary": profile.last_scene_summary,
                    "last_scene_turn": profile.last_scene_turn,
                }
                candidates.extend(
                    (surface, "npc", public_profile)
                    for surface in [profile.name, *profile.aliases]
                )
    with runtime.uow() as uow:
        with uow.world_setting as store:
            for setting in store.list_all():
                if not eligible_highlight_term(setting.name):
                    continue
                public_profile = {
                    "kind": "setting",
                    "setting_kind": setting.kind,
                    "setting_id": setting.setting_id,
                    "name": setting.name,
                    "summary": setting.summary,
                    "public_facts": list(setting.public_facts),
                }
                candidates.append((setting.name, "setting", public_profile))
    projected: list[dict[str, Any]] = []
    seen: set[str] = set()
    player_key = player_name.strip().casefold()
    # Surfaces are deduplicated by text with NPC candidates added first, so a
    # Setting sharing a name with an NPC deterministically loses: NPC wins.
    for raw_surface, kind, profile in candidates:
        surface = str(raw_surface).strip()
        key = surface.casefold()
        if (
            not surface
            or key == player_key
            or key in seen
            or key not in folded_text
        ):
            continue
        seen.add(key)
        item = {
            "surface": surface,
            "kind": kind,
        }
        if profile is not None:
            item["profile"] = profile
        projected.append(item)
    projected.sort(key=lambda item: len(item["surface"]), reverse=True)
    return projected


def _history_page(runtime: CampaignRuntime, *, before_turn_index=None, limit=5):
    with runtime.uow() as uow:
        with uow.short_term as short_store:
            history, has_more = short_store.list_page(
                before_turn_index=before_turn_index,
                limit=limit,
            )
    projected_history = _project_history(history)
    with runtime.uow() as uow:
        with uow.current_state as state_store:
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


def _pending_scene_projection(pending) -> dict[str, str | None]:
    original = getattr(pending, "original_scene_text", None)
    current = pending.scene_result.scene_text
    return {
        "scene_text": original if original is not None else current,
        "saved_scene_text": current if original is not None else None,
    }


def _load_player_state(runtime: CampaignRuntime) -> dict[str, Any]:
    stores = runtime.stores
    core_memory = stores.core_memory.load()
    current_state = load_effective_current_state(runtime)
    initialization = runtime.initialization.load()
    if initialization is None and core_memory is not None:
        raise RuntimeError("CoreMemory exists without an initialization record")
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
                    **_pending_scene_projection(variant),
                    "closing_prompt": variant.scene_result.closing_prompt,
                    "player_resolution": model_data(
                        getattr(variant, "player_resolution", None)
                    ),
                    "token_usage": getattr(variant, "token_usage", None),
                }
                for variant in variants
            ],
        })

    opening_group_ids = list(dict.fromkeys(
        opening.group_id for opening in runtime.pending_openings.values()
    ))
    pending_openings = []
    for group_id in opening_group_ids:
        variants = runtime.pending_openings.variants(group_id)
        if not variants:
            continue
        pending_openings.append({
            "kind": "opening",
            "group_id": group_id,
            "user_input": "",
            "variants": [{
                "turn_id": opening.turn_id,
                "version_index": opening.version_index,
                "turn_index": 0,
                **_pending_scene_projection(opening),
                "closing_prompt": opening.scene_result.closing_prompt,
                "token_usage": getattr(opening, "token_usage", None),
            } for opening in variants],
        })
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
    initialization_phase = (
        initialization.phase if initialization is not None else "config"
    )
    initialization_completed = initialization_phase == "completed"
    needs_init = not initialization_completed and not pending_openings
    return {
        "save": {
            "save_id": runtime.campaign_id,
            "initialized": initialization_completed,
        },
        "needs_init": needs_init,
        "initialization": {
            "phase": initialization_phase,
            "config": (
                model_data(initialization.config)
                if initialization is not None
                else None
            ),
            "campaign_setup": (
                model_data(initialization.campaign_setup)
                if initialization is not None
                else None
            ),
        },
        "difficulty": settings.get("difficulty", "困难"),
        "dice_display": normalize_dice_display(settings.get("dice_display")),
        "canon_validation_enabled": canon_validation_enabled(settings),
        "experience_mode": experience_mode(settings),
        "campaign": {
            "turn_index": history[-1]["turn_index"] if history else 0,
            "world_time": current_state.world_clock.label,
            "location": scene.location,
            "participants": scene.participant_names,
            "player": {
                "name": player.name,
                "visible_status": player.visible_status,
            },
            "resources": model_data(resources),
        },
        "history": history,
        "history_navigation": _history_navigation(runtime),
        "action_preview": (
            history[-1]["action_preview"] if history else None
        ),
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
                if key in {
                    "save_id",
                    "name",
                    "active",
                    "initialized",
                    "last_played_at",
                    "turn_count",
                    "protagonist_name",
                    "location",
                }
            }
            for save in payload.get("saves", [])
        ],
    }
