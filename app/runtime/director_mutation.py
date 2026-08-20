from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.director.schemas import DirectorResourceRef
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.world_setting.schemas import WorldSetting


class DirectorProfileBeforeImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    profile: NPCProfile | None


class DirectorEventBeforeImage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    event: DirectorEvent


@dataclass(frozen=True)
class DirectorPendingActorBinding:
    """A deferred Event actor binding waiting for the batch commit."""

    event_id: str
    actor_name: str
    actor_ref: DirectorResourceRef
    turn_index: int


@dataclass
class DirectorPendingMutationPlan:
    """Deferred writes accumulated by confirmation handlers.

    Handlers never write to stores directly. All intended mutations are
    collected here and committed as one recoverable batch only after every
    request has succeeded.
    """

    settings: list[WorldSetting] = field(default_factory=list)
    npc_drafts: list[DirectorNPCDraft] = field(default_factory=list)
    events: list[DirectorEvent] = field(default_factory=list)
    profile_upserts: list[NPCProfile] = field(default_factory=list)
    actor_bindings: list[DirectorPendingActorBinding] = field(
        default_factory=list
    )


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


def recover_pending_director_mutation(uow) -> bool:
    """Rollback an interrupted Director commit; safe to call repeatedly."""
    journal_store = DirectorMutationJournalStore(
        uow.paths.director_mutation_journal
    )
    journal = journal_store.load()
    if journal is None:
        return False

    npc_store = uow.npc_profile
    for before in journal.profile_before_images:
        if before.profile is None:
            npc_store.delete_by_id(before.npc_id)
        else:
            npc_store.upsert(before.profile)

    event_store = uow.director_event
    for before in journal.event_before_images:
        event_store.restore_snapshot(before.event)
    for event_id in reversed(journal.new_event_ids):
        event_store.delete_by_id(event_id)

    npc_draft_store = uow.director_npc
    for npc_id in reversed(journal.new_npc_ids):
        npc_draft_store.delete_by_id(npc_id)

    setting_store = uow.world_setting
    for setting_id in reversed(journal.new_setting_ids):
        setting_store.delete_by_id(setting_id)

    journal_store.clear()
    return True


def commit_director_mutations(
    plan: DirectorPendingMutationPlan,
    uow,
) -> None:
    """Persist one Director batch with durable cross-database rollback data.

    The mirror of :func:`recover_pending_director_mutation`: records new IDs and
    before-images in a durable journal before the first write, then applies every
    deferred write in deterministic order. Any failure rolls the whole batch back
    through the journal before re-raising.

    New confirmations commit their Director batch inside the campaign transaction
    instead and call :func:`apply_director_mutations`; this journaled path is kept
    for legacy startup recovery and direct tests.
    """
    recover_pending_director_mutation(uow)

    validation = _validate_director_plan(plan, uow)

    journal_store = DirectorMutationJournalStore(
        uow.paths.director_mutation_journal
    )
    journal_store.save(
        DirectorMutationJournal(
            new_setting_ids=[item.setting_id for item in plan.settings],
            new_npc_ids=[item.draft_id for item in plan.npc_drafts],
            new_event_ids=[item.event_id for item in plan.events],
            profile_before_images=validation.profile_before,
            event_before_images=validation.event_before,
        )
    )

    try:
        _apply_director_plan(plan, uow)
        journal_store.clear()
    except BaseException:
        recover_pending_director_mutation(uow)
        raise


def apply_director_mutations(
    plan: DirectorPendingMutationPlan,
    uow,
) -> None:
    """Apply one Director batch inside the confirmation's campaign transaction.

    Phase 2 consolidates the canonical databases into ``campaign.db``, so the
    whole confirmation (including the Director batch) commits as one transaction.
    No durable journal is written; the surrounding transaction provides the
    atomicity. Validation still runs up front so an invalid batch fails the
    confirmation before any write is staged.
    """
    _validate_director_plan(plan, uow)
    _apply_director_plan(plan, uow)


@dataclass(frozen=True)
class _DirectorPlanValidation:
    event_before: list[DirectorEventBeforeImage]
    profile_before: list[DirectorProfileBeforeImage]


def _validate_director_plan(
    plan: DirectorPendingMutationPlan,
    uow,
) -> _DirectorPlanValidation:
    settings = plan.settings
    npc_drafts = plan.npc_drafts
    events = plan.events
    profile_upserts = plan.profile_upserts
    actor_bindings = plan.actor_bindings

    world_setting_store = uow.world_setting
    if any(world_setting_store.get_by_id(item.setting_id) for item in settings):
        raise RuntimeError("Director commit would overwrite an existing Setting")
    director_npc_store = uow.director_npc
    if any(director_npc_store.get_by_id(item.draft_id) for item in npc_drafts):
        raise RuntimeError("Director commit would overwrite an existing NPC draft")
    director_event_store = uow.director_event
    if any(director_event_store.get_by_id(item.event_id) for item in events):
        raise RuntimeError("Director commit would overwrite an existing Event")
    event_before = []
    seen_event_ids: set[str] = set()
    for binding in actor_bindings:
        if binding.event_id in seen_event_ids:
            continue
        existing = director_event_store.get_by_id(binding.event_id)
        if existing is None:
            raise ValueError("actor binding references an unknown DirectorEvent")
        seen_event_ids.add(binding.event_id)
        event_before.append(
            DirectorEventBeforeImage(
                event_id=binding.event_id,
                event=existing,
            )
        )

    npc_profile_store = uow.npc_profile
    profile_before = []
    seen_profile_ids: set[str] = set()
    for profile in profile_upserts:
        if profile.npc_id in seen_profile_ids:
            continue
        seen_profile_ids.add(profile.npc_id)
        profile_before.append(
            DirectorProfileBeforeImage(
                npc_id=profile.npc_id,
                profile=npc_profile_store.get_by_id(profile.npc_id),
            )
        )

    return _DirectorPlanValidation(
        event_before=event_before,
        profile_before=profile_before,
    )


def _apply_director_plan(
    plan: DirectorPendingMutationPlan,
    uow,
) -> None:
    settings = plan.settings
    npc_drafts = plan.npc_drafts
    events = plan.events
    profile_upserts = plan.profile_upserts
    actor_bindings = plan.actor_bindings
    if settings:
        store = uow.world_setting
        for setting in settings:
            store.save(setting)
    if npc_drafts:
        store = uow.director_npc
        for draft in npc_drafts:
            store.save(draft)
    if events:
        store = uow.director_event
        for event in events:
            store.save(event)
    if profile_upserts:
        store = uow.npc_profile
        for profile in profile_upserts:
            store.upsert(profile)
    if actor_bindings:
        store = uow.director_event
        for binding in actor_bindings:
            store.bind_actor(
                binding.event_id,
                actor_name=binding.actor_name,
                actor_ref=binding.actor_ref,
                turn_index=binding.turn_index,
            )
