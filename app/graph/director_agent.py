from __future__ import annotations

import json

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
from app.domain.director_event.schemas import DirectorActorRef, utc_now
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_event.views import project_director_event
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.schemas import NPCCreationContext
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.workflows import (
    attach_transient_alias_for_rename,
    backfill_new_npc_profile,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.preparer import SettingAnchorPreparer

from .director_skill_specs import (
    BindEventActorArguments,
    CreateEventArguments,
    CreateNPCArguments,
    EnrichNPCArguments,
    EnrichSettingArguments,
    ForeshadowSettingArguments,
    IntroduceNPCArguments,
    NPC_VIEW_ALWAYS_KEPT,
    NPC_VIEW_MODE_FIELDS,
    PresentEventArguments,
    get_director_skill_specs,
)


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
    )


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
    pending_engagements: list[str] | None = None,
) -> tuple[DirectorStageResult, object]:
    service = DirectorSkillService()
    pending_engagements = pending_engagements or []
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        active_events = event_store.list_active()
        engaged_event_ids = set(pending_engagements)
        engaged_events = [
            event
            for event in active_events
            if event.event_id in engaged_event_ids or event.stage == "engaged"
        ]
        hidden_events = [
            event
            for event in active_events
            if event.event_id not in engaged_event_ids
            and event.stage != "engaged"
        ]
        director_npcs = [
            npc
            for npc in director_npc_store.list_all()
            if npc.status != "retired"
        ]
        npc_profiles = npc_profile_store.list_all()
        settings = setting_store.list_active()

        _register_skill(
            service,
            "present_event",
            handler=lambda args, guidance=None: _present_event(
                event_store,
                args,
                guidance=guidance,
                engaged_event_ids=engaged_event_ids,
            ),
            arguments_validator=lambda args: _require_active_event(
                event_store,
                args.event_id,
            ),
        )
        _register_skill(
            service,
            "introduce_npc",
            handler=lambda args, guidance=None: _introduce_npc(
                director_npc_store,
                npc_profile_store,
                args,
                guidance=guidance,
            ),
            arguments_validator=lambda args: _require_npc(
                director_npc_store,
                npc_profile_store,
                args.npc_id,
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
            arguments_validator=lambda args: _require_active_setting(
                setting_store,
                args.setting_id,
            ),
        )
        observation = DirectorObservation(
            phase="generation",
            turn_index=turn_index,
            player_input=user_input,
            core_memory=core_memory.model_dump(mode="json"),
            current_state=current_state.model_dump(mode="json"),
            context_facts=context_facts,
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
                ]
                + [
                    {
                        **event.semantic_view(),
                        "stage": "engaged",
                    }
                    for event in engaged_events
                ],
                "npcs": [
                    {
                        "npc_id": npc.draft_id,
                        "source": "director_npc",
                        **npc.private_view().model_dump(mode="json"),
                    }
                    for npc in director_npcs
                ]
                + [
                    {
                        "npc_id": profile.npc_id,
                        "source": "npc_profile",
                        **profile.director_view().model_dump(mode="json"),
                    }
                    for profile in npc_profiles
                ],
                "settings": [
                    _setting_resource(setting)
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
    entity_profiles: list,
    npc_profiles: list,
    entity_mentions: list[str],
    npc_mentions: list[str],
    turn_index: int,
    trace_id: str,
    new_npc_profiles: list[NPCProfile] | None = None,
) -> tuple[object, DirectorSkillExecutionBatch]:
    service = DirectorSkillService()
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        def create_npc(args: CreateNPCArguments, guidance: str | None = None):
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
                    anchors=args.anchors,
                ),
                creative_brief=draw_creative_brief(),
                protagonist_name=current_state.player.name,
                turn_index=turn_index,
                trace_id=trace_id,
                guidance=guidance,
            )
            director_npc_store.save(draft)
            return {"npc_id": draft.draft_id, "name": draft.name}

        def create_event(
            args: CreateEventArguments,
            guidance: str | None = None,
        ):
            event_guidance = args.event_guidance.strip() or (guidance or "")
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
                entity_profiles=entity_profiles,
                npc_profiles=npc_profiles,
                required_actors=required_actors,
                trace_id=trace_id,
                guidance=event_guidance,
            )
            event = selection.to_event(
                role=args.role,
                turn_index=turn_index,
                actors=bound_actors,
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
                    creation_context=NPCCreationContext(anchors=[]),
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
            for draft in drafts:
                director_npc_store.save(draft)
            event_store.save(event)
            return {
                "event_id": event.event_id,
                "role": event.role,
                "new_npc_ids": new_npc_ids,
                "actor_names": actor_names,
            }

        def enrich_setting(args: EnrichSettingArguments, guidance: str | None = None):
            setting = setting_store.get_by_id(args.setting_id)
            if (
                setting is None
                or setting.status != "active"
                or setting.maturity != "seed"
            ):
                raise ValueError(
                    "enrich_setting requires an active setting seed"
                )
            batch = SettingAnchorPreparer(gateway).prepare(
                core_memory=core_memory,
                settings=[setting],
                trace_id=trace_id,
                guidance=guidance,
            )
            preparation = next(
                (
                    item
                    for item in batch.preparations
                    if item.setting_id == setting.setting_id
                ),
                None,
            )
            if preparation is None:
                raise ValueError("setting preparation returned no matching result")
            if preparation.decision == "reject":
                setting_store.mark_rejected(
                    setting.setting_id,
                    reason=preparation.reason,
                )
                return {
                    "setting_id": setting.setting_id,
                    "status": "rejected",
                }
            if preparation.anchor is None:
                raise ValueError("usable setting preparation requires an anchor")
            setting_store.save_preparation(
                setting.setting_id,
                preparation.anchor,
            )
            return {
                "setting_id": setting.setting_id,
                "status": "usable",
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
            event = event_store.bind_actor(
                args.event_id,
                actor_name=actor_name,
                actor_ref=DirectorResourceRef(
                    kind="npc",
                    resource_id=args.npc_id,
                ),
                turn_index=turn_index,
            )
            return {"event_id": event.event_id, "npc_id": args.npc_id}

        def enrich_npc(args: EnrichNPCArguments, guidance: str | None = None):
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
                scene_facts=json.dumps(
                    scene_facts,
                    ensure_ascii=False,
                    indent=2,
                ),
                current_state=current_state,
                core_memory=core_memory,
                gateway=gateway,
                npc_store=npc_profile_store,
                recent_summaries=recent_summaries,
                trace_id=trace_id,
                guidance=guidance,
            )
            if backfilled.name != original.name:
                attach_transient_alias_for_rename(
                    backfilled,
                    old_name=original.name,
                    turn_index=turn_index,
                )
            saved_id = npc_profile_store.upsert(backfilled)
            return {
                "npc_id": saved_id,
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
            "enrich_setting",
            handler=enrich_setting,
            arguments_validator=lambda args: _require_setting_seed(
                setting_store,
                args.setting_id,
            ),
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

        observation = DirectorObservation(
            phase="confirmation",
            turn_index=turn_index,
            player_input=user_input,
            core_memory=core_memory.model_dump(mode="json"),
            current_state=current_state.model_dump(mode="json"),
            scene_facts=scene_facts,
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
                    for event in event_store.list_all()
                    if event.stage != "resolved"
                ],
                "npcs": [
                    {
                        "npc_id": npc.draft_id,
                        "source": "director_npc",
                        **npc.private_view().model_dump(mode="json"),
                    }
                    for npc in director_npc_store.list_all()
                    if npc.status != "retired"
                ]
                + [
                    {
                        "npc_id": profile.npc_id,
                        "source": "npc_profile",
                        **profile.director_view().model_dump(mode="json"),
                    }
                    for profile in npc_profile_store.list_all()
                ],
                "settings": [
                    _setting_resource(setting)
                    for setting in setting_store.list_active()
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
        executions = service.execute_batch(
            decision,
            phase="confirmation",
        )
    return decision, executions


def _present_event(
    store,
    args: PresentEventArguments,
    *,
    guidance: str | None,
    engaged_event_ids: set[str] | None = None,
) -> DirectorStageMaterial:
    event = store.get_by_id(args.event_id)
    if event is None or event.stage == "resolved":
        raise ValueError("present_event requires an active event")
    engaged_event_ids = engaged_event_ids or set()
    is_engaged = event.stage == "engaged" or event.event_id in engaged_event_ids
    if not is_engaged and args.hook_id:
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
    elif is_engaged:
        payload = json.loads(
            project_director_event(
                event,
                effective_stage="engaged",
            )
        )
    else:
        payload = {
            "event_id": event.event_id,
            "stage": "hidden",
        }
    return DirectorStageMaterial(
        kind="event",
        resource_id=event.event_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _introduce_npc(
    director_npc_store,
    npc_profile_store,
    args: IntroduceNPCArguments,
    *,
    guidance: str | None,
) -> DirectorStageMaterial:
    draft = director_npc_store.get_by_id(args.npc_id)
    if draft is not None and draft.status != "retired":
        payload = {
            "source": "director_npc",
            **draft.private_view().model_dump(
                mode="json",
                exclude_none=True,
            ),
        }
    else:
        profile = npc_profile_store.get_by_id(args.npc_id)
        if profile is None:
            raise ValueError("introduce_npc references an unknown NPC")
        payload = {
            "source": "npc_profile",
            "npc_id": profile.npc_id,
            **profile.director_view().model_dump(
                mode="json",
                exclude_none=True,
            ),
        }
    allowed = NPC_VIEW_MODE_FIELDS[args.view_mode]
    if allowed is not None:
        payload = {
            key: value
            for key, value in payload.items()
            if key in allowed or key in NPC_VIEW_ALWAYS_KEPT
        }
    return DirectorStageMaterial(
        kind="npc",
        resource_id=args.npc_id,
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
    if setting is None or setting.status != "active":
        raise ValueError("foreshadow_setting requires an active setting")
    return DirectorStageMaterial(
        kind="setting",
        resource_id=setting.setting_id,
        guidance=_require_generation_guidance(guidance),
        payload=_setting_resource(setting),
    )


def _require_generation_guidance(guidance: str | None) -> str:
    clean = str(guidance or "").strip()
    if not clean:
        raise ValueError("generation director skills require guidance")
    return clean


def _setting_resource(setting) -> dict:
    return {
        "setting_id": setting.setting_id,
        "label": setting.label,
        "kind": setting.kind,
        "basic_concept": setting.basic_concept,
        "maturity": setting.maturity,
        "prepared_anchor": (
            setting.prepared_anchor.model_dump(mode="json")
            if setting.prepared_anchor is not None
            else None
        ),
    }


def _require_active_event(store, event_id: str) -> None:
    event = store.get_by_id(event_id)
    if event is None or event.stage == "resolved":
        raise ValueError("director skill requires an active event_id")


def _require_npc(director_npc_store, npc_profile_store, npc_id: str) -> None:
    draft = director_npc_store.get_by_id(npc_id)
    if draft is not None and draft.status != "retired":
        return
    if npc_profile_store.get_by_id(npc_id) is None:
        raise ValueError("director skill references an unknown npc_id")


def _require_active_setting(store, setting_id: str) -> None:
    setting = store.get_by_id(setting_id)
    if setting is None or setting.status != "active":
        raise ValueError("director skill requires an active setting_id")


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


def _require_setting_seed(store, setting_id: str) -> None:
    setting = store.get_by_id(setting_id)
    if (
        setting is None
        or setting.status != "active"
        or setting.maturity != "seed"
    ):
        raise ValueError("enrich_setting requires a setting seed")


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
