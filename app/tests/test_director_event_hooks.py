from app.tests.factories.director import make_review_selection
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


@pytest.mark.parametrize("receipt", [
    "used", "edited", "unused", "legacy", "dry_run",
    "legacy-material", "ambiguous-legacy",
])
def test_confirmation_consumes_only_used_hook_before_director(tmp_path, monkeypatch, receipt):
    import hashlib
    import json
    from types import SimpleNamespace
    from app.domain.scene_writer import SceneWriterResult
    from app.domain.director_event.schemas import EventHook
    from app.graph import confirmation_graph

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event = make_review_selection("无灯船", hook_pool=[
        EventHook(hook_id="sound", kind="auditory", content="水面传来摇橹声。", trigger_anchors=["旧码头"]),
        EventHook(hook_id="rumor", kind="rumor", content="码头流传货船的传闻。", trigger_anchors=["旧码头"]),
    ]).to_event(turn_index=1, current_clock=WorldClock())
    stores.director_event.save(event)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "提供两个不同的征兆。", "requests": [
            {"request_id": hook.hook_id, "skill": "present_event",
             "arguments": {"event_id": event.event_id, "hook_id": hook.hook_id},
             "guidance": "呈现这个征兆。"}
            for hook in event.hook_pool
        ]},
        event_review_output={"reviews": [{"event_id": event.event_id,
            "reason": "玩家尚未直接调查。", "action": "keep_hidden"}]},
    )
    stage, _ = run_generation_director_agent(
        gateway=gateway, stores=stores, core_memory=CoreMemory(items=[]),
        current_state=make_current_state(), user_input="观察码头。", context_facts=[],
        recent_summaries=[], turn_index=3, trace_id="generation-hooks",
        npc_profiles=[], touched_event_candidates=[],
    )
    assert len({m.resource_id for m in stage.materials}) == 2
    assert not any(h.consumed for h in stores.director_event.get_by_id(event.event_id).hook_pool)
    if receipt in {"legacy-material", "ambiguous-legacy"}:
        stage.materials[0].resource_id = event.event_id
        if receipt == "ambiguous-legacy":
            stage.materials[1].resource_id = event.event_id
    text = "水面传来摇橹声。"
    scene = SceneWriterResult(
        scene_text=text,
        director_usages=([stage.materials[0].resource_id] * 2 + ["unknown"] if receipt != "unused" else []),
        npc_usages_text_hash=(hashlib.sha256(text.encode()).hexdigest() if receipt != "legacy" else None),
    )
    state = make_review_node_state(stores, gateway)
    state.update(pending=SimpleNamespace(director_stage=stage),
                 effective_scene_text="你离开了码头。" if receipt == "edited" else text,
                 write_db=receipt != "dry_run")
    state["pipeline_input"].scene_result = scene
    expected = receipt in {"used", "legacy-material"}
    def confirm_director(state):
        saved = stores.director_event.get_by_id(event.event_id)
        assert [h.consumed for h in saved.hook_pool] == [expected, False]
        assert saved.stage == "hidden"
        return {}

    monkeypatch.setattr(confirmation_graph, "director_agent_confirm_node", confirm_director)
    confirmation_graph.build_confirmation_director_graph.cache_clear()
    try:
        result = confirmation_graph.build_confirmation_director_graph().invoke(state)
    finally:
        confirmation_graph.build_confirmation_director_graph.cache_clear()
    assert not result.get("node_errors")
    assert gateway.tasks == ["director_agent_decide_generation"]
    if expected:
        state["pipeline_input"].turn_index = 4
        confirmation_nodes.director_hook_consumption_node(state)
        assert stores.director_event.get_by_id(event.event_id).hook_pool[0].consumed_turn == 3


def test_present_event_rejects_already_consumed_hook(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection(
        "无灯船",
        current_story_direction="河岸码头出现异常动静。",
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
    selection = make_review_selection(
        "无灯船",
        current_story_direction="河岸码头出现异常动静。",
        hook_pool=[
            EventHook(
                hook_id="hook_rumor",
                kind="rumor",
                content="码头流传无灯货船的传闻。",
                trigger_anchors=["旧码头"],
            )
        ],
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
        scheduled = collect_scheduled_npcs(stage.materials, npc_store, stores.director_npc)
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
        scheduled = collect_scheduled_npcs(stage.materials, npc_store, stores.director_npc)
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
        assert collect_scheduled_npcs(stage.materials, npc_store, stores.director_npc) == []

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
    selection = make_review_selection(
        "无灯船",
        current_story_direction="河岸码头出现异常动静。",
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


def test_confirmation_director_sees_consumed_hook_traces(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection(
        "无灯船",
        current_story_direction="河岸码头出现异常动静。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头", "港务署"],
            )
        ],
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
                }
            ],
        },
    )

    from app.domain.director.confirmation import DirectorConfirmationExecutor
    stores.director_event.mark_hooks_consumed(event_id, ["hook_sound"], turn_index=2)
    DirectorConfirmationExecutor(
        gateway=gateway, stores=stores, core_memory=CoreMemory(items=[]),
        current_state=make_current_state(), user_input="调查旧码头的声音。",
        scene_facts={"scene_focus": "调查旧码头。", "facts": []},
        recent_summaries=[], selection_summaries=[], turn_index=3, trace_id="director-hook",
    ).run()
    import json
    observation = json.loads(gateway.input_data[0]["director_observation_json"])
    [hook] = observation["resources"]["events"][0]["hook_pool"]
    assert hook["trigger_anchors"] == ["旧码头", "港务署"]
    assert hook["consumed"] and hook["consumed_turn"] == 2
    assert gateway.tasks == ["director_agent_decide_confirmation"]


@pytest.mark.parametrize("scenario,reverse", [
    ("promoted", False), ("promoted", True),
    ("renamed", False), ("prepared-name-collision", False),
])
def test_scheduled_event_actor_uses_referenced_identity_and_latest_profile(
    tmp_path, scenario, reverse,
):
    from app.domain.director import DirectorStageMaterial
    from app.domain.director.scheduled import collect_scheduled_npcs
    from app.domain.npc_profile.schemas import NPCProfile
    from app.tests.factories.director import make_director_npc

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc().model_copy(update={
        "role": "旧职务", "appearance": "旧外貌",
        "status": "prepared" if scenario == "prepared-name-collision" else "introduced",
    })
    stores.director_npc.save(draft)
    formal = NPCProfile(
        name="林医生" if scenario == "renamed" else draft.name,
        role="新职务", appearance="新外貌",
        source_director_npc_id=(
            None if scenario == "prepared-name-collision" else draft.draft_id
        ),
    )
    stores.npc_profile.upsert(formal)
    materials = [DirectorStageMaterial(
        kind="engaged_event", resource_id="event-selected",
        guidance="安排所引用的人物出场。",
        payload={"actors": [{"name": draft.name, "ref": {
            "kind": "npc", "resource_id": draft.draft_id,
        }}]},
    )]
    if scenario != "prepared-name-collision":
        materials.append(DirectorStageMaterial(
            kind="event", resource_id="event-hook", guidance="呈现此人的动静。",
            payload={"actors": [formal.name]},
        ))
    if reverse:
        materials.reverse()

    scheduled = collect_scheduled_npcs(materials, stores.npc_profile, stores.director_npc)

    expected = draft if scenario == "prepared-name-collision" else formal
    assert [(p.name, p.role, p.appearance) for p in scheduled] == [
        (expected.name, expected.role, expected.appearance),
    ]


@pytest.mark.parametrize("kind", ["event", "engaged_event"])
@pytest.mark.parametrize("status", ["introduced", "retired"])
def test_event_actor_draft_fallback_only_uses_prepared_records(tmp_path, kind, status):
    from app.domain.director import DirectorStageMaterial
    from app.domain.director.scheduled import collect_scheduled_npcs
    from app.tests.factories.director import make_director_npc

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    draft = make_director_npc().model_copy(update={"status": status})
    stores.director_npc.save(draft)
    actor = draft.name if kind == "event" else {
        "name": draft.name, "ref": {"kind": "npc", "resource_id": draft.draft_id},
    }
    material = DirectorStageMaterial(
        kind=kind, resource_id="event-selected", guidance="安排人物出场。",
        payload={"actors": [actor]},
    )

    scheduled = collect_scheduled_npcs([material], stores.npc_profile, stores.director_npc)

    # present_event can retain an unresolved name, but not obsolete draft data.
    assert all(p.role is None and p.appearance is None for p in scheduled)
