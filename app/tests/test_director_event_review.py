from app.tests.factories.director import make_review_selection
import pytest
from types import SimpleNamespace
from app.core.llm.errors import LLMOutputValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.director.confirmation import DirectorConfirmationExecutor
from app.domain.npc_profile.schemas import NPCProfile
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state, make_event_selection_output, make_intrude_event, make_review_node_state


def test_confirmation_review_event_commits_engagement_and_hook_consumption(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection("无灯船").to_event(
        turn_index=1,
        current_clock=WorldClock(),
    )
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "核心冲突仍在持续。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                }
            ],
        },
    )

    node_state = make_review_node_state(stores, gateway)
    confirmation_nodes.director_event_review_node(node_state)

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": event_id,
                    "reason": "核心冲突仍在持续。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_sound"],
                }
            ],
        },
    )

    node_state = make_review_node_state(stores, gateway)
    confirmation_nodes.director_event_review_node(node_state)

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"
        assert {hook.hook_id for hook in persisted.hook_pool if hook.consumed} == {
            "hook_sound"
        }


def test_confirmation_does_not_commit_when_write_db_disabled(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection("无灯船").to_event(turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(decision={"reasoning": "无调度。", "requests": []})
    with stores.director_event as store:
        node_state = {
            "write_db": False,
            "pipeline_input": SimpleNamespace(),
            "cache": {},
            "events": [],
        }
        from app.graph import confirmation_nodes

        confirmation_nodes.director_agent_confirm_node(node_state)

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"


def test_resolved_review_reopens_one_event_slot_in_same_confirmation(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        old_event_id = store.save(make_intrude_event())
        store.set_stage(old_event_id, "engaged", turn_index=2)
    setting = WorldSetting(
        kind="location",
        name="河岸船坞",
        summary="承担沿河商船修造与维护的老船坞。",
        public_facts=["船坞由几个老师傅共同经营"],
        hidden_facts=["部分旧账从未对外公开"],
        created_turn=1,
    )
    with stores.world_setting as store:
        store.save(setting)

    review_gateway = ScriptedDirectorGateway(
        decision={"reasoning": "旧事件已经收束。", "requests": []},
        event_review_output={
            "reviews": [{
                "event_id": old_event_id,
                "reason": "行动主体已经被永久驱逐。",
                "action": "resolve",
                "resolved_reason": "行动主体已经被永久驱逐。",
            }],
        },
    )
    confirmation_nodes.director_event_review_node(
        make_review_node_state(stores, review_gateway)
    )

    existing_actor = NPCProfile(name="船坞管事", role="船坞管事")
    with stores.npc_profile as store:
        store.upsert(existing_actor)

    create_gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "配额已经释放，可以布置下一事件。",
            "requests": [{
                "request_id": "event_2",
                "skill": "create_event",
                "arguments": {
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "事件依托河岸船坞。",
                        "matched_setting_id": setting.setting_id,
                    }],
                    "actors": [
                        {
                            "source": "existing",
                            "npc_id": existing_actor.npc_id,
                            "guidance": "由既有船坞管事充当演员。",
                        },
                    ],
                },
                "guidance": "围绕船坞的独立日常矛盾展开。",
            }],
        },
        event_selection_output=make_event_selection_output([
            {"actor_key": "actor_1", "role_in_event": "维持船坞日常"},
        ]),
    )
    DirectorConfirmationExecutor(
        gateway=create_gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="继续前进。",
        scene_facts={"scene_focus": "旧事件已经结束。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    ).run()

    with stores.director_event as store:
        assert store.get_by_id(old_event_id).stage == "resolved"
        [active] = store.list_active()
        assert active.event_id != old_event_id
        assert active.stage == "hidden"


def test_review_node_skips_events_created_this_turn(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection("无灯船").to_event(turn_index=3, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "不应被调用。",
                "action": "engage",
                "consume_hook_ids": [],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "hidden"
    assert gateway.tasks == []


@pytest.mark.parametrize("stage", ["hidden", "engaged"])
def test_review_batch_covers_multiple_events_in_one_call(tmp_path, stage):
    with CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path)) as stores:
        with stores.director_event as store:
            ids = [store.save(make_review_selection(title).to_event(
                turn_index=1, current_clock=WorldClock(),
            )) for title in ("无灯船", "夜探码头")]
            if stage == "engaged":
                for event_id in ids:
                    store.set_stage(event_id, stage, turn_index=2)
        gateway = ScriptedDirectorGateway(
            decision={"reasoning": "无额外调度。", "requests": []},
            event_review_output={"reviews": [
                {"event_id": ids[0], "reason": "第一事件。",
                 "action": "keep_hidden" if stage == "hidden" else "resolve",
                 "resolved_reason": "行动方撤离。" if stage == "engaged" else None},
                {"event_id": ids[1], "reason": "第二事件。", "action": "engage",
                 "consume_hook_ids": ["hook_sound"] if stage == "hidden" else []},
            ]},
        )
        confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))
        assert gateway.tasks == [f"director_event_review_{stage}"]
        with stores.director_event as store:
            first, second = [store.get_by_id(event_id) for event_id in ids]
            assert first.stage == ("hidden" if stage == "hidden" else "resolved")
            assert second.stage == "engaged"
            if stage == "hidden":
                assert {hook.hook_id for hook in second.hook_pool if hook.consumed} == {"hook_sound"}


def test_review_input_uses_actor_names_only(tmp_path):
    from app.domain.director_event.schemas import (
        DirectorActorRef,
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = make_review_selection("无灯船").to_event(
        turn_index=1,
        current_clock=WorldClock(),
        actors=[DirectorActorRef(name="港务署"), DirectorActorRef(name="林澈")],
    )
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

    hidden_input = gateway.input_data[0]["events_json"]
    assert '"港务署"' in hidden_input
    assert '"林澈"' in hidden_input


def test_hidden_success_engaged_failure_keeps_hidden_result(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        hidden_id = store.save(
            make_review_selection("无灯船").to_event( turn_index=1, current_clock=WorldClock()
            )
        )
        engaged_id = store.save(
            make_review_selection("夜哨").to_event( turn_index=1, current_clock=WorldClock()
            )
        )
        store.set_stage(engaged_id, "engaged", turn_index=2)

    class _FailEngagedGateway:
        def __init__(self, hidden_output):
            self.hidden_output = hidden_output

        def run(self, **kwargs):
            if kwargs["task"] == "director_event_review_hidden":
                return kwargs["output_schema"].model_validate(self.hidden_output)
            raise LLMOutputValidationError("engaged review failed")

    gateway = _FailEngagedGateway({
        "reviews": [{
            "event_id": hidden_id,
            "reason": "保持隐藏。",
            "action": "keep_hidden",
            "consume_hook_ids": [],
        }],
    })
    state = make_review_node_state(stores, gateway)
    state["runtime_services"] = SimpleNamespace(
        stores=stores,
        gateway=lambda **kwargs: gateway,
    )

    result = confirmation_nodes.director_event_review_node(state)

    assert any(
        review["event_id"] == hidden_id
        for review in result["director_event_reviews"]
    )
    with stores.director_event as store:
        assert store.get_by_id(hidden_id).stage == "hidden"
        assert store.get_by_id(engaged_id).stage == "engaged"


@pytest.mark.parametrize("stage, action, expected", [
    pytest.param("hidden", "engage", "engaged", id="first-contact"),
    pytest.param("hidden", "keep_hidden", "hidden", id="uncontacted"),
    pytest.param("engaged", "engage", "engaged", id="ongoing"),
    pytest.param("engaged", "resolve", "resolved", id="resolved"),
])
def test_review_applies_event_transition(tmp_path, stage, action, expected):
    with CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path)) as stores:
        with stores.director_event as store:
            event_id = store.save(make_review_selection("无灯船").to_event(
                turn_index=1, current_clock=WorldClock(),
            ))
            if stage == "engaged":
                store.set_stage(event_id, stage, turn_index=2)
        gateway = ScriptedDirectorGateway(
            decision={"reasoning": "无额外调度。", "requests": []},
            event_review_output={"reviews": [{
                "event_id": event_id, "action": action,
                "reason": "根据玩家接触和冲突进展审查。",
                "resolved_reason": "行动方撤离。" if action == "resolve" else None,
            }]},
        )
        confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))
        with stores.director_event as store:
            event = store.get_by_id(event_id)
            assert event.stage == expected
            if expected == "resolved":
                assert event.resolved_turn == 3
