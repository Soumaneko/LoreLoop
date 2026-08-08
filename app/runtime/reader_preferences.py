from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .paths import APP_CONFIG_DIR


FONT_SIZES = {"14", "15", "16.5", "18", "20"}
FONT_FAMILIES = {"serif", "kaiti", "ming", "sans"}
THEMES = {"dark", "sepia", "light"}
READER_PREFERENCES_FILE = APP_CONFIG_DIR / "reader_preferences.json"
_PREFERENCES_WRITE_LOCK = threading.RLock()


@dataclass(frozen=True)
class ReaderPreferences:
    font_size: str = "16.5"
    font_family: str = "serif"
    theme: str = "dark"
    show_token_usage: bool = False
    one_click_confirm: bool = False

    @classmethod
    def from_dict(cls, payload: object) -> "ReaderPreferences":
        if not isinstance(payload, dict):
            raise ValueError("Reader preferences must be a JSON object")
        return cls(
            font_size=normalize_font_size(payload.get("font_size")),
            font_family=normalize_font_family(payload.get("font_family")),
            theme=normalize_theme(payload.get("theme")),
            show_token_usage=payload.get("show_token_usage") is True,
            one_click_confirm=payload.get("one_click_confirm") is True,
        )

    def public_payload(self) -> dict[str, Any]:
        return asdict(self)


class ReaderPreferencesStore:
    def __init__(self, path: str | Path = READER_PREFERENCES_FILE) -> None:
        self.path = Path(path)

    def load(self) -> ReaderPreferences:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return ReaderPreferences.from_dict(payload)
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            return ReaderPreferences()

    def save(self, preferences: ReaderPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            temporary.write_text(
                json.dumps(
                    preferences.public_payload(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


def normalize_font_size(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in FONT_SIZES else "16.5"


def normalize_font_family(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in FONT_FAMILIES else "serif"


def normalize_theme(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in THEMES else "dark"


def load_reader_preferences(
    *,
    store: ReaderPreferencesStore | None = None,
) -> dict[str, Any]:
    return (store or ReaderPreferencesStore()).load().public_payload()


def save_reader_preferences(
    payload: dict[str, Any],
    *,
    store: ReaderPreferencesStore | None = None,
) -> dict[str, Any]:
    preferences = ReaderPreferences.from_dict(payload)
    with _PREFERENCES_WRITE_LOCK:
        (store or ReaderPreferencesStore()).save(preferences)
    return preferences.public_payload()
