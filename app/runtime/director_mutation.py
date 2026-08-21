from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.director.schemas import DirectorResourceRef
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.world_setting.schemas import WorldSetting


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
    collected here and committed as one batch only after every
    request has succeeded.
    """

    settings: list[WorldSetting] = field(default_factory=list)
    npc_drafts: list[DirectorNPCDraft] = field(default_factory=list)
    events: list[DirectorEvent] = field(default_factory=list)
    profile_upserts: list[NPCProfile] = field(default_factory=list)
    actor_bindings: list[DirectorPendingActorBinding] = field(
        default_factory=list
    )


def apply_director_mutations(
    plan: DirectorPendingMutationPlan,
    uow,
) -> None:
    """Apply one Director batch inside the confirmation's campaign transaction.

    Phase 5: the whole confirmation (including the Director batch) commits as
    one ``campaign.db`` transaction. No durable journal is written; the
    surrounding transaction provides the atomicity. Validation still runs up
    front so an invalid batch fails the confirmation before any write is staged.
    """
    _validate_director_plan(plan, uow)
    _apply_director_plan(plan, uow)


def _validate_director_plan(
    plan: DirectorPendingMutationPlan,
    uow,
) -> None:
    settings = plan.settings
    npc_drafts = plan.npc_drafts
    events = plan.events
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
    seen_event_ids: set[str] = set()
    for binding in actor_bindings:
        if binding.event_id in seen_event_ids:
            continue
        existing = director_event_store.get_by_id(binding.event_id)
        if existing is None:
            raise ValueError("actor binding references an unknown DirectorEvent")
        seen_event_ids.add(binding.event_id)
    return None


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
