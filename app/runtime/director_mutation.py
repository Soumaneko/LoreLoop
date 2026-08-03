from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import NPCProfile


class DirectorProfileBeforeImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    profile: NPCProfile | None


class DirectorEventBeforeImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    event: DirectorEvent


class DirectorMutationJournal(BaseModel):
    """Rollback data for one cross-database Director confirmation commit."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    new_setting_ids: list[str] = Field(default_factory=list)
    new_npc_ids: list[str] = Field(default_factory=list)
    new_event_ids: list[str] = Field(default_factory=list)
    profile_before_images: list[DirectorProfileBeforeImage] = Field(
        default_factory=list
    )
    event_before_images: list[DirectorEventBeforeImage] = Field(
        default_factory=list
    )

    @model_validator(mode="after")
    def require_unique_targets(self):
        groups = (
            self.new_setting_ids,
            self.new_npc_ids,
            self.new_event_ids,
            [item.npc_id for item in self.profile_before_images],
            [item.event_id for item in self.event_before_images],
        )
        if any(len(group) != len(set(group)) for group in groups):
            raise ValueError("Director mutation journal contains duplicate targets")
        return self


class DirectorMutationJournalStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> DirectorMutationJournal | None:
        try:
            payload = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        return DirectorMutationJournal.model_validate_json(payload)

    def save(self, journal: DirectorMutationJournal) -> None:
        encoded = json.dumps(
            journal.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)


def recover_pending_director_mutation(stores) -> bool:
    """Rollback an interrupted Director commit; safe to call repeatedly."""
    journal_store = DirectorMutationJournalStore(
        stores.paths.director_mutation_journal
    )
    journal = journal_store.load()
    if journal is None:
        return False

    with stores.npc_profile() as store:
        for before in journal.profile_before_images:
            if before.profile is None:
                store.delete_by_id(before.npc_id)
            else:
                store.upsert(before.profile)

    with stores.director_event() as store:
        for before in journal.event_before_images:
            store.restore_snapshot(before.event)
        for event_id in reversed(journal.new_event_ids):
            store.delete_by_id(event_id)

    with stores.director_npc() as store:
        for npc_id in reversed(journal.new_npc_ids):
            store.delete_by_id(npc_id)

    with stores.world_setting() as store:
        for setting_id in reversed(journal.new_setting_ids):
            store.delete_by_id(setting_id)

    journal_store.clear()
    return True
