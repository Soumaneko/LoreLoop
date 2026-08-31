import json
import os
import threading
from typing import Any


DICE_DISPLAY_MODES = {"hidden", "outcome", "full"}
DEFAULT_DICE_DISPLAY = "outcome"
DEFAULT_CANON_VALIDATION_ENABLED = False
EXPERIENCE_MODES = {"player", "creator"}
DEFAULT_EXPERIENCE_MODE = "player"
CAMPAIGN_SETTING_KEYS = {
    "difficulty",
    "dice_display",
    "max_chars",
    "canon_validation_enabled",
    "experience_mode",
}
_SETTINGS_LOCK = threading.RLock()


def normalize_dice_display(value: Any) -> str:
    mode = str(value or "").strip()
    return mode if mode in DICE_DISPLAY_MODES else DEFAULT_DICE_DISPLAY


def normalize_boolean(value: Any, *, default: bool) -> bool:
    return value if isinstance(value, bool) else default


def canon_validation_enabled(settings: dict[str, Any] | None = None) -> bool:
    payload = load_settings() if settings is None else settings
    return normalize_boolean(
        payload.get("canon_validation_enabled"),
        default=DEFAULT_CANON_VALIDATION_ENABLED,
    )


def normalize_experience_mode(value: Any) -> str:
    mode = str(value or "").strip()
    return mode if mode in EXPERIENCE_MODES else DEFAULT_EXPERIENCE_MODE


def experience_mode(settings: dict[str, Any] | None = None) -> str:
    payload = load_settings() if settings is None else settings
    return normalize_experience_mode(payload.get("experience_mode"))


def _settings_path():
    from .session import get_campaign_runtime

    return get_campaign_runtime().paths.settings_json


def load_settings() -> dict[str, Any]:
    with _SETTINGS_LOCK:
        try:
            payload = json.loads(_settings_path().read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            return {
                key: value
                for key, value in payload.items()
                if key in CAMPAIGN_SETTING_KEYS
            }
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return {}


def _write_settings(settings: dict[str, Any]) -> None:
    with _SETTINGS_LOCK:
        settings_path = _settings_path()
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = settings_path.with_name(f".{settings_path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    {
                        key: value
                        for key, value in settings.items()
                        if key in CAMPAIGN_SETTING_KEYS
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, settings_path)
        finally:
            temporary.unlink(missing_ok=True)


def _update_settings(updates: dict[str, Any]) -> dict[str, Any]:
    from .session import get_campaign_runtime

    runtime = get_campaign_runtime()

    def merge_and_write() -> dict[str, Any]:
        with _SETTINGS_LOCK:
            settings = load_settings()
            settings.update(updates)
            _write_settings(settings)
            runtime.invalidate_generation_cache()
            return settings

    with runtime.operation_submission():
        return merge_and_write()


def save_difficulty(payload: dict[str, Any]) -> dict[str, str]:
    difficulty = str(payload.get("difficulty", "困难")).strip()
    if difficulty not in ("简单", "普通", "困难"):
        difficulty = "困难"
    _update_settings({"difficulty": difficulty})
    return {"difficulty": difficulty}


def save_dice_display(payload: dict[str, Any]) -> dict[str, str]:
    dice_display = normalize_dice_display(payload.get("dice_display"))
    _update_settings({"dice_display": dice_display})
    return {"dice_display": dice_display}


def save_canon_validation(payload: dict[str, Any]) -> dict[str, bool]:
    enabled = normalize_boolean(
        payload.get("enabled"),
        default=DEFAULT_CANON_VALIDATION_ENABLED,
    )
    _update_settings({"canon_validation_enabled": enabled})
    return {"canon_validation_enabled": enabled}


def save_experience_mode(payload: dict[str, Any]) -> dict[str, str]:
    mode = normalize_experience_mode(payload.get("experience_mode"))
    _update_settings({"experience_mode": mode})
    return {"experience_mode": mode}


def save_max_chars(payload: dict[str, Any]) -> dict[str, int]:
    try:
        max_chars = int(payload.get("max_chars", 1500))
    except (TypeError, ValueError):
        max_chars = 1500
    max_chars = max(100, min(5000, max_chars))
    _update_settings({"max_chars": max_chars})
    return {"max_chars": max_chars}
