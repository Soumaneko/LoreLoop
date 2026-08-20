import pytest

from types import SimpleNamespace
from app.core.llm.errors import LLMOutputValidationError
from app.domain.current_state import WorldClock
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_review_node_state


def test_confirmation_review_event_commits_engagement_and_hook_consumption(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(
        role="ambient",
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
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
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


def test_hidden_review_engages_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
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
                "consume_hook_ids": [],
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"


def test_engaged_review_resolves_event(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "猎户撤离码头，冲突收束。",
                "action": "resolve",
                "resolved_reason": "猎户撤离码头。",
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "resolved"
        assert persisted.resolved_turn == 3


def test_engaged_review_keeps_event_when_not_resolved(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)
        store.set_stage(event_id, "engaged", turn_index=2)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "核心冲突仍在持续。",
                "action": "engage",
        }
    ],
},
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    with stores.director_event as store:
        persisted = store.get_by_id(event_id)
        assert persisted is not None
        assert persisted.stage == "engaged"


def test_review_node_skips_events_created_this_turn(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=3, current_clock=WorldClock())
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


def test_keep_hidden_does_not_transition_stage(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(role="ambient", turn_index=1, current_clock=WorldClock())
    with stores.director_event as store:
        event_id = store.save(selection)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
    "reviews": [
        {
            "event_id": event_id,
                "reason": "玩家未接触事件。",
                "action": "keep_hidden",
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


def test_hidden_review_batch_covers_multiple_events_in_one_call(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    ids = []
    for index, title in enumerate(["无灯船", "夜探码头"], start=1):
        selection = DirectorEventSelectionResult(
            current_story_direction="河岸码头出现异常动静。",
            reason="独立世界事件。",
            title=title,
            duration_phases=7,
            actor_roles=[],
            subject=f"行动方{index}",
            intent="查明灵气异动",
            method="组队搜山",
            macro_intent=f"{title}的动机。",
            hook_pool=[
                EventHook(
                    hook_id=f"hook_{index}",
                    kind="auditory",
                    content=f"{title}的水面异响。",
                    trigger_anchors=["旧码头"],
                )
            ],
            world_impact=WorldImpact(description="外围变为凡人活动区。"),
        ).to_event(
            role="ambient", turn_index=index, current_clock=WorldClock()
        )
        with stores.director_event as store:
            ids.append(store.save(selection))

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": ids[0],
                    "reason": "事件一未接触。",
                    "action": "keep_hidden",
                    "consume_hook_ids": [],
                },
                {
                    "event_id": ids[1],
                    "reason": "事件二被接触。",
                    "action": "engage",
                    "consume_hook_ids": ["hook_2"],
                },
            ],
        },
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_hidden"]
    with stores.director_event as store:
        first = store.get_by_id(ids[0])
        second = store.get_by_id(ids[1])
        assert first is not None and first.stage == "hidden"
        assert second is not None and second.stage == "engaged"
        assert {hook.hook_id for hook in second.hook_pool if hook.consumed} == {
            "hook_2"
        }


def test_engaged_review_batch_covers_multiple_events_in_one_call(tmp_path):
    from app.domain.director_event.schemas import DirectorEventSelectionResult, EventHook, WorldImpact

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    ids = []
    for index, title in enumerate(["无灯船", "夜探码头"], start=1):
        selection = DirectorEventSelectionResult(
            current_story_direction="河岸码头出现异常动静。",
            reason="独立世界事件。",
            title=title,
            duration_phases=7,
            actor_roles=[],
            subject=f"行动方{index}",
            intent="查明灵气异动",
            method="组队搜山",
            macro_intent=f"{title}的动机。",
            hook_pool=[
                EventHook(
                    hook_id=f"hook_{index}",
                    kind="auditory",
                    content=f"{title}的水面异响。",
                    trigger_anchors=["旧码头"],
                )
            ],
            world_impact=WorldImpact(description="外围变为凡人活动区。"),
        ).to_event(
            role="ambient", turn_index=index, current_clock=WorldClock()
        )
        with stores.director_event as store:
            ids.append(store.save(selection))
            store.set_stage(ids[-1], "engaged", turn_index=index)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无额外调度。", "requests": []},
        event_review_output={
            "reviews": [
                {
                    "event_id": ids[0],
                    "reason": "事件一已收束。",
                    "action": "resolve",
                    "resolved_reason": "行动方撤离。",
                },
                {
                    "event_id": ids[1],
                    "reason": "事件二仍在追踪。",
                    "action": "engage",
                },
            ],
        },
    )

    confirmation_nodes.director_event_review_node(make_review_node_state(stores, gateway))

    assert gateway.tasks == ["director_event_review_engaged"]
    with stores.director_event as store:
        first = store.get_by_id(ids[0])
        second = store.get_by_id(ids[1])
        assert first is not None and first.stage == "resolved"
        assert second is not None and second.stage == "engaged"


def test_review_input_uses_actor_names_only(tmp_path):
    from app.domain.director_event.schemas import (
        DirectorActorRef,
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    selection = DirectorEventSelectionResult(
        current_story_direction="河岸码头出现异常动静。",
        reason="独立世界事件。",
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
    ).to_event(
        role="ambient",
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


def _review_selection(title):
    from app.domain.director_event.schemas import (
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    return DirectorEventSelectionResult(
        current_story_direction=f"{title} 出现异常动静。",
        reason="独立世界事件。",
        title=title,
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
    )


def test_hidden_success_engaged_failure_keeps_hidden_result(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_event as store:
        hidden_id = store.save(
            _review_selection("无灯船").to_event(
                role="ambient", turn_index=1, current_clock=WorldClock()
            )
        )
        engaged_id = store.save(
            _review_selection("夜哨").to_event(
                role="ambient", turn_index=1, current_clock=WorldClock()
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
