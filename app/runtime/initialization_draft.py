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

    version: Literal[1] = 1
    stage: Literal["config", "story_direction"] = "config"
    form: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] | None = None
    story_direction: dict[str, Any] | None = None


def load_initialization_draft() -> dict[str, Any] | None:
    runtime = get_campaign_runtime()
    with runtime.operation_submission(), _DRAFT_LOCK:
        try:
            payload = json.loads(
                runtime.paths.initialization_draft_json.read_text(encoding="utf-8")
            )
        except FileNotFoundError:
            return None
        return InitializationDraft.model_validate(payload).model_dump(mode="json")


def save_initialization_draft(payload: dict[str, Any]) -> dict[str, Any]:
    runtime = get_campaign_runtime()
    draft = InitializationDraft.model_validate(payload)
    encoded = json.dumps(
        draft.model_dump(mode="json"),
        ensure_ascii=False,
        indent=2,
    )
    if len(encoded.encode("utf-8")) > MAX_INITIALIZATION_DRAFT_BYTES:
        raise ValueError("Initialization draft is too large")

    with runtime.operation_submission(), _DRAFT_LOCK:
        if runtime.services.stores.core_memory().load() is not None:
            raise RuntimeError("Initialization is already complete")
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
