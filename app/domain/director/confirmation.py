"""Confirmation-stage Director skill execution（领域层）。

LangGraph 节点只调用 :class:`DirectorConfirmationExecutor`（或薄包装
``run_confirmation_director_agent``）。本模块拥有技能 handler 及其参数
validator、两波执行（Wave A 生成 Setting / Wave B 并发执行技能）以及延迟提交。
持久化细节由 :mod:`app.runtime.director_mutation` 负责。

并发与连接：Phase 2 起整个确认共享同一份操作级 Store（单一 SQLite 连接），
handler 只读、写入全部延迟到 :func:`apply_director_mutations`，因此 Wave B 的
并发 handler 只并发执行只读查询。
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
    register_skill,
)
from app.domain.director_event.schemas import (
    DirectorActorRef,
    utc_now,
)
from app.domain.director_event.selector import (
    DirectorEventSelector,
    event_eligible_setting_ids,
)
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.schemas import NPCCreationContext
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.selection import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_fact_texts,
)
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.creator import WorldSettingCreator
from app.domain.world_setting.schemas import WorldSetting, setting_appears_in_text
from app.runtime.director_mutation import (
    DirectorPendingActorBinding,
    DirectorPendingMutationPlan,
    apply_director_mutations,
)

DIRECTOR_WAVE_MAX_WORKERS = 3


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
        self._scene_text = scene_text
        self._director_log_entry = director_log_entry

        self._service = DirectorSkillService()
        self._plan = DirectorPendingMutationPlan()
        self._plan_lock = threading.Lock()
        self.last_warnings: list[DirectorWarning] = []
        # Phase 2 shares one connection per confirmation: handlers read through
        # the operation-level stores that also carry the commit. All handler
        # reads are plain SELECTs and may run concurrently; writes are deferred
        # to ``apply_director_mutations`` after every request succeeds.
        self._event_store: Any = stores.director_event
        self._director_npc_store: Any = stores.director_npc
        self._npc_profile_store: Any = stores.npc_profile
        self._setting_store: Any = stores.world_setting
        self._settings_by_id: dict[str, WorldSetting] = {}

    def run(self) -> tuple[object, DirectorSkillExecutionBatch]:
        self.last_warnings = []
        log_store = self._stores.director_log
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
        all_events = self._event_store.list_all()
        active_events = [
            event for event in all_events if event.stage != "resolved"
        ]
        eligible_event_setting_ids = event_eligible_setting_ids(
            available_settings,
            all_events,
        )

        self._register_skills(
            active_events=active_events,
            eligible_event_setting_ids=eligible_event_setting_ids,
        )

        observation = self._build_observation(
            active_events=active_events,
            available_settings=available_settings,
            eligible_event_setting_ids=eligible_event_setting_ids,
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
            eligible_event_setting_ids.update(
                setting.setting_id for setting in created_settings
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

            # Wave B may execute every request concurrently. Handlers share the
            # operation connection but issue only SELECTs; all writes remain in
            # the in-memory mutation plan until the batch succeeds.
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

        apply_director_mutations(self._plan, self._stores)
        return decision, executions

    def _register_skills(
        self,
        *,
        active_events,
        eligible_event_setting_ids: set[str],
    ) -> None:
        register_skill(
            self._service,
            "create_npc",
            handler=self._create_npc,
        )
        if not active_events:
            register_skill(
                self._service,
                "create_event",
                handler=self._create_event,
                arguments_validator=lambda args: _require_event_setting_available(
                    args,
                    known_setting_ids=set(self._settings_by_id),
                    eligible_setting_ids=eligible_event_setting_ids,
                ),
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

    def _build_observation(
        self,
        *,
        active_events,
        available_settings,
        eligible_event_setting_ids,
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
                    {
                        **setting.reference_view(),
                        "event_creation_available": (
                            setting.setting_id in eligible_event_setting_ids
                        ),
                    }
                    for setting in available_settings
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


def _require_event_setting_available(
    args: CreateEventArguments,
    *,
    known_setting_ids: set[str],
    eligible_setting_ids: set[str],
) -> None:
    setting_id = args.setting_requirements[0].matched_setting_id
    if setting_id is None:
        return
    if setting_id not in known_setting_ids:
        raise ValueError("create_event references an unknown Setting")
    if setting_id not in eligible_setting_ids:
        raise ValueError("create_event Setting has no unused facts")


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
