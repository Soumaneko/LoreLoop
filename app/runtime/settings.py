import json
from typing import Any



def _settings_path():
    from .session import get_campaign_runtime

    return get_campaign_runtime().paths.settings_json


def load_settings() -> dict[str, Any]:
    try:
        return json.loads(_settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        return {}


def save_settings(settings: dict[str, Any]) -> None:
    settings_path = _settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_difficulty(payload: dict[str, Any]) -> dict[str, str]:
    difficulty = str(payload.get("difficulty", "困难")).strip()
    if difficulty not in ("简单", "普通", "困难"):
        difficulty = "困难"
    settings = load_settings()
    settings["difficulty"] = difficulty
    save_settings(settings)
    return {"difficulty": difficulty}


def save_max_chars(payload: dict[str, Any]) -> dict[str, int]:
    try:
        max_chars = int(payload.get("max_chars", 1500))
    except (TypeError, ValueError):
        max_chars = 1500
    max_chars = max(100, min(5000, max_chars))
    settings = load_settings()
    settings["max_chars"] = max_chars
    save_settings(settings)
    return {"max_chars": max_chars}
