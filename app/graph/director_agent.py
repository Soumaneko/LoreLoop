from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field

from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director import (
    DirectorAgent,
    DirectorObservation,
    DirectorResourceRef,
    DirectorSkillExecutionBatch,
    DirectorSkillService,
    DirectorStageMaterial,
    DirectorStageResult,
)
from app.domain.director_event.intervention_planner import (
    DirectorEventInterventionPlanner,
)
from app.domain.director_event.intervention_reviewer import (
    DirectorEventInterventionReviewer,
)
from app.domain.director_event.reviewer import DirectorEventReviewer
from app.domain.director_event.schemas import DirectorEventRole
from app.domain.director_event.selector import DirectorEventSelector
from app.domain.director_event.views import project_director_event
from app.domain.director_event.workflows import review_director_event_workflow
from app.domain.director_npc.creator import DirectorNPCCreator
from app.domain.director_npc.creative_brief import (
    draw_director_npc_creative_brief,
)
from app.domain.director_npc.schemas import NPCCreationContext
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.world_setting.preparer import SettingAnchorPreparer


class _PresentEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)


class _IntroduceNPCArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    npc_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)


class _ForeshadowSettingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=300)


class _ReviewEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)


class _CreateEventArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: DirectorEventRole


class _CreateNPCArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str = Field(min_length=1, max_length=300)
    anchors: list[str] = Field(default_factory=list, max_length=8)


class _EnrichSettingArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    setting_id: str = Field(min_length=1)


class _BindEventActorArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    npc_id: str = Field(min_length=1)


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
) -> tuple[DirectorStageResult, object]:
    service = DirectorSkillService()
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        active_events = event_store.list_active()
        director_npcs = [
            npc
            for npc in director_npc_store.list_all()
            if npc.status != "retired"
        ]
        npc_profiles = npc_profile_store.list_all()
        settings = setting_store.list_active()

        service.register(
            name="present_event",
            description=(
                "允许一个已有活跃导演事件作为本轮舞台材料；只在其当前状态"
                "与玩家行动或世界反应确实相关时使用。"
            ),
            phases={"generation"},
            mutates=False,
            arguments_model=_PresentEventArgs,
            handler=lambda args: _present_event(event_store, args),
            arguments_validator=lambda args: _require_active_event(
                event_store,
                args.event_id,
            ),
        )
        service.register(
            name="introduce_npc",
            description=(
                "允许一个已准备的 Director NPC 或已有 NPCProfile 进入本轮；"
                "不得创建人物，每轮至多选择一个。"
            ),
            phases={"generation"},
            mutates=False,
            arguments_model=_IntroduceNPCArgs,
            handler=lambda args: _introduce_npc(
                director_npc_store,
                npc_profile_store,
                args,
            ),
            arguments_validator=lambda args: _require_npc(
                director_npc_store,
                npc_profile_store,
                args.npc_id,
            ),
        )
        service.register(
            name="foreshadow_setting",
            description=(
                "允许一个已有世界设定通过可见影响进入本轮，不得新增或改写设定。"
            ),
            phases={"generation"},
            mutates=False,
            arguments_model=_ForeshadowSettingArgs,
            handler=lambda args: _foreshadow_setting(setting_store, args),
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
            resources={
                "events": [
                    event.semantic_view()
                    for event in active_events
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
        decision = DirectorAgent(gateway, service).decide(
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
) -> tuple[object, DirectorSkillExecutionBatch]:
    service = DirectorSkillService()
    with (
        stores.director_event() as event_store,
        stores.director_npc() as director_npc_store,
        stores.npc_profile() as npc_profile_store,
        stores.world_setting() as setting_store,
    ):
        def review_event(args: _ReviewEventArgs):
            event = event_store.get_by_id(args.event_id)
            if event is None or event.status != "active":
                raise ValueError("review_event requires an active event")
            result = review_director_event_workflow(
                active_event=event,
                user_input=user_input,
                scene_facts=json.dumps(
                    scene_facts,
                    ensure_ascii=False,
                    indent=2,
                ),
                current_state=current_state,
                core_memory=core_memory,
                scene_summaries=recent_summaries,
                recent_summaries=recent_summaries,
                entity_profiles=entity_profiles,
                npc_profiles=npc_profiles,
                entity_mentions=entity_mentions,
                npc_mentions=npc_mentions,
                turn_index=turn_index,
                store=event_store,
                reviewer=DirectorEventReviewer(gateway),
                intervention_reviewer=DirectorEventInterventionReviewer(
                    gateway
                ),
                intervention_planner=DirectorEventInterventionPlanner(gateway),
                trace_id=trace_id,
            )
            return result.model_dump(mode="json")

        def create_event(args: _CreateEventArgs):
            if event_store.get_active(args.role) is not None:
                raise ValueError(
                    f"create_event cannot replace active {args.role} event"
                )
            selection = DirectorEventSelector(gateway).select_or_create(
                role=args.role,
                events=event_store.list_all(),
                current_state=current_state,
                core_memory=core_memory,
                recent_summaries=selection_summaries,
                entity_profiles=entity_profiles,
                npc_profiles=npc_profiles,
                trace_id=trace_id,
            )
            event = selection.to_event(
                role=args.role,
                turn_index=turn_index,
                current_clock=current_state.world_clock,
            )
            event_store.save(event)
            return {"event_id": event.event_id, "role": event.role}

        def create_npc(args: _CreateNPCArgs):
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
                    purpose=args.purpose,
                    anchors=args.anchors,
                ),
                creative_brief=draw_director_npc_creative_brief(),
                protagonist_name=current_state.player.name,
                turn_index=turn_index,
                trace_id=trace_id,
            )
            director_npc_store.save(draft)
            return {"npc_id": draft.draft_id, "name": draft.name}

        def enrich_setting(args: _EnrichSettingArgs):
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

        def bind_actor(args: _BindEventActorArgs):
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

        service.register(
            name="review_event",
            description=(
                "依据本轮已确认 Scene Facts 审查一个活跃事件并应用其生命周期；"
                "每个活跃事件通常每轮审查一次。"
            ),
            phases={"confirmation"},
            mutates=True,
            arguments_model=_ReviewEventArgs,
            handler=review_event,
            arguments_validator=lambda args: _require_active_event(
                event_store,
                args.event_id,
            ),
        )
        service.register(
            name="create_event",
            description=(
                "为当前没有活跃事件的 arc 或 ambient 角色创建一个独立事件。"
            ),
            phases={"confirmation"},
            mutates=True,
            arguments_model=_CreateEventArgs,
            handler=create_event,
        )
        service.register(
            name="create_npc",
            description=(
                "按明确的未来导演需要准备一个独立 NPC；Setting 不是必填前置。"
            ),
            phases={"confirmation"},
            mutates=True,
            arguments_model=_CreateNPCArgs,
            handler=create_npc,
        )
        service.register(
            name="enrich_setting",
            description="补全一个已有的具体 Setting seed，使其成为可用世界资产。",
            phases={"confirmation"},
            mutates=True,
            arguments_model=_EnrichSettingArgs,
            handler=enrich_setting,
            arguments_validator=lambda args: _require_setting_seed(
                setting_store,
                args.setting_id,
            ),
        )
        service.register(
            name="bind_event_actor",
            description=(
                "把已有 NPC 绑定到已有活跃事件；Event 和 NPC 的创建本身互不依赖。"
            ),
            phases={"confirmation"},
            mutates=True,
            arguments_model=_BindEventActorArgs,
            handler=bind_actor,
            arguments_validator=lambda args: _require_event_actor_binding(
                event_store,
                director_npc_store,
                npc_profile_store,
                event_id=args.event_id,
                npc_id=args.npc_id,
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
                    event.semantic_view()
                    for event in event_store.list_all()
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
            },
        )
        decision = DirectorAgent(gateway, service).decide(
            observation,
            trace_id=trace_id,
        )
        executions = service.execute_batch(
            decision,
            phase="confirmation",
        )
    return decision, executions


def _present_event(store, args: _PresentEventArgs) -> DirectorStageMaterial:
    event = store.get_by_id(args.event_id)
    if event is None or event.status != "active":
        raise ValueError("present_event requires an active event")
    return DirectorStageMaterial(
        kind="event",
        resource_id=event.event_id,
        reason=args.reason,
        payload=json.loads(project_director_event(event)),
    )


def _introduce_npc(
    director_npc_store,
    npc_profile_store,
    args: _IntroduceNPCArgs,
) -> DirectorStageMaterial:
    draft = director_npc_store.get_by_id(args.npc_id)
    if draft is not None and draft.status != "retired":
        payload = {
            "source": "director_npc",
            **draft.private_view().model_dump(mode="json"),
        }
    else:
        profile = npc_profile_store.get_by_id(args.npc_id)
        if profile is None:
            raise ValueError("introduce_npc references an unknown NPC")
        payload = {
            "source": "npc_profile",
            "npc_id": profile.npc_id,
            **profile.director_view().model_dump(mode="json"),
        }
    return DirectorStageMaterial(
        kind="npc",
        resource_id=args.npc_id,
        reason=args.reason,
        payload=payload,
    )


def _foreshadow_setting(
    store,
    args: _ForeshadowSettingArgs,
) -> DirectorStageMaterial:
    setting = store.get_by_id(args.setting_id)
    if setting is None or setting.status != "active":
        raise ValueError("foreshadow_setting requires an active setting")
    return DirectorStageMaterial(
        kind="setting",
        resource_id=setting.setting_id,
        reason=args.reason,
        payload=_setting_resource(setting),
    )


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
    if event is None or event.status != "active":
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
