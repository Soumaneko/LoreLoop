import pytest

from pydantic import ValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_review_node_state
from app.tests.factories.director import run_generation_director_agent


def test_review_event_consumes_multiple_hooks_via_arguments(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            ),
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="码头流传无灯货船的传闻。",
                trigger_anchors=["旧码头"],
            ),
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "核心冲突仍在持续。",
                "action": "engage",
                "consume_hook_ids": ["hook_sound", "hook_rumor"],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert {hook.hook_id for hook in persisted.hook_pool if hook.consumed} == {
            "hook_sound",
            "hook_rumor",
        }


def test_present_event_rejects_already_consumed_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)
        store.mark_hooks_consumed(event_id, ["hook_sound"], turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "错误地引用已消费 hook。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {
                        "event_id": event_id,
                        "hook_id": "hook_sound",
                    },
                    "guidance": "呈现该征兆。",
                }
            ],
        }
    )

    with pytest.raises(ValueError, match="consumed hook_id"):
        run_generation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="看看码头有什么动静。",
            context_facts=[],
            recent_summaries=[],
            turn_index=3,
            trace_id="generation-director",
        npc_profiles=[],
            touched_event_candidates=[],
        )


def test_present_event_carries_optional_actors_into_material(tmp_path):
    from app.domain.director_event.schemas import (
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="码头流传无灯货船的传闻。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "通过传闻带出相关人物。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {
                        "event_id": event_id,
                        "hook_id": "hook_rumor",
                        "actors": ["林澈", "顾晚棠"],
                    },
                    "guidance": "通过码头传闻带出这两人。",
                }
            ],
        },
    )

    stage, _decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="打听码头传闻。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[],
    )

    assert len(stage.materials) == 1
    assert stage.materials[0].kind == "event"
    assert stage.materials[0].payload["actors"] == ["林澈", "顾晚棠"]

    from app.domain.director.scheduled import collect_scheduled_npcs

    with stores.npc_profile as npc_store:
        scheduled = collect_scheduled_npcs(stage.materials, npc_store)
    assert [entry.name for entry in scheduled] == ["林澈", "顾晚棠"]
    assert {entry.source for entry in scheduled} == {"present_event"}


def test_intrude_engaged_event_selects_subset_of_cast(tmp_path):
    from app.domain.director.scheduled import collect_scheduled_npcs
    from app.domain.director_event.schemas import (
        DirectorActorRef,
        DirectorEvent,
        DirectorResourceRef,
        WorldClock,
    )
    from app.domain.npc_profile.schemas import NPCProfile

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    samuel = NPCProfile(name="萨缪尔·灰潮", role="走私联络人")
    trevor = NPCProfile(name="特雷弗·石巷", role="跑腿小贼")
    with stores.npc_profile as store:
        store.upsert(samuel)
        store.upsert(trevor)
    event = DirectorEvent(
        title="走私暗流",
        subject="走私网络退潮",
        intent="把货物送入市场。",
        method="利用特许印签。",
        macro_intent="扩大地下流通。",
        deadline_clock=WorldClock(),
        created_turn=1,
        updated_turn=1,
        stage="engaged",
        actors=[
            DirectorActorRef(
                name=samuel.name,
                ref=DirectorResourceRef(
                    kind="npc", resource_id=samuel.npc_id
                ),
            ),
            DirectorActorRef(
                name=trevor.name,
                ref=DirectorResourceRef(
                    kind="npc", resource_id=trevor.npc_id
                ),
            ),
        ],
    )
    with stores.director_event as store:
        store.save(event)

    def _gateway(actors):
        return ScriptedDirectorGateway(decision={
            "reasoning": "让萨缪尔推动事件。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "intrude_engaged_event",
                    "arguments": {"event_id": event.event_id, "actors": actors},
                    "guidance": "让萨缪尔直接介入。",
                }
            ],
        })

    def _run(gateway):
        return run_generation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="观察暗号。",
            context_facts=[],
            recent_summaries=[],
            turn_index=3,
            trace_id="generation-director",
            npc_profiles=[],
            touched_event_candidates=[],
        )

    stage, _decision = _run(_gateway(["萨缪尔·灰潮"]))
    assert [
        actor["name"] for actor in stage.materials[0].payload["actors"]
    ] == ["萨缪尔·灰潮"]
    with stores.npc_profile as npc_store:
        scheduled = collect_scheduled_npcs(stage.materials, npc_store)
    assert [entry.name for entry in scheduled] == ["萨缪尔·灰潮"]

    # Event actors are not this turn's participants: the selection is required,
    # so an omitted cast can neither fall back to all nor silently schedule none.
    from app.domain.director.skill_specs import IntrudeEngagedEventArguments

    with pytest.raises(ValidationError):
        IntrudeEngagedEventArguments(event_id=event.event_id)

    # Explicitly choosing no NPCs still admits the event, without cast fallback.
    stage, _decision = _run(_gateway([]))
    assert len(stage.materials) == 1
    assert stage.materials[0].kind == "engaged_event"
    assert stage.materials[0].resource_id == event.event_id
    assert stage.materials[0].payload["actors"] == []
    with stores.npc_profile as npc_store:
        assert collect_scheduled_npcs(stage.materials, npc_store) == []

    # Reject the whole request even when only part of the selection is invalid;
    # otherwise guidance can still introduce an actor missing from the payload.
    for actors in (["不存在的演员"], [samuel.name, "不存在的演员"]):
        with pytest.raises(ValueError, match="actors not in event cast") as exc:
            _run(_gateway(actors))
        assert samuel.name in str(exc.value)
        assert trevor.name in str(exc.value)

    for actors in (["  "], [samuel.name, "\t"]):
        with pytest.raises(ValueError, match="non-blank names"):
            _run(_gateway(actors))

    stage, _decision = _run(_gateway([f"  {samuel.name}  "]))
    assert [actor["name"] for actor in stage.materials[0].payload["actors"]] == [
        samuel.name,
    ]


def test_present_event_requires_hook_id(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "无指定 hook。",
            "requests": [
                {
                    "request_id": "evt_1",
                    "skill": "present_event",
                    "arguments": {"event_id": event_id},
                    "guidance": "作为背景呈现。",
                }
            ],
        }
    )

    stage, decision = run_generation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="继续在码头巡查。",
        context_facts=[],
        recent_summaries=[],
        turn_index=3,
        trace_id="generation-director",
        npc_profiles=[],
        touched_event_candidates=[],
    )

    assert decision.requests == []
    assert stage.materials == []


def test_review_event_rejects_unknown_consume_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "错误地消费不存在的事件 hook。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_ghost"],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="consume_hook_ids not part of this event"):
        confirmation_nodes.director_event_review_node(
            make_review_node_state(stores, gateway)
        )

    with stores.director_event as store:
        assert store.get_by_id(event_id).stage == "hidden"


def test_review_event_rejects_already_consumed_consume_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)
        store.mark_hooks_consumed(event_id, ["hook_sound"], turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "错误地重复消费已消费 hook。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_sound"],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="consume_hook_ids already consumed"):
        confirmation_nodes.director_event_review_node(
            make_review_node_state(stores, gateway)
        )

    with stores.director_event as store:
        assert store.get_by_id(event_id).stage == "hidden"


def test_hidden_review_input_includes_hook_trigger_anchors(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        conception="独立世界事件。",
        title="无灯船",
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头", "港务署"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    ).to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "事件未接触。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                }
            ],
        },
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_hidden"]
    hidden_input = gateway.input_data[0]["events_json"]
    assert '"trigger_anchors"' in hidden_input
    assert "旧码头" in hidden_input
    assert "player_contacts" not in hidden_input
    assert "last_presented_turn" not in hidden_input
