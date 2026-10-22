from __future__ import annotations

import json
import os
import threading
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .session import get_campaign_runtime


MAX_INITIALIZATION_DRAFT_BYTES = 128 * 1024
_DRAFT_LOCK = threading.RLock()


class InitializationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[4] = 4
    stage: Literal["config", "campaign_setup"] = "config"
    form: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] | None = None
    campaign_setup: dict[str, Any] | None = None


def _draft_config_version(draft: InitializationDraft) -> int | None:
    """Read the embedded config version while allowing incomplete form values."""
    if draft.config is None:
        return None
    version = draft.config.get("version")
    return version if isinstance(version, int) else None


def load_initialization_draft() -> dict[str, Any] | None:
    runtime = get_campaign_runtime()
    with runtime.operation_submission(), _DRAFT_LOCK:
        try:
            payload = json.loads(
                runtime.paths.initialization_draft_json.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        draft = InitializationDraft.model_validate(payload)
        if _draft_config_version(draft) not in (None, 4):
            raise ValueError("Initialization draft config must use version 4")
        return draft.model_dump(mode="json")


def save_initialization_draft(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = get_campaign_runtime()
    draft = InitializationDraft.model_validate(payload)
    if _draft_config_version(draft) not in (None, 4):
        raise ValueError(
            "Initialization draft config is not version 4; "
            "the draft contract has moved on"
        )
    encoded = json.dumps(
        draft.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    if len(encoded.encode("utf-8")) > MAX_INITIALIZATION_DRAFT_BYTES:
        raise ValueError("Initialization draft is too large")

    with runtime.operation_submission(), _DRAFT_LOCK:
        if runtime.initialization.load() is not None:
            raise RuntimeError("Campaign setup is already committed")
        path = runtime.paths.initialization_draft_json
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            temporary.write_text(encoded, encoding="utf-8")
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return draft.model_dump(mode="json")


def clear_initialization_draft() -> None:
    runtime = get_campaign_runtime()
    with runtime.operation_submission(), _DRAFT_LOCK:
        runtime.paths.initialization_draft_json.unlink(missing_ok=True)
