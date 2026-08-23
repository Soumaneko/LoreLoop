"""Generation-stage Director skill execution（领域层）。

LangGraph 节点只调用 :class:`DirectorGenerationExecutor`（或薄包装
``run_generation_director_agent``）。技能 handler 与其参数 validator 均
在本模块，而不是编排层。生成阶段技能为只读：只产出
:class:`DirectorStageMaterial`，不触碰任何持久状态。
"""

from __future__ import annotations

import json
import uuid

from app.core.text import normalize_compact_key
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director import (
    DirectorAgent,
    DirectorObservation,
    DirectorSkillService,
    DirectorStageMaterial,
    DirectorStageResult,
)
from app.domain.director.agent import DirectorWarning
from app.domain.director.skill_specs import (
    IntroduceNPCArguments,
    IntrudeEngagedEventArguments,
    NPC_VIEW_ALWAYS_KEPT,
    PresentEventArguments,
    UpgradeDisclosureArguments,
    register_skill,
)
from app.domain.npc_profile.schemas import (
    DISCLOSED_FIELD_SETS,
    NPC_DISCLOSURE_ORDER,
    NPCProfile,
)
from app.domain.scene_summary.schemas import SceneSummary


class DirectorGenerationExecutor:
    """Executes the generation Director turn: observation, decision, execution.

    Handlers never write to stores; the result is a read-only
    :class:`DirectorStageResult` admitted to composition.
    """

    def __init__(
        self,
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
    ):
        self._gateway = gateway
        self._stores = stores
        self._core_memory = core_memory
        self._current_state = current_state
        self._user_input = user_input
        self._context_facts = context_facts
        self._recent_summaries = recent_summaries
        self._turn_index = turn_index
        self._trace_id = trace_id
        self._turn_resolution_json = turn_resolution_json
        self._touched_event_candidates = touched_event_candidates or []
        self._npc_profiles = npc_profiles
        self.last_warnings: list[DirectorWarning] = []

    def run(self) -> tuple[DirectorStageResult, object]:
        service = DirectorSkillService()
        touched_event_ids = set(self._touched_event_candidates)
        # The stores object is owned by the caller (an operation UoW or test
        # fixture); do not enter/exit it here because context exit now closes
        # the UoW permanently.
        uow = self._stores
        event_store = uow.director_event
        director_npc_store = uow.director_npc
        npc_profile_store = uow.npc_profile
        active_events = event_store.list_active()
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
        selected_npc_profiles = self._npc_profiles
        log_store = uow.director_log
        recent_director_log = log_store.recent()

        if hidden_events:
            register_skill(
                service,
                "present_event",
                handler=lambda args, guidance=None: _present_event(
                    event_store,
                    args,
                    guidance=guidance,
                    touched_event_ids=touched_event_ids,
                ),
                arguments_validator=lambda args: _get_hidden_event(
                    event_store,
                    args.event_id,
                    touched_event_ids=touched_event_ids,
                ),
            )
        player_name = (
            self._current_state.player.name
            if self._current_state and self._current_state.player
            else None
        )
        register_skill(
            service,
            "introduce_npc",
            handler=lambda args, guidance=None: _introduce_npc(
                director_npc_store,
                args,
                guidance=guidance,
            ),
            arguments_validator=lambda args: _validate_introduce_npc(
                args,
                director_npc_store,
                npc_profile_store,
                player_name=player_name,
            ),
            batch_validator=_validate_unique_introduce_npc_targets,
        )
        if selected_npc_profiles:
            register_skill(
                service,
                "upgrade_disclosure",
                handler=lambda args, guidance=None: _upgrade_disclosure(
                    npc_profile_store,
                    args,
                    guidance=guidance,
                    visible_profiles=selected_npc_profiles,
                ),
                arguments_validator=lambda args: _get_disclosure_profile(
                    npc_profile_store,
                    args.npc_id,
                    args.new_disclosure_level,
                    visible_profiles=selected_npc_profiles,
                ),
            )
        if engaged_events:
            register_skill(
                service,
                "intrude_engaged_event",
                handler=lambda args, guidance=None: _intrude_engaged_event(
                    event_store,
                    args,
                    guidance=guidance,
                    touched_event_ids=touched_event_ids,
                ),
                arguments_validator=lambda args: _get_engaged_event(
                    event_store,
                    args.event_id,
                    touched_event_ids=touched_event_ids,
                ),
            )
        observation = DirectorObservation(
            phase="generation",
            turn_index=self._turn_index,
            player_input=self._user_input,
            core_memory=self._core_memory.model_dump(mode="json"),
            current_state=self._current_state.model_dump(mode="json"),
            context_facts=self._context_facts,
            recent_director_log=recent_director_log,
            recent_summaries=[
                summary.model_dump(mode="json")
                for summary in self._recent_summaries
            ],
            turn_resolution=(
                json.loads(self._turn_resolution_json)
                if self._turn_resolution_json
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
            },
        )
        agent = DirectorAgent(self._gateway, service)
        decision = agent.decide_generation(
            observation,
            trace_id=self._trace_id,
        )
        self.last_warnings.extend(agent.last_warnings)
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
    """Thin wrapper kept for call-site compatibility."""
    return DirectorGenerationExecutor(
        gateway=gateway,
        stores=stores,
        core_memory=core_memory,
        current_state=current_state,
        user_input=user_input,
        context_facts=context_facts,
        recent_summaries=recent_summaries,
        turn_index=turn_index,
        trace_id=trace_id,
        turn_resolution_json=turn_resolution_json,
        touched_event_candidates=touched_event_candidates,
        npc_profiles=npc_profiles,
    ).run()


def _present_event(
    store,
    args: PresentEventArguments,
    *,
    guidance: str | None,
    touched_event_ids: set[str] | None = None,
) -> DirectorStageMaterial:
    event = _get_hidden_event(
        store, args.event_id, touched_event_ids=touched_event_ids
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
    clean_id = args.npc_id.strip() if args.npc_id and args.npc_id.strip() else None
    if clean_id:
        draft = _get_director_draft(director_npc_store, clean_id)
        payload = {
            "source": "director_npc",
            **draft.private_view().model_dump(
                mode="json",
                exclude_none=True,
            ),
        }
        resource_id = clean_id
    elif args.spontaneous:
        spontaneous = args.spontaneous
        resource_id = f"spontaneous_npc_{uuid.uuid4().hex[:12]}"
        payload = {
            "npc_id": resource_id,
            "source": "spontaneous",
            "name": spontaneous.name.strip(),
            "role": spontaneous.role.strip(),
            "appearance": spontaneous.appearance.strip(),
            "personality_trait": spontaneous.personality_trait.strip(),
            "known_abilities": [
                ability.model_dump(mode="json")
                for ability in spontaneous.known_abilities
            ],
            "faction": "",
            "disclosure_level": args.view_mode,
        }
    else:
        raise ValueError("introduce_npc requires either npc_id or spontaneous")

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
        resource_id=resource_id,
        guidance=_require_generation_guidance(guidance),
        payload=payload,
    )


def _upgrade_disclosure(
    npc_profile_store,
    args: UpgradeDisclosureArguments,
    *,
    guidance: str | None,
    visible_profiles: list[NPCProfile] | None = None,
) -> DirectorStageMaterial:
    profile = _get_disclosure_profile(
        npc_profile_store,
        args.npc_id,
        args.new_disclosure_level,
        visible_profiles=visible_profiles,
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


def _intrude_engaged_event(
    store,
    args: IntrudeEngagedEventArguments,
    *,
    guidance: str | None,
    touched_event_ids: set[str] | None = None,
) -> DirectorStageMaterial:
    event = _get_engaged_event(
        store, args.event_id, touched_event_ids=touched_event_ids
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


def _get_hidden_event(
    store,
    event_id: str,
    touched_event_ids: set[str] | None = None,
):
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
    return event


def _get_engaged_event(
    store,
    event_id: str,
    touched_event_ids: set[str] | None = None,
):
    event = store.get_by_id(event_id)
    if event is None:
        raise ValueError("intrude_engaged_event references an unknown event")
    touched_event_ids = touched_event_ids or set()
    if event.stage != "engaged" and event.event_id not in touched_event_ids:
        raise ValueError(
            "intrude_engaged_event requires an engaged event (hidden and "
            "resolved events are not intrudable)"
        )
    return event


def _validate_unique_introduce_npc_targets(arguments: list[IntroduceNPCArguments]) -> None:
    seen_ids: set[str] = set()
    seen_spontaneous_names: set[str] = set()
    for args in arguments:
        if args.npc_id and args.npc_id.strip():
            clean_id = args.npc_id.strip()
            if clean_id in seen_ids:
                raise ValueError(
                    f"introduce_npc draft target '{clean_id}' is duplicated in the same batch"
                )
            seen_ids.add(clean_id)
        if args.spontaneous:
            name = args.spontaneous.name.strip()
            compact = normalize_compact_key(name)
            if compact in seen_spontaneous_names:
                raise ValueError(
                    f"spontaneous NPC name '{name}' is duplicated within the same decision batch"
                )
            seen_spontaneous_names.add(compact)


def _validate_introduce_npc(
    args: IntroduceNPCArguments,
    director_npc_store,
    npc_profile_store,
    *,
    player_name: str | None = None,
):
    clean_id = args.npc_id.strip() if args.npc_id and args.npc_id.strip() else None
    if clean_id:
        return _get_director_draft(director_npc_store, clean_id)
    if args.spontaneous:
        name = args.spontaneous.name.strip()
        if not name:
            raise ValueError("spontaneous NPC requires a non-empty name")
        compact_name = normalize_compact_key(name)
        if player_name:
            compact_player = normalize_compact_key(player_name)
            if compact_name == compact_player:
                raise ValueError(
                    f"spontaneous NPC name '{name}' conflicts with the protagonist name"
                )
        for p in npc_profile_store.list_all():
            if normalize_compact_key(p.name) == compact_name:
                raise ValueError(
                    f"spontaneous NPC name '{name}' conflicts with an existing NPC profile"
                )
            alias_keys = {
                normalize_compact_key(a)
                for a in [*p.aliases, *p.transient_aliases.keys()]
            }
            if compact_name in alias_keys:
                raise ValueError(
                    f"spontaneous NPC name '{name}' conflicts with an existing NPC alias of '{p.name}'"
                )
        if any(
            normalize_compact_key(d.name) == compact_name
            for d in director_npc_store.list_all()
            if d.status == "prepared"
        ):
            raise ValueError(
                f"spontaneous NPC name '{name}' conflicts with an existing prepared draft"
            )
        return args.spontaneous
    raise ValueError("introduce_npc requires either npc_id or spontaneous")


def _get_director_draft(director_npc_store, npc_id: str):
    draft = director_npc_store.get_by_id(npc_id)
    if draft is None or draft.status != "prepared":
        raise ValueError("introduce_npc requires a prepared director NPC draft")
    return draft


def _get_disclosure_profile(
    npc_profile_store,
    npc_id: str,
    new_level: str,
    *,
    visible_profiles: list[NPCProfile] | None = None,
):
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
    return profile
