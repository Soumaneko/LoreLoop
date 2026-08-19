"""Confirmation-stage Director skill execution（领域层）。

LangGraph 节点只调用 :class:`DirectorConfirmationExecutor`（或薄包装
``run_confirmation_director_agent``）。本模块拥有技能 handler 及其参数
validator、两波执行（Wave A 生成 Setting / Wave B 并发执行技能）以及跨库
延迟提交。持久化细节由 :mod:`app.runtime.director_mutation` 负责。

并发与连接：每个技能操作通过 ``_FreshStoreProxy`` 各开一条 SQLite 连接，
因此在 :meth:`DirectorConfirmationExecutor.run` 开头一次性创建代理并存入
实例属性、此后不再重绑定——handler 始终从 ``self._*_store`` 读取，不依赖
闭包晚绑定语义。
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.domain.core_memory.schemas import CoreMemory
from app.domain.creative_brief import draw_creative_brief
from app.domain.current_state.schemas import CurrentState
from app.domain.director import (
    DirectorAgent,
    DirectorDecisionBatch,
    DirectorObservation,
    DirectorResourceRef,
    DirectorSkillExecutionBatch,
    DirectorSkillService,
)
from app.domain.director.agent import DirectorWarning
from app.domain.director.skill_specs import (
    BindEventActorArguments,
    CreateEventArguments,
    CreateNPCArguments,
    EnrichNPCArguments,
    register_skill,
)
from app.domain.director_event.schemas import (
    DirectorActorRef,
    utc_now,
)
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.schemas import NPCCreationContext
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.selection import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_fact_texts,
)
from app.domain.npc_profile.workflows import (
    attach_transient_alias_for_rename,
    backfill_new_npc_profile,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.creator import WorldSettingCreator
from app.domain.world_setting.schemas import WorldSetting, setting_appears_in_text
from app.runtime.director_mutation import (
    DirectorPendingActorBinding,
    DirectorPendingMutationPlan,
    commit_director_mutations,
    recover_pending_director_mutation,
)

DIRECTOR_WAVE_MAX_WORKERS = 3


class _FreshStoreProxy:
    """Give each concurrent skill operation its own SQLite connection."""

    def __init__(self, factory):
        self._factory = factory

    def __getattr__(self, name):
        def invoke(*args, **kwargs):
            with self._factory() as store:
                return getattr(store, name)(*args, **kwargs)

        return invoke


class DirectorConfirmationExecutor:
    """Executes one confirmation Director turn end to end.

    Handlers never write to stores: every intended mutation lands in
    ``self._plan`` and is committed as one recoverable batch only after every
    request has succeeded.
    """

    def __init__(
        self,
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
        npc_candidate_context: dict[str, dict] | None = None,
        scene_text: str = "",
        director_log_entry: dict | None = None,
    ):
        self._gateway = gateway
        self._stores = stores
        self._core_memory = core_memory
        self._current_state = current_state
        self._user_input = user_input
        self._scene_facts = scene_facts
        self._recent_summaries = recent_summaries
        self._selection_summaries = selection_summaries
        self._turn_index = turn_index
        self._trace_id = trace_id
        self._new_npc_profiles = new_npc_profiles or []
        self._npc_candidate_context = npc_candidate_context or {}
        self._scene_text = scene_text
        self._director_log_entry = director_log_entry

        self._service = DirectorSkillService()
        self._plan = DirectorPendingMutationPlan()
        self._plan_lock = threading.Lock()
        self.last_warnings: list[DirectorWarning] = []
        # Fresh proxies created once: every operation opens its own SQLite
        # connection, so Wave B may run handlers concurrently without sharing a
        # connection. Never rebound afterwards.
        self._event_store: Any = _FreshStoreProxy(stores.director_event)
        self._director_npc_store: Any = _FreshStoreProxy(stores.director_npc)
        self._npc_profile_store: Any = _FreshStoreProxy(stores.npc_profile)
        self._setting_store: Any = _FreshStoreProxy(stores.world_setting)
        self._settings_by_id: dict[str, WorldSetting] = {}

    def run(self) -> tuple[object, DirectorSkillExecutionBatch]:
        self.last_warnings = []
        recover_pending_director_mutation(self._stores)
        with self._stores.director_log() as log_store:
            recent_director_log = log_store.recent()
        if self._director_log_entry:
            recent_director_log = [
                self._director_log_entry,
                *recent_director_log,
            ][:6]

        available_settings = self._setting_store.list_all()
        self._settings_by_id = {
            setting.setting_id: setting for setting in available_settings
        }

        self._register_skills()

        observation = self._build_observation(
            active_events=[
                event
                for event in self._event_store.list_all()
                if event.stage != "resolved"
            ],
            available_settings=available_settings,
            recent_director_log=recent_director_log,
        )
        try:
            agent = DirectorAgent(self._gateway, self._service)
            decision = agent.decide_confirmation(
                observation,
                trace_id=self._trace_id,
            )
            self.last_warnings.extend(agent.last_warnings)
            self._service.validate_batch(decision, phase="confirmation")
            if not decision.requests:
                return decision, DirectorSkillExecutionBatch()

            decision, created_settings = self._run_setting_creation_wave(
                decision,
                available_settings=available_settings,
            )
            # A Setting created in this same confirmation may already appear in
            # the confirmed scene text; the appearance node runs before this
            # stage, so its first mention would otherwise never be counted and
            # the record stays hidden from the general archive view.
            folded_text = self._scene_text.casefold()
            for setting in created_settings:
                if setting_appears_in_text(setting.name, folded_text):
                    setting.hit_count = 1
            self._plan.settings.extend(created_settings)
            for setting in created_settings:
                self._settings_by_id[setting.setting_id] = setting

            # Wave B may execute every request concurrently. Each proxy opens a
            # dedicated SQLite connection per operation; semantic conflicts are
            # prevented by the upstream Director contract.
            executions = self._service.execute_batch(
                decision,
                phase="confirmation",
                max_workers=DIRECTOR_WAVE_MAX_WORKERS,
            )
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            self.last_warnings.append(DirectorWarning(
                code="director_execution_fallback",
                message=(
                    "Director execution failed; no mutations applied: "
                    f"{exc}"
                ),
            ))
            return (
                DirectorDecisionBatch(
                    reasoning="Director execution fallback: no mutations applied.",
                    requests=[],
                ),
                DirectorSkillExecutionBatch(),
            )

        commit_director_mutations(self._plan, self._stores)
        return decision, executions

    def _register_skills(self) -> None:
        register_skill(
            self._service,
            "create_npc",
            handler=self._create_npc,
        )
        register_skill(
            self._service,
            "create_event",
            handler=self._create_event,
        )
        register_skill(
            self._service,
            "bind_event_actor",
            handler=self._bind_actor,
            arguments_validator=lambda args: _require_event_actor_binding(
                self._event_store,
                self._director_npc_store,
                self._npc_profile_store,
                event_id=args.event_id,
                npc_id=args.npc_id,
            ),
        )
        if self._new_npc_profiles:
            register_skill(
                self._service,
                "enrich_npc",
                handler=self._enrich_npc,
                arguments_validator=lambda args: _get_new_npc(
                    self._npc_profile_store,
                    self._new_npc_profiles,
                    args.npc_id,
                ),
                batch_validator=_validate_unique_enrich_npc_targets,
            )

    def _dispatch_setting_requirements(self, requirements) -> list:
        """Resolve the requirement from available + Wave-A in-memory Settings."""
        resolved: list = []
        for requirement in requirements:
            if requirement.matched_setting_id is not None:
                setting = self._settings_by_id.get(
                    requirement.matched_setting_id
                )
                if setting is not None and setting.kind == requirement.kind:
                    resolved.append(setting)
                    continue
            raise ValueError(
                "confirmation skill reached Wave B without a valid Setting"
            )
        return resolved

    def _create_npc(self, args: CreateNPCArguments, guidance: str | None = None):
        settings = self._dispatch_setting_requirements(args.setting_requirements)
        setting_ids = [setting.setting_id for setting in settings]
        draft = DirectorNPCCreator(self._gateway).create(
            core_memory=self._core_memory,
            existing_npcs=self._npc_profile_store.list_all(),
            reserved_npcs=self._reserved_npc_names([]),
            creation_context=NPCCreationContext(
                setting_ids=setting_ids,
            ),
            settings=settings,
            creative_brief=draw_creative_brief(),
            protagonist_name=self._current_state.player.name,
            turn_index=self._turn_index,
            trace_id=self._trace_id,
            guidance=guidance,
        )
        with self._plan_lock:
            self._plan.npc_drafts.append(draft)
        return {"npc_id": draft.draft_id, "name": draft.name}

    def _create_event(
        self,
        args: CreateEventArguments,
        guidance: str | None = None,
    ):
        settings = self._dispatch_setting_requirements(args.setting_requirements)
        setting_ids = [setting.setting_id for setting in settings]
        event_guidance = guidance or ""
        required_actors, bound_actors, actor_inputs_by_key = (
            self._resolve_event_actors(args.actors)
        )
        selection = DirectorEventSelector(self._gateway).select_or_create(
            role=args.role,
            events=self._event_store.list_all(),
            current_state=self._current_state,
            core_memory=self._core_memory,
            recent_summaries=self._selection_summaries,
            settings=settings,
            required_actors=required_actors,
            trace_id=self._trace_id,
            guidance=event_guidance,
        )
        event = selection.to_event(
            role=args.role,
            turn_index=self._turn_index,
            actors=bound_actors,
            current_clock=self._current_state.world_clock,
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
            draft = DirectorNPCCreator(self._gateway).create(
                core_memory=self._core_memory,
                existing_npcs=self._npc_profile_store.list_all(),
                reserved_npcs=self._reserved_npc_names(drafts),
                creation_context=NPCCreationContext(
                    setting_ids=setting_ids
                ),
                settings=settings,
                creative_brief=draw_creative_brief(),
                protagonist_name=self._current_state.player.name,
                turn_index=self._turn_index,
                trace_id=self._trace_id,
                guidance=npc_guidance,
                recent_creations=recent_creations,
            )
            drafts.append(draft)
            actor_ref = DirectorResourceRef(
                kind="npc",
                resource_id=draft.draft_id,
            )
            event = self._attach_new_actor_ref(event, draft, actor_ref)
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
        with self._plan_lock:
            self._plan.events.append(event)
            self._plan.npc_drafts.extend(drafts)
        return {
            "event_id": event.event_id,
            "role": event.role,
            "new_npc_ids": new_npc_ids,
            "actor_names": actor_names,
        }

    def _resolve_event_actors(self, actor_inputs):
        """Resolve every requested actor into Selector inputs and bound refs.

        ``source=new`` actors enter the Selector as guidance only; existing
        actors resolve against the Director NPC and profile stores and are
        bound to the event immediately.
        """
        required_actors: list[dict] = []
        bound_actors: list[DirectorActorRef] = []
        actor_inputs_by_key = {}
        for index, actor_input in enumerate(actor_inputs, start=1):
            actor_key = f"actor_{index}"
            actor_inputs_by_key[actor_key] = actor_input
            if actor_input.source == "new":
                required_actors.append({
                    "actor_key": actor_key,
                    "source": "new",
                    "guidance": actor_input.guidance,
                })
                continue

            draft = self._director_npc_store.get_by_id(actor_input.npc_id)
            profile = self._npc_profile_store.get_by_id(actor_input.npc_id)
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
        return required_actors, bound_actors, actor_inputs_by_key

    def _reserved_npc_names(self, staged_drafts):
        """Names reserved against new-NPC creation for name deduplication."""
        return [
            {
                "name": npc.name,
                "role": npc.role,
                "core_motivation": npc.core_motivation,
                "hidden_secret": npc.hidden_secret,
            }
            for npc in self._director_npc_store.list_all()
            if npc.status != "retired"
        ] + [
            {
                "name": staged.name,
                "role": staged.role,
                "core_motivation": staged.core_motivation,
                "hidden_secret": staged.hidden_secret,
            }
            for staged in staged_drafts
        ]

    def _attach_new_actor_ref(self, event, draft, actor_ref):
        """Bind a freshly created NPC to the event, replacing a name-only actor."""
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
        return event.model_copy(
            update={
                "actors": actors,
                "updated_turn": self._turn_index,
                "updated_at": utc_now(),
            }
        )

    def _bind_actor(
        self,
        args: BindEventActorArguments,
        guidance: str | None = None,
    ):
        draft = self._director_npc_store.get_by_id(args.npc_id)
        profile = self._npc_profile_store.get_by_id(args.npc_id)
        if draft is not None and draft.status != "retired":
            actor_name = draft.name
        elif profile is not None:
            actor_name = profile.name
        else:
            raise ValueError("bind_event_actor references an unknown NPC")
        with self._plan_lock:
            self._plan.actor_bindings.append(
                DirectorPendingActorBinding(
                    event_id=args.event_id,
                    actor_name=actor_name,
                    actor_ref=DirectorResourceRef(
                        kind="npc",
                        resource_id=args.npc_id,
                    ),
                    turn_index=self._turn_index,
                )
            )
        return {"event_id": args.event_id, "npc_id": args.npc_id}

    def _enrich_npc(self, args: EnrichNPCArguments, guidance: str | None = None):
        original = _get_new_npc(
            self._npc_profile_store,
            self._new_npc_profiles,
            args.npc_id,
        )
        settings = self._dispatch_setting_requirements(args.setting_requirements)
        setting_ids = [setting.setting_id for setting in settings]
        backfilled = backfill_new_npc_profile(
            profile=original,
            existing_profiles=[
                npc
                for npc in self._npc_profile_store.list_all()
                if npc.npc_id != args.npc_id
            ],
            core_memory=self._core_memory,
            gateway=self._gateway,
            npc_store=self._npc_profile_store,
            settings=settings,
            trace_id=self._trace_id,
            guidance=guidance,
        )
        backfilled.source_setting_ids = list(setting_ids)
        if backfilled.name != original.name:
            attach_transient_alias_for_rename(
                backfilled,
                old_name=original.name,
                turn_index=self._turn_index,
            )
        with self._plan_lock:
            self._plan.profile_upserts.append(backfilled)
        return {
            "npc_id": backfilled.npc_id,
            "name": backfilled.name,
        }

    def _build_observation(
        self,
        *,
        active_events,
        available_settings,
        recent_director_log,
    ) -> DirectorObservation:
        scene_fact_texts = (
            [str(self._scene_facts.get("scene_focus") or "")]
            + [
                str(fact.get("fact") or "")
                for fact in self._scene_facts.get("facts", [])
            ]
            if isinstance(self._scene_facts, dict)
            else []
        )
        npc_detail_profiles = merge_profile_selections(
            select_profiles_by_fact_texts(
                texts=scene_fact_texts,
                npc_store=self._npc_profile_store,
            ),
            select_profiles_by_active_events(
                events=active_events,
                npc_store=self._npc_profile_store,
            ),
            limit=8,
        )
        return DirectorObservation(
            phase="confirmation",
            turn_index=self._turn_index,
            player_input=self._user_input,
            core_memory=self._core_memory.model_dump(mode="json"),
            current_state=self._current_state.model_dump(mode="json"),
            scene_facts=self._scene_facts,
            recent_director_log=recent_director_log,
            recent_summaries=[
                summary.model_dump(mode="json")
                for summary in self._recent_summaries
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
                        "last_scene_summary": profile.last_scene_summary,
                    }
                    for profile in self._npc_profile_store.list_all()
                ],
                "drafts": [
                    {
                        "npc_id": npc.draft_id,
                        "source": "director_npc",
                        **npc.private_view().model_dump(mode="json"),
                    }
                    for npc in self._director_npc_store.list_all()
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
                    for setting in available_settings
                ],
                "new_npcs": [
                    {
                        "npc_id": profile.npc_id,
                        **profile.director_view().model_dump(mode="json"),
                        **self._npc_candidate_context.get(profile.npc_id, {}),
                    }
                    for profile in self._new_npc_profiles
                ],
            },
        )

    def _run_setting_creation_wave(
        self,
        decision,
        *,
        available_settings,
    ):
        """Create one missing Setting per unique requirement in memory.

        Requests that declare the identical missing requirement (same ``kind``
        and ``setting_description``) share a single generated Setting instead of
        producing duplicate assets. Nothing is persisted here; the caller
        commits the generated Settings only after the whole confirmation batch
        succeeds.
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
                max_workers=min(
                    DIRECTOR_WAVE_MAX_WORKERS, len(missing_groups)
                ),
                thread_name_prefix="director-setting",
            ) as executor:
                futures = {
                    executor.submit(
                        _create_missing_setting,
                        group[0][1],
                        gateway=self._gateway,
                        core_memory=self._core_memory,
                        available_settings=available_settings,
                        turn_index=self._turn_index,
                        trace_id=self._trace_id,
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
    npc_candidate_context: dict[str, dict] | None = None,
    scene_text: str = "",
    director_log_entry: dict | None = None,
) -> tuple[object, DirectorSkillExecutionBatch]:
    """Thin wrapper kept for call-site compatibility."""
    return DirectorConfirmationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=core_memory,
        current_state=current_state,
        user_input=user_input,
        scene_facts=scene_facts,
        recent_summaries=recent_summaries,
        selection_summaries=selection_summaries,
        turn_index=turn_index,
        trace_id=trace_id,
        new_npc_profiles=new_npc_profiles,
        npc_candidate_context=npc_candidate_context,
        scene_text=scene_text,
        director_log_entry=director_log_entry,
    ).run()


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


def _get_new_npc(npc_profile_store, new_npc_profiles, npc_id: str):
    candidates = [profile for profile in (new_npc_profiles or [])]
    if npc_id not in {profile.npc_id for profile in candidates}:
        raise ValueError("enrich_npc requires a newly created npc_id")
    profile = next(
        (item for item in candidates if item.npc_id == npc_id),
        npc_profile_store.get_by_id(npc_id),
    )
    if profile is None or profile.source_director_npc_id:
        raise ValueError("enrich_npc references an invalid npc_id")
    return profile


def _validate_unique_enrich_npc_targets(arguments) -> None:
    """Reject duplicate enrich_npc targets within one decision batch.

    Wave B executes requests concurrently, so enriching the same new NPC twice
    would produce several backfilled variants that overwrite each other in the
    mutation plan and may leave orphaned Settings behind.
    """
    npc_ids = [args.npc_id for args in arguments]
    if len(npc_ids) != len(set(npc_ids)):
        raise ValueError(
            "enrich_npc can only target each new NPC once per batch"
        )


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
