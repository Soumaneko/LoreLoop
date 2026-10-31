"""Read-only, bundled story starts. Save identity belongs to SaveManager."""

import json
from pathlib import Path

from app.core.client_error import ClientValidationError
from app.runtime.app_paths import PACKAGE_ROOT

PRESETS_DIR = PACKAGE_ROOT / "opening_presets"


def list_opening_presets() -> dict:
    return {"presets": json.loads((PRESETS_DIR / "catalog.json").read_text(encoding="utf-8"))}


def opening_preset(preset_id: str) -> tuple[dict, Path]:
    for preset in list_opening_presets()["presets"]:
        if isinstance(preset_id, str) and preset_id == preset["id"]:
            return preset, PRESETS_DIR / f"{preset['id']}.zip"
    raise ClientValidationError("这个预设不存在，请重新选择故事开局。")
