from __future__ import annotations

import hashlib
import json
import os
import threading
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.domain.core_memory import (
    CampaignSetupProposal,
    CoreMemory,
    InitConfig,
    build_core_memory,
)
from app.domain.opening_foundation import OpeningFoundation

from .serialization import model_data


InitializationPhase = Literal[
    "proposal_committing",
    "core_memory_committed",
    "foundation_ready",
    "opening_pending",
    "completed",
]


class InitializationRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    phase: InitializationPhase
    fingerprint: str
    config: InitConfig
    campaign_setup: CampaignSetupProposal
    core_memory: CoreMemory
    foundation: OpeningFoundation | None = None
    foundation_token_usage: dict | None = None


class InitializationStore:
    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()

    def load(self) -> InitializationRecord | None:
        with self._lock:
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return None
            return InitializationRecord.model_validate(payload)

    def save(self, record: InitializationRecord) -> None:
        encoded = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.tmp")
            try:
                temporary.write_text(encoded, encoding="utf-8")
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)


def initialization_fingerprint(
    config: InitConfig,
    proposal: CampaignSetupProposal,
) -> str:
    canonical = json.dumps(
        {
            "config": model_data(config),
            "campaign_setup": model_data(proposal),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def commit_campaign_setup(
    config: InitConfig,
    proposal: CampaignSetupProposal,
) -> InitializationRecord:
    from .session import get_campaign_runtime

    runtime = get_campaign_runtime()
    store = runtime.initialization
    fingerprint = initialization_fingerprint(config, proposal)
    core_memory = build_core_memory(config, proposal)
    existing = store.load()

    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise RuntimeError(
                "Campaign setup is already committed and cannot be replaced"
            )
        if existing.config != config or existing.campaign_setup != proposal:
            raise RuntimeError("Committed campaign setup does not match its fingerprint")
        if existing.core_memory != core_memory:
            raise RuntimeError(
                "Committed CoreMemory does not match its config and campaign setup"
            )
        saved_core = runtime.stores.core_memory().load()
        if saved_core is None and existing.phase == "proposal_committing":
            runtime.stores.core_memory().save(existing.core_memory)
        elif saved_core != existing.core_memory:
            raise RuntimeError("Committed CoreMemory does not match initialization")
        if existing.phase == "proposal_committing":
            existing = existing.model_copy(
                update={"phase": "core_memory_committed"}
            )
            store.save(existing)
        _clear_draft_without_invalidating_commit(runtime)
        return existing

    if runtime.stores.core_memory().load() is not None:
        raise RuntimeError("CoreMemory exists without an initialization record")

    record = InitializationRecord(
        phase="proposal_committing",
        fingerprint=fingerprint,
        config=config,
        campaign_setup=proposal,
        core_memory=core_memory,
    )
    store.save(record)
    runtime.stores.core_memory().save(core_memory)
    record = record.model_copy(update={"phase": "core_memory_committed"})
    store.save(record)
    _clear_draft_without_invalidating_commit(runtime)
    return record


def _clear_draft_without_invalidating_commit(runtime) -> None:
    from .initialization_draft import clear_initialization_draft

    try:
        clear_initialization_draft()
    except OSError as exc:
        runtime.diagnostics.warning(
            source="initialization",
            code="initialization_draft_cleanup_failed",
            message=str(exc),
            operation_type="initialization_opening",
        )
