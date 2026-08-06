from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from concurrent.futures import ThreadPoolExecutor

from app.core.text import eligible_highlight_term
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.creative_brief import draw_creative_brief
from app.domain.director import (
    DirectorAgent,
    DirectorObservation,
    DirectorResourceRef,
    DirectorSkillExecutionBatch,
    DirectorSkillService,
    DirectorStageMaterial,
    DirectorStageResult,
)
from app.domain.director_event.schemas import (
    DirectorActorRef,
    DirectorEvent,
    utc_now,
)
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.schemas import DirectorNPCDraft, NPCCreationContext
from app.domain.npc_profile.schemas import (
    DISCLOSED_FIELD_SETS,
    NPC_DISCLOSURE_ORDER,
    NPCProfile,
)
from app.domain.npc_profile.workflows import (
    attach_transient_alias_for_rename,
    backfill_new_npc_profile,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.creator import WorldSettingCreator
from app.domain.world_setting.schemas import WorldSetting
from app.runtime.director_mutation import (
    DirectorEventBeforeImage,
    DirectorMutationJournal,
    DirectorMutationJournalStore,
    DirectorProfileBeforeImage,
    recover_pending_director_mutation,
)

from .director_skill_specs import (
    BindEventActorArguments,
    CreateEventArguments,
    CreateNPCArguments,
    EnrichNPCArguments,
    ForeshadowSettingArguments,
    IntroduceNPCArguments,
    IntrudeEngagedEventArguments,
    NPC_VIEW_ALWAYS_KEPT,
    PresentEventArguments,
    UpgradeDisclosureArguments,
    get_director_skill_specs,
)
from .generation_profile_helpers import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_fact_texts,
)


DIRECTOR_WAVE_MAX_WORKERS = 3


@dataclass
class _PendingActorBinding:
    event_id: str
    actor_name: str
    actor_ref: DirectorResourceRef
    turn_index: int


@dataclass
class _DirectorMutationPlan:
    """Deferred writes accumulated by confirmation handlers.

    Handlers never write to stores directly. All intended mutations are
    collected here and committed as one recoverable batch only after every
    request has succeeded.
    """

    settings: list[WorldSetting] = field(default_factory=list)
    npc_drafts: list[DirectorNPCDraft] = field(default_factory=list)
    events: list[DirectorEvent] = field(default_factory=list)
    profile_upserts: list[NPCProfile] = field(default_factory=list)
    actor_bindings: list[_PendingActorBinding] = field(default_factory=list)


class _FreshStoreProxy:
    """Give each concurrent skill operation its own SQLite connection."""

    def __init__(self, factory):
        self._factory = factory

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            with self._factory() as store:
                return getattr(store, name)(*args, **kwargs)

        return invoke


def _register_skill(
    service,
    name: str,
    *,
    handler,
    arguments_validator=None,
) -> None:
    """Register a skill using its semantic spec declared in director_skill_specs."""
    spec = next(
        (item for item in get_director_skill_specs() if item.name == name),
        None,
    )
    if spec is None:
        raise ValueError(f"unknown director skill spec: {name}")
    service.register(
        name=spec.name,
        description=spec.description,
        phases=set(spec.phases),
        mutates=spec.mutates,
        arguments_model=spec.arguments_model,
        handler=handler,
        arguments_validator=arguments_validator,
        max_requests_per_batch=spec.max_requests_per_batch,
    )


def _run_setting_creation_wave(
    decision,
    *,
    gateway,
    core_memory: CoreMemory,
    available_settings: list,
    turn_index: int,
    trace_id: str,
):
    """Create one missing Setting per unique requirement in memory.

    Requests that declare the identical missing requirement (same ``kind`` and
    ``setting_description``) share a single generated Setting instead of
    producing duplicate assets. Nothing is persisted here; the caller commits
    the generated Settings only after the whole confirmation batch succeeds.
    """
    argument_models = {
        "create_npc": CreateNPCArguments,
        "create_event": CreateEventArguments,
        "enrich_npc": EnrichNPCArguments,
    }
    settings_by_id = {
        setting.setting_id: setting for setting in available_settings
    }
    parsed_arguments: dict[str, object] = {}
    missing_groups: dict[tuple[str, str], list[tuple[object, object]]] = {}

    for request in decision.requests:
        arguments_model = argument_models.get(request.skill)
        if arguments_model is None:
            continue
        arguments = arguments_model.model_validate(request.arguments)
        parsed_arguments[request.request_id] = arguments
        requirement = arguments.setting_requirements[0]
        matched = settings_by_id.get(requirement.matched_setting_id)
        if matched is None or matched.kind != requirement.kind:
            key = (requirement.kind, requirement.setting_description)
            missing_groups.setdefault(key, []).append((request, requirement))

    created_by_request: dict[str, object] = {}
    created_settings: list = []
    if missing_groups:
        with ThreadPoolExecutor(
            max_workers=min(DIRECTOR_WAVE_MAX_WORKERS, len(missing_groups)),
            thread_name_prefix="director-setting",
        ) as executor:
            futures = {
                executor.submit(
                    _create_missing_setting,
                    group[0][1],
                    gateway=gateway,
                    core_memory=core_memory,
                    available_settings=available_settings,
                    turn_index=turn_index,
                    trace_id=trace_id,
                ): key
                for key, group in missing_groups.items()
            }
            for future in futures:
                setting = future.result()
                created_settings.append(setting)
                for request, _requirement in missing_groups[futures[future]]:
                    created_by_request[request.request_id] = setting

    if not created_by_request:
        return decision, created_settings

    patched_requests = []
    for request in decision.requests:
        created = created_by_request.get(request.request_id)
        if created is None:
            patched_requests.append(request)
            continue
        arguments = parsed_arguments[request.request_id]
        requirement = arguments.setting_requirements[0].model_copy(
            update={"matched_setting_id": created.setting_id}
        )
        patched_arguments = arguments.model_copy(
            update={"setting_requirements": [requirement]}
        )
        patched_requests.append(
            request.model_copy(
                update={"arguments": patched_arguments.model_dump(mode="json")}
            )
        )
    return (
        decision.model_copy(update={"requests": patched_requests}),
        created_settings,
    )


def _create_missing_setting(
    requirement,
    *,
    gateway,
    core_memory: CoreMemory,
    available_settings: list,
    turn_index: int,
    trace_id: str,
):
    return WorldSettingCreator(gateway).create(
        kind=requirement.kind,
        guidance=requirement.setting_description,
        core_memory=core_memory,
        existing_settings=available_settings,
        turn_index=turn_index,
        trace_id=trace_id,
    )


def _commit_director_mutations(
    plan: _DirectorMutationPlan,
    stores,
) -> None:
    """Persist one Director batch with durable cross-database rollback data."""
    recover_pending_director_mutation(stores)

    with stores.world_setting() as store:
        if any(store.get_by_id(item.setting_id) for item in plan.settings):
            raise RuntimeError("Director commit would overwrite an existing Setting")
    with stores.director_npc() as store:
        if any(store.get_by_id(item.draft_id) for item in plan.npc_drafts):
            raise RuntimeError("Director commit would overwrite an existing NPC draft")
    with stores.director_event() as store:
        if any(store.get_by_id(item.event_id) for item in plan.events):
            raise RuntimeError("Director commit would overwrite an existing Event")
        event_before = []
        seen_event_ids: set[str] = set()
        for binding in plan.actor_bindings:
            if binding.event_id in seen_event_ids:
                continue
            existing = store.get_by_id(binding.event_id)
            if existing is None:
                raise ValueError("actor binding references an unknown DirectorEvent")
            seen_event_ids.add(binding.event_id)
            event_before.append(
                DirectorEventBeforeImage(
                    event_id=binding.event_id,
                    event=existing,
                )
            )

    with stores.npc_profile() as store:
        profile_before = []
        seen_profile_ids: set[str] = set()
        for profile in plan.profile_upserts:
            if profile.npc_id in seen_profile_ids:
                continue
            seen_profile_ids.add(profile.npc_id)
            profile_before.append(
                DirectorProfileBeforeImage(
                    npc_id=profile.npc_id,
                    profile=store.get_by_id(profile.npc_id),
                )
            )

    journal_store = DirectorMutationJournalStore(
        stores.paths.director_mutation_journal
    )
    journal_store.save(
        DirectorMutationJournal(
            new_setting_ids=[item.setting_id for item in plan.settings],
            new_npc_ids=[item.draft_id for item in plan.npc_drafts],
            new_event_ids=[item.event_id for item in plan.events],
            profile_before_images=profile_before,
            event_before_images=event_before,
        )
    )

    try:
        if plan.settings:
            with stores.world_setting() as store:
                for setting in plan.settings:
                    store.save(setting)
        if plan.npc_drafts:
            with stores.director_npc() as store:
                for draft in plan.npc_drafts:
                    store.save(draft)
        if plan.events:
            with stores.director_event() as store:
                for event in plan.events:
                    store.save(event)
        if plan.profile_upserts:
            with stores.npc_profile() as store:
                for profile in plan.profile_upserts:
                    store.upsert(profile)
        if plan.actor_bindings:
            with stores.director_event() as store:
                for binding in plan.actor_bindings:
                    store.bind_actor(
                        binding.event_id,
                        actor_name=binding.actor_name,
                        actor_ref=binding.actor_ref,
                        turn_index=binding.turn_index,
                    )
        journal_store.clear()
    except BaseException:
        recover_pending_director_mutation(stores)
        raise


def run_generation_director_agent(
    *,
    gateway,
    stores,
    core_memory: CoreMemory,
    current_state: CurrentState,
    user_input: str,
    context_facts: list[str],
    recent_summaries: list[SceneSummary],
    turn_index: int,
    trace_id: str,
    turn_resolution_json: str = "",
    touched_event_candidates: list[str] | None = None,
    npc_profiles: list[NPCProfile],
) -> tuple[DirectorStageResult, object]:
    recover_pending_director_mutation(stores)
    service = DirectorSkillService()
    touched_event_candidates = touched_event_candidates or []
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        active_events = event_store.list_active()
        touched_event_ids = set(touched_event_candidates)
        hidden_events = [
            event
            for event in active_events
            if event.event_id not in touched_event_ids
            and event.stage != "engaged"
        ]
        engaged_events = [
            event
            for event in active_events
            if event.stage == "engaged" or event.event_id in touched_event_ids
        ]
        # Only not-yet-introduced Director NPC drafts are observable during
        # generation. Introduced Director NPCs are already regular NPC
        # Profiles and stay visible through the profile channel.
        director_npcs = [
            npc
            for npc in director_npc_store.list_all()
            if npc.status == "prepared"
        ]
        selected_npc_profiles = npc_profiles
        settings = setting_store.list_all()
        with stores.director_log() as log_store:
            recent_director_log = log_store.recent()

        _register_skill(
            service,
            "present_event",
            handler=lambda args, guidance=None: _present_event(
                event_store,
                args,
                guidance=guidance,
                touched_event_ids=touched_event_ids,
            ),
            arguments_validator=lambda args: _require_hidden_event(
                event_store,
                args.event_id,
                touched_event_ids=touched_event_ids,
            ),
        )
        _register_skill(
            service,
            "introduce_npc",
            handler=lambda args, guidance=None: _introduce_npc(
                director_npc_store,
                args,
                guidance=guidance,
            ),
            arguments_validator=lambda args: _require_director_draft(
                director_npc_store,
                args.npc_id,
            ),
        )
        _register_skill(
            service,
            "upgrade_disclosure",
            handler=lambda args, guidance=None: _upgrade_disclosure(
                npc_profile_store,
                args,
                guidance=guidance,
            ),
            arguments_validator=lambda args: _require_disclosure_upgradeable(
                npc_profile_store,
                args.npc_id,
                args.new_disclosure_level,
                visible_profiles=selected_npc_profiles,
            ),
        )
        _register_skill(
            service,
            "foreshadow_setting",
            handler=lambda args, guidance=None: _foreshadow_setting(
                setting_store,
                args,
                guidance=guidance,
            ),
            arguments_validator=lambda args: _require_setting(
                setting_store,
                args.setting_id,
            ),
        )
        _register_skill(
            service,
            "intrude_engaged_event",
            handler=lambda args, guidance=None: _intrude_engaged_event(
                event_store,
                args,
                guidance=guidance,
                touched_event_ids=touched_event_ids,
            ),
            arguments_validator=lambda args: _require_engaged_event(
                event_store,
                args.event_id,
                touched_event_ids=touched_event_ids,
            ),
        )
        observation = DirectorObservation(
            phase="generation",
            turn_index=turn_index,
            player_input=user_input,
            core_memory=core_memory.model_dump(mode="json"),
            current_state=current_state.model_dump(mode="json"),
            context_facts=context_facts,
            recent_director_log=recent_director_log,
            recent_summaries=[
                summary.model_dump(mode="json")
                for summary in recent_summaries
            ],
            turn_resolution=(
                json.loads(turn_resolution_json)
                if turn_resolution_json
                else None
            ),
            resources={
                "events": [
                    {
                        **event.semantic_view(),
                        "hook_pool": [
                            {
                                "hook_id": hook.hook_id,
                                "kind": hook.kind,
                                "content": hook.content,
                            }
                            for hook in event.hook_pool
                            if not hook.consumed
                        ],
                    }
                    for event in hidden_events
                ],
                "engaged_events": [
                    {
                        **event.semantic_view(),
                        "stage": "engaged",
                        "touched_this_turn": (
                            event.event_id in touched_event_ids
                        ),
                        "world_impact": (
                            event.world_impact.model_dump(mode="json")
                            if event.world_impact is not None
                            else None
                        ),
                    }
                    for event in engaged_events
                ],
                "drafts": [
                    {
                        "npc_id": npc.draft_id,
                        "source": "director_npc",
                        **npc.private_view().model_dump(mode="json"),
                    }
                    for npc in director_npcs
                ],
                "npcs": [
                    {
                        "npc_id": profile.npc_id,
                        "source": "npc_profile",
                        **profile.director_view().model_dump(mode="json"),
                    }
                    for profile in selected_npc_profiles
                ],
                "settings": [
                    setting.reference_view()
                    for setting in settings
                ],
            },
        )
        decision = DirectorAgent(gateway, service).decide_generation(
            observation,
            trace_id=trace_id,
        )
        executions = service.execute_batch(
            decision,
            phase="generation",
        )
    materials = [
        DirectorStageMaterial.model_validate(execution.output)
        for execution in executions.executions
    ]
    return (
        DirectorStageResult(
            reasoning=decision.reasoning,
            materials=materials,
        ),
        decision,
    )


def run_confirmation_director_agent(
    *,
    gateway,
    stores,
    core_memory: CoreMemory,
    current_state: CurrentState,
    user_input: str,
    scene_facts: dict,
    recent_summaries: list[SceneSummary],
    selection_summaries: list[SceneSummary],
    turn_index: int,
    trace_id: str,
    new_npc_profiles: list[NPCProfile] | None = None,
    scene_text: str = "",
    director_log_entry: dict | None = None,
) -> tuple[object, DirectorSkillExecutionBatch]:
    recover_pending_director_mutation(stores)
    service = DirectorSkillService()
    with stores.director_log() as log_store:
        recent_director_log = log_store.recent()
    if director_log_entry:
        recent_director_log = [director_log_entry, *recent_director_log][:6]
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        available_settings = setting_store.list_all()
        plan = _DirectorMutationPlan()
        plan_lock = threading.Lock()
        settings_by_id = {
            setting.setting_id: setting for setting in available_settings
        }

        def dispatch_setting_requirements(requirements):
            """Resolve the requirement from available + Wave-A in-memory Settings."""
            resolved: list = []
            for requirement in requirements:
                if requirement.matched_setting_id is not None:
                    setting = settings_by_id.get(
                        requirement.matched_setting_id
                    )
                    if setting is not None and setting.kind == requirement.kind:
                        resolved.append(setting)
                        continue
                raise ValueError(
                    "confirmation skill reached Wave B without a valid Setting"
                )
            return resolved

        def create_npc(args: CreateNPCArguments, guidance: str | None = None):
            settings = dispatch_setting_requirements(args.setting_requirements)
            setting_ids = [setting.setting_id for setting in settings]
            draft = DirectorNPCCreator(gateway).create(
                core_memory=core_memory,
                existing_npcs=npc_profile_store.list_all(),
                reserved_npcs=[
                    {
                        "name": npc.name,
                        "role": npc.role,
                        "core_motivation": npc.core_motivation,
                        "hidden_secret": npc.hidden_secret,
                    }
                    for npc in director_npc_store.list_all()
                    if npc.status != "retired"
                ],
                creation_context=NPCCreationContext(
                    setting_ids=setting_ids,
                ),
                settings=settings,
                creative_brief=draw_creative_brief(),
                protagonist_name=current_state.player.name,
                turn_index=turn_index,
                trace_id=trace_id,
                guidance=guidance,
            )
            with plan_lock:
                plan.npc_drafts.append(draft)
            return {"npc_id": draft.draft_id, "name": draft.name}

        def create_event(
            args: CreateEventArguments,
            guidance: str | None = None,
        ):
            settings = dispatch_setting_requirements(args.setting_requirements)
            setting_ids = [setting.setting_id for setting in settings]
            event_guidance = guidance or ""
            required_actors: list[dict] = []
            bound_actors: list[DirectorActorRef] = []
            actor_inputs_by_key = {}
            for index, actor_input in enumerate(args.actors, start=1):
                actor_key = f"actor_{index}"
                actor_inputs_by_key[actor_key] = actor_input
                if actor_input.source == "new":
                    required_actors.append({
                        "actor_key": actor_key,
                        "source": "new",
                        "guidance": actor_input.guidance,
                    })
                    continue

                draft = director_npc_store.get_by_id(actor_input.npc_id)
                profile = npc_profile_store.get_by_id(actor_input.npc_id)
                if draft is not None and draft.status != "retired":
                    actor_name = draft.name
                    actor_profile = {
                        "resource_type": "director_npc",
                        **draft.private_view().model_dump(mode="json"),
                    }
                elif profile is not None and profile.active:
                    actor_name = profile.name
                    actor_profile = {
                        "resource_type": "npc_profile",
                        "npc_id": profile.npc_id,
                        **profile.director_view().model_dump(mode="json"),
                    }
                else:
                    raise ValueError("create_event references an unknown active NPC")
                required_actors.append({
                    "actor_key": actor_key,
                    "source": "existing",
                    "npc_id": actor_input.npc_id,
                    "guidance": actor_input.guidance,
                    "profile": actor_profile,
                })
                bound_actors.append(
                    DirectorActorRef(
                        name=actor_name,
                        ref=DirectorResourceRef(
                            kind="npc",
                            resource_id=actor_input.npc_id,
                        ),
                    )
                )
            selection = DirectorEventSelector(gateway).select_or_create(
                role=args.role,
                events=event_store.list_all(),
                current_state=current_state,
                core_memory=core_memory,
                recent_summaries=selection_summaries,
                settings=settings,
                required_actors=required_actors,
                trace_id=trace_id,
                guidance=event_guidance,
            )
            event = selection.to_event(
                role=args.role,
                turn_index=turn_index,
                actors=bound_actors,
                current_clock=current_state.world_clock,
                source_setting_ids=setting_ids,
            )
            drafts: list = []
            new_npc_ids: list[str] = []
            actor_names: list[str] = [actor.name for actor in bound_actors]
            role_by_key = {
                role.actor_key: role.role_in_event
                for role in selection.actor_roles
            }
            actor_roles_view = [
                role.model_dump(mode="json")
                for role in selection.actor_roles
            ]
            recent_creations: list[dict] = [{
                **event.semantic_view(),
                "actor_roles": actor_roles_view,
            }]
            for actor_key, actor_input in actor_inputs_by_key.items():
                if actor_input.source != "new":
                    continue
                npc_guidance = (
                    f"{actor_input.guidance}\n"
                    f"在本事件中的职责：{role_by_key[actor_key]}"
                )
                draft = DirectorNPCCreator(gateway).create(
                    core_memory=core_memory,
                    existing_npcs=npc_profile_store.list_all(),
                    reserved_npcs=[
                        {
                            "name": npc.name,
                            "role": npc.role,
                            "core_motivation": npc.core_motivation,
                            "hidden_secret": npc.hidden_secret,
                        }
                        for npc in director_npc_store.list_all()
                        if npc.status != "retired"
                    ] + [
                        {
                            "name": staged.name,
                            "role": staged.role,
                            "core_motivation": staged.core_motivation,
                            "hidden_secret": staged.hidden_secret,
                        }
                        for staged in drafts
                    ],
                    creation_context=NPCCreationContext(
                        setting_ids=setting_ids
                    ),
                    settings=settings,
                    creative_brief=draw_creative_brief(),
                    protagonist_name=current_state.player.name,
                    turn_index=turn_index,
                    trace_id=trace_id,
                    guidance=npc_guidance,
                    recent_creations=recent_creations,
                )
                drafts.append(draft)
                actor_ref = DirectorResourceRef(
                    kind="npc",
                    resource_id=draft.draft_id,
                )
                actors = list(event.actors)
                clean_name = draft.name.strip()
                replaced = False
                for index, existing in enumerate(actors):
                    if existing.ref is not None and existing.ref == actor_ref:
                        replaced = True
                        break
                    if clean_name and existing.name == clean_name and existing.ref is None:
                        actors[index] = DirectorActorRef(
                            name=clean_name,
                            ref=actor_ref,
                        )
                        replaced = True
                        break
                if not replaced and clean_name and not any(
                    existing.name == clean_name and existing.ref == actor_ref
                    for existing in actors
                ):
                    actors.append(
                        DirectorActorRef(
                            name=clean_name,
                            ref=actor_ref,
                        )
                    )
                event = event.model_copy(
                    update={
                        "actors": actors,
                        "updated_turn": turn_index,
                        "updated_at": utc_now(),
                    }
                )
                recent_creations[0] = {
                    **event.semantic_view(),
                    "actor_roles": actor_roles_view,
                }
                recent_creations.append(
                    {
                        "kind": "npc",
                        "npc_id": draft.draft_id,
                        "name": draft.name,
                        "role": draft.role,
                        "core_motivation": draft.core_motivation,
                    }
                )
                new_npc_ids.append(draft.draft_id)
                actor_names.append(draft.name)
            with plan_lock:
                plan.events.append(event)
                plan.npc_drafts.extend(drafts)
            return {
                "event_id": event.event_id,
                "role": event.role,
                "new_npc_ids": new_npc_ids,
                "actor_names": actor_names,
            }

        def bind_actor(args: BindEventActorArguments, guidance: str | None = None):
            draft = director_npc_store.get_by_id(args.npc_id)
            profile = npc_profile_store.get_by_id(args.npc_id)
            if draft is not None and draft.status != "retired":
                actor_name = draft.name
            elif profile is not None:
                actor_name = profile.name
            else:
                raise ValueError("bind_event_actor references an unknown NPC")
            with plan_lock:
                plan.actor_bindings.append(
                    _PendingActorBinding(
                        event_id=args.event_id,
                        actor_name=actor_name,
                        actor_ref=DirectorResourceRef(
                            kind="npc",
                            resource_id=args.npc_id,
                        ),
                        turn_index=turn_index,
                    )
                )
            return {"event_id": args.event_id, "npc_id": args.npc_id}

        def enrich_npc(args: EnrichNPCArguments, guidance: str | None = None):
            settings = dispatch_setting_requirements(args.setting_requirements)
            setting_ids = [setting.setting_id for setting in settings]
            original = next(
                (
                    profile
                    for profile in (new_npc_profiles or [])
                    if profile.npc_id == args.npc_id
                ),
                None,
            )
            if original is None:
                original = npc_profile_store.get_by_id(args.npc_id)
            if original is None:
                raise ValueError("enrich_npc references an unknown npc_id")
            backfilled = backfill_new_npc_profile(
                profile=original,
                existing_profiles=[
                    npc
                    for npc in npc_profile_store.list_all()
                    if npc.npc_id != args.npc_id
                ],
                core_memory=core_memory,
                gateway=gateway,
                npc_store=npc_profile_store,
                settings=settings,
                trace_id=trace_id,
                guidance=guidance,
            )
            backfilled.source_setting_ids = list(setting_ids)
            if backfilled.name != original.name:
                attach_transient_alias_for_rename(
                    backfilled,
                    old_name=original.name,
                    turn_index=turn_index,
                )
            with plan_lock:
                plan.profile_upserts.append(backfilled)
            return {
                "npc_id": backfilled.npc_id,
                "name": backfilled.name,
            }

        _register_skill(
            service,
            "create_npc",
            handler=create_npc,
        )
        _register_skill(
            service,
            "create_event",
            handler=create_event,
        )
        _register_skill(
            service,
            "bind_event_actor",
            handler=bind_actor,
            arguments_validator=lambda args: _require_event_actor_binding(
                event_store,
                director_npc_store,
                npc_profile_store,
                event_id=args.event_id,
                npc_id=args.npc_id,
            ),
        )
        if new_npc_profiles:
            _register_skill(
                service,
                "enrich_npc",
                handler=enrich_npc,
                arguments_validator=lambda args: _require_new_npc(
                    npc_profile_store,
                    new_npc_profiles,
                    args.npc_id,
                ),
            )

        active_events = [
            event
            for event in event_store.list_all()
            if event.stage != "resolved"
        ]
        scene_fact_texts = (
            [str(scene_facts.get("scene_focus") or "")]
            + [str(fact.get("fact") or "") for fact in scene_facts.get("facts", [])]
            if isinstance(scene_facts, dict)
            else []
        )
        npc_detail_profiles = merge_profile_selections(
            select_profiles_by_fact_texts(
                texts=scene_fact_texts,
                npc_store=npc_profile_store,
            ),
            select_profiles_by_active_events(
                events=active_events,
                npc_store=npc_profile_store,
            ),
            limit=8,
        )

        observation = DirectorObservation(
            phase="confirmation",
            turn_index=turn_index,
            player_input=user_input,
            core_memory=core_memory.model_dump(mode="json"),
            current_state=current_state.model_dump(mode="json"),
            scene_facts=scene_facts,
            recent_director_log=recent_director_log,
            recent_summaries=[
                summary.model_dump(mode="json")
                for summary in recent_summaries
            ],
            resources={
                "events": [
                    {
                        **event.semantic_view(),
                        "hook_pool": [
                            {
                                "hook_id": hook.hook_id,
                                "kind": hook.kind,
                                "content": hook.content,
                            }
                            for hook in event.hook_pool
                            if not hook.consumed
                        ],
                    }
                    for event in active_events
                ],
                "npcs": [
                    {
                        "npc_id": profile.npc_id,
                        "source": "npc_profile",
                        "name": profile.name,
                        "role": profile.role,
                        "aliases": list(profile.aliases),
                        "faction": profile.faction,
                        "public_summary": profile.public_summary,
                    }
                    for profile in npc_profile_store.list_all()
                ],
                "drafts": [
                    {
                        "npc_id": npc.draft_id,
                        "source": "director_npc",
                        **npc.private_view().model_dump(mode="json"),
                    }
                    for npc in director_npc_store.list_all()
                    if npc.status == "prepared"
                ],
                "npc_details": [
                    {
                        "npc_id": profile.npc_id,
                        "source": "npc_profile",
                        **profile.director_view().model_dump(mode="json"),
                    }
                    for profile in npc_detail_profiles
                ],
                "settings": [
                    setting.reference_view()
                    for setting in setting_store.list_all()
                ],
                "new_npcs": [
                    {
                        "npc_id": profile.npc_id,
                        **profile.director_view().model_dump(mode="json"),
                    }
                    for profile in (new_npc_profiles or [])
                ],
            },
        )
        decision = DirectorAgent(gateway, service).decide_confirmation(
            observation,
            trace_id=trace_id,
        )
        service.validate_batch(decision, phase="confirmation")
        decision, created_settings = _run_setting_creation_wave(
            decision,
            gateway=gateway,
            core_memory=core_memory,
            available_settings=available_settings,
            turn_index=turn_index,
            trace_id=trace_id,
        )
        # A Setting created in this same confirmation may already appear in the
        # confirmed scene text; the appearance node runs before this stage, so
        # its first mention would otherwise never be counted and the record
        # stays hidden from the general archive view.
        folded_text = scene_text.casefold()
        for setting in created_settings:
            if (
                eligible_highlight_term(setting.name)
                and setting.name.casefold() in folded_text
            ):
                setting.hit_count = 1
        plan.settings.extend(created_settings)
        for setting in created_settings:
            settings_by_id[setting.setting_id] = setting

        # Wave B may execute every request concurrently. Each proxy opens a
        # dedicated SQLite connection per operation; semantic conflicts are
        # prevented by the upstream Director contract.
        event_store = _FreshStoreProxy(stores.director_event)
        director_npc_store = _FreshStoreProxy(stores.director_npc)
        npc_profile_store = _FreshStoreProxy(stores.npc_profile)
        setting_store = _FreshStoreProxy(stores.world_setting)
        executions = service.execute_batch(
            decision,
            phase="confirmation",
            max_workers=DIRECTOR_WAVE_MAX_WORKERS,
        )
        _commit_director_mutations(plan, stores)
    return decision, executions


def _present_event(
    store,
    args: PresentEventArguments,
    *,
    guidance: str | None,
    touched_event_ids: set[str] | None = None,
) -> DirectorStageMaterial:
    event = store.get_by_id(args.event_id)
    if event is None or event.stage != "hidden":
        raise ValueError(
            "present_event requires a hidden event (engaged events are "
            "steered via intrude_engaged_event)"
        )
    touched_event_ids = touched_event_ids or set()
    if event.event_id in touched_event_ids:
        raise ValueError(
            "present_event is only for indirect contact; directly engaged "
            "events are steered via intrude_engaged_event"
        )
    hook = next(
        (hook for hook in event.hook_pool if hook.hook_id == args.hook_id),
        None,
    )
    if hook is None:
        raise ValueError("present_event references an unknown hook_id")
    if hook.consumed:
        raise ValueError("present_event references a consumed hook_id")
    payload = {
        "event_id": event.event_id,
        "title": event.title,
        "stage": "hidden",
        "hook": {
            "hook_id": hook.hook_id,
            "kind": hook.kind,
            "content": hook.content,
        },
    }
    return DirectorStageMaterial(
        kind="event",
        resource_id=event.event_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _introduce_npc(
    director_npc_store,
    args: IntroduceNPCArguments,
    *,
    guidance: str | None,
) -> DirectorStageMaterial:
    draft = director_npc_store.get_by_id(args.npc_id)
    if draft is None or draft.status != "prepared":
        raise ValueError("introduce_npc requires a prepared director NPC draft")
    payload = {
        "source": "director_npc",
        **draft.private_view().model_dump(
            mode="json",
            exclude_none=True,
        ),
    }
    allowed = DISCLOSED_FIELD_SETS[args.view_mode]
    if allowed is not None:
        payload = {
            key: value
            for key, value in payload.items()
            if key in allowed or key in NPC_VIEW_ALWAYS_KEPT
        }
    payload["view_mode"] = args.view_mode
    return DirectorStageMaterial(
        kind="npc",
        resource_id=args.npc_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _upgrade_disclosure(
    npc_profile_store,
    args: UpgradeDisclosureArguments,
    *,
    guidance: str | None,
) -> DirectorStageMaterial:
    profile = npc_profile_store.get_by_id(args.npc_id)
    if profile is None:
        raise ValueError("upgrade_disclosure references an unknown NPC profile")
    current = profile.disclosure_level
    if NPC_DISCLOSURE_ORDER[args.new_disclosure_level] <= NPC_DISCLOSURE_ORDER[current]:
        raise ValueError(
            "upgrade_disclosure only supports raising the disclosure level"
        )
    payload = {
        "source": "npc_profile",
        "npc_id": profile.npc_id,
        "disclosure_upgrade": True,
        "new_disclosure_level": args.new_disclosure_level,
        **profile.disclosure_view(args.new_disclosure_level).model_dump(
            mode="json",
            exclude_none=True,
        ),
    }
    return DirectorStageMaterial(
        kind="npc",
        resource_id=profile.npc_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _foreshadow_setting(
    store,
    args: ForeshadowSettingArguments,
    *,
    guidance: str | None,
) -> DirectorStageMaterial:
    setting = store.get_by_id(args.setting_id)
    if setting is None:
        raise ValueError("foreshadow_setting references an unknown setting")
    return DirectorStageMaterial(
        kind="setting",
        resource_id=setting.setting_id,
        guidance=_require_generation_guidance(guidance),
        payload=setting.director_view(),
    )


def _intrude_engaged_event(
    store,
    args: IntrudeEngagedEventArguments,
    *,
    guidance: str | None,
    touched_event_ids: set[str] | None = None,
) -> DirectorStageMaterial:
    event = store.get_by_id(args.event_id)
    if event is None:
        raise ValueError("intrude_engaged_event references an unknown event")
    touched_event_ids = touched_event_ids or set()
    engaged = event.stage == "engaged" or event.event_id in touched_event_ids
    if not engaged:
        raise ValueError(
            "intrude_engaged_event requires an engaged event (hidden and "
            "resolved events are not intrudable)"
        )
    payload = {
        **event.semantic_view(),
        "stage": "engaged",
    }
    return DirectorStageMaterial(
        kind="engaged_event",
        resource_id=event.event_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _require_generation_guidance(guidance: str | None) -> str:
    clean = str(guidance or "").strip()
    if not clean:
        raise ValueError("generation director skills require guidance")
    return clean


def _require_active_event(store, event_id: str) -> None:
    event = store.get_by_id(event_id)
    if event is None or event.stage == "resolved":
        raise ValueError("director skill requires an active event_id")


def _require_hidden_event(
    store,
    event_id: str,
    touched_event_ids: set[str] | None = None,
) -> None:
    event = store.get_by_id(event_id)
    if event is None or event.stage != "hidden":
        raise ValueError(
            "present_event requires a hidden event (engaged events are "
            "steered via intrude_engaged_event)"
        )
    touched_event_ids = touched_event_ids or set()
    if event.event_id in touched_event_ids:
        raise ValueError(
            "present_event is only for indirect contact; directly engaged "
            "events are steered via intrude_engaged_event"
        )


def _require_engaged_event(
    store,
    event_id: str,
    touched_event_ids: set[str] | None = None,
) -> None:
    event = store.get_by_id(event_id)
    if event is None:
        raise ValueError("intrude_engaged_event references an unknown event")
    touched_event_ids = touched_event_ids or set()
    if event.stage != "engaged" and event.event_id not in touched_event_ids:
        raise ValueError(
            "intrude_engaged_event requires an engaged event (hidden and "
            "resolved events are not intrudable)"
        )


def _require_npc(director_npc_store, npc_profile_store, npc_id: str) -> None:
    draft = director_npc_store.get_by_id(npc_id)
    if draft is not None and draft.status != "retired":
        return
    if npc_profile_store.get_by_id(npc_id) is None:
        raise ValueError("director skill references an unknown npc_id")


def _require_director_draft(director_npc_store, npc_id: str) -> None:
    draft = director_npc_store.get_by_id(npc_id)
    if draft is None or draft.status != "prepared":
        raise ValueError("introduce_npc requires a prepared director NPC draft")


def _require_disclosure_upgradeable(
    npc_profile_store,
    npc_id: str,
    new_level: str,
    *,
    visible_profiles: list[NPCProfile] | None = None,
) -> None:
    profile = npc_profile_store.get_by_id(npc_id)
    if profile is None:
        raise ValueError("upgrade_disclosure references an unknown npc_id")
    if visible_profiles is not None and npc_id not in {
        item.npc_id for item in visible_profiles
    }:
        raise ValueError(
            "upgrade_disclosure only works on NPCs projected to the "
            "generation director this turn (resources.npcs)"
        )
    if NPC_DISCLOSURE_ORDER[new_level] <= NPC_DISCLOSURE_ORDER[
        profile.disclosure_level
    ]:
        raise ValueError(
            "upgrade_disclosure only supports raising the disclosure level"
        )


def _require_setting(store, setting_id: str) -> None:
    setting = store.get_by_id(setting_id)
    if setting is None:
        raise ValueError("director skill references an unknown setting_id")


def _require_new_npc(npc_profile_store, new_npc_profiles, npc_id: str) -> None:
    candidates = [profile for profile in (new_npc_profiles or [])]
    if npc_id not in {profile.npc_id for profile in candidates}:
        raise ValueError("enrich_npc requires a newly created npc_id")
    profile = next(
        (item for item in candidates if item.npc_id == npc_id),
        npc_profile_store.get_by_id(npc_id),
    )
    if profile is None or profile.source_director_npc_id:
        raise ValueError("enrich_npc references an invalid npc_id")


def _require_event_actor_binding(
    event_store,
    director_npc_store,
    npc_profile_store,
    *,
    event_id: str,
    npc_id: str,
) -> None:
    _require_active_event(event_store, event_id)
    _require_npc(director_npc_store, npc_profile_store, npc_id)
