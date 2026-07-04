import json

import pytest

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director_event import (
    DirectorEvent,
    DirectorEventDraft,
    DirectorEventEntryPoint,
    DirectorEventReviewResult,
    DirectorEventReviewer,
    DirectorEventSelector,
    DirectorEventStore,
)
from app.domain.director_event.views import (
    plot_outline_director_event_instructions,
    project_director_event,
    turn_resolution_director_event_instructions,
)
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.workflows.update_director_event import update_director_event_workflow


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡", participants=["卢西安"]),
        player=PlayerState(name="卢西安", position="北郡"),
    )


def _core() -> CoreMemory:
    return CoreMemory(items=[])


def _entry_points() -> list[DirectorEventEntryPoint]:
    return [
        DirectorEventEntryPoint(
            summary="信使相关的异常接触到北郡街面。",
            natural_contexts=["北郡街道", "驿站", "酒馆"],
            involved_npcs=["信使"],
            pressure="附近通信与交易出现可被玩家察觉的扰动。",
        )
    ]


def _event(title="失踪的信使", status="active") -> DirectorEvent:
    return DirectorEvent(
        title=title,
        status=status,
        backstage_premise="一名信使被地方势力扣押，其随身信件正被用于牵动北郡关系网。",
        driving_tension="信使被扣押后，截获信件仍在牵动北郡势力关系，通信安全尚未恢复。",
        entry_points=_entry_points(),
        related_npcs=["信使"],
        location_constraints=["北郡"],
        created_turn=1,
        updated_turn=1,
    )


def _event_draft() -> DirectorEventDraft:
    return DirectorEventDraft(
        title="旧教团的账册",
        backstage_premise="旧教团残党暗中转移一本能撬动地方商路的账册。",
        driving_tension="账册仍被旧教团残党控制，各方尚未确认其去向与可利用价值。",
        entry_points=[
            DirectorEventEntryPoint(
                summary="账册交易的边缘人物出现在主角附近。",
                natural_contexts=["商铺", "客栈", "码头"],
                involved_npcs=["旧教团残党"],
                pressure="交易外溢出可被玩家利用或旁观的利益冲突。",
            )
        ],
        related_npcs=["旧教团残党"],
        location_constraints=["北郡"],
    )


def _standby_npc(name="旧教团残党") -> DirectorNPCDraft:
    return DirectorNPCDraft(
        name=name,
        role="可牵动后台事件的未出场导演 NPC",
        appearance="灰衣蒙面，行踪谨慎。",
        personality_traits=["谨慎", "精于交涉"],
        known_abilities=["联络地方势力"],
        core_motivation="借局部事件扩大自身影响力。",
        hidden_secret="暗中握有一份能改变局势的账册。",
        conflict_style="优先借他人之手施压，必要时亲自谈判。",
        social_style="话少而准确，擅长试探对方底线。",
        encounter_potentials=[
            "在交易场合与主角产生交集",
            "通过中间人向主角释放消息",
            "因利益冲突被迫现身",
        ],
        created_turn=1,
    )


def _review_response(**updates):
    response = {
        "decision": "keep_active",
        "reason": "事件仍可继续作为后台张力。",
        "player_involved": False,
        "evidence": ["本轮没有显示事件完成或不可用。"],
        "suspended_reason": None,
    }
    response.update(updates)
    return response


def _run_review(store, response, *, turn_index=2):
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    gateway = mock.build_gateway()
    return update_director_event_workflow(
        user_input="我继续处理眼前的事情。",
        scene_text="卢西安完成了本轮行动。",
        current_state=_state(),
        core_memory=_core(),
        scene_summaries=[],
        turn_index=turn_index,
        store=store,
        reviewer=DirectorEventReviewer(gateway),
        selector=DirectorEventSelector(gateway),
    )


class _SpyGateway:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestDirectorEventSchema:
    def test_event_shape_has_entry_points_without_legacy_fields(self):
        data = _event().model_dump()

        assert data["entry_points"]
        assert set(data) == {
            "event_id",
            "title",
            "status",
            "backstage_premise",
            "driving_tension",
            "player_involved",
            "entry_points",
            "related_npcs",
            "location_constraints",
            "suspended_reason",
            "resolved_reason",
            "resolved_turn",
            "created_turn",
            "updated_turn",
            "created_at",
            "updated_at",
        }

    def test_schema_orders_driving_tension_after_location_constraints(self):
        properties = list(DirectorEventDraft.model_json_schema()["properties"])

        assert properties.index("driving_tension") > properties.index("location_constraints")

    def test_draft_rejects_time_anchors_in_premise_and_entry_points(self):
        with pytest.raises(ValueError):
            DirectorEventDraft(
                title="带时间锚点的事件",
                backstage_premise="旧教团残党三日后转移账册。",
                driving_tension="账册仍未落定。",
                entry_points=_entry_points(),
                related_npcs=["旧教团残党"],
            )

        with pytest.raises(ValueError):
            DirectorEventEntryPoint(
                summary="入夜后有人接触主角。",
                pressure="产生短暂压力。",
            )


class TestDirectorEventStore:
    def test_only_one_active_event_and_suspension_preserves_entry_points(self):
        store = DirectorEventStore()
        first = _event("第一事件")
        second = _event("第二事件")

        store.save(first)
        store.save(second)

        assert store.get_active().event_id == second.event_id
        suspended = store.list_suspended()
        assert len(suspended) == 1
        assert suspended[0].event_id == first.event_id
        assert suspended[0].entry_points == first.entry_points

    def test_suspend_archive_and_touch(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        assert store.touch(event.event_id, turn_index=2) is True
        assert store.get_active().updated_turn == 2
        assert store.suspend(event.event_id, "暂时不可达", turn_index=3) is True
        assert store.get_by_id(event.event_id).status == "suspended"
        assert store.activate(event.event_id, turn_index=4) is True
        assert store.archive_active(event.event_id, turn_index=5, resolved_reason="事件已结束") is True

        archived = store.get_by_id(event.event_id)
        assert archived.status == "archived"
        assert archived.resolved_turn == 5
        assert archived.resolved_reason == "事件已结束"


class TestDirectorEventReviewSchema:
    def test_suspend_fills_reason_when_missing(self):
        result = DirectorEventReviewResult.model_validate(
            _review_response(
                decision="suspend_active",
                reason="用户明确先不处理此事。",
                suspended_reason=None,
            )
        )

        assert result.suspended_reason == "用户明确先不处理此事。"


class TestDirectorEventReviewerInput:
    def test_reviewer_receives_entry_points_without_legacy_fields(self):
        spy = _SpyGateway(_review_response())
        DirectorEventReviewer(spy).review(
            active_event=_event(),
            user_input="我继续行动。",
            scene_text="卢西安完成了本轮行动。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        event_json = json.loads(spy.calls[0]["input_data"]["active_event_json"])
        assert event_json["entry_points"]
        assert set(event_json) == {
            "event_id",
            "title",
            "backstage_premise",
            "driving_tension",
            "player_involved",
            "entry_points",
            "related_npcs",
            "location_constraints",
            "status",
            "created_turn",
            "updated_turn",
        }


class TestDirectorEventViews:
    def test_active_projection_uses_semantic_view_only(self):
        data = json.loads(project_director_event(_event()))

        assert data["entry_points"]
        assert data["player_involved"] is False

    def test_active_projection_omits_entry_points_after_player_involved(self):
        data = json.loads(
            project_director_event(_event().model_copy(update={"player_involved": True}))
        )

        assert data["player_involved"] is True
        assert "entry_points" not in data

    def test_recent_archived_projection_uses_resolution_reason(self):
        event = _event(status="archived").model_copy(
            update={"resolved_turn": 3, "resolved_reason": "事件已被确认收束"}
        )
        data = json.loads(project_director_event(None, recent_archived_event=event))

        assert data["status"] == "recently_archived"
        assert data["resolved_reason"] == "事件已被确认收束"

    def test_instructions_are_empty_without_active_or_recent_event(self):
        assert plot_outline_director_event_instructions(None) == ""
        assert turn_resolution_director_event_instructions(None) == ""

    def test_instructions_reference_entry_points_without_legacy_terms(self):
        plot_text = plot_outline_director_event_instructions(_event())
        tr_text = turn_resolution_director_event_instructions(_event())

        assert "entry_points" in plot_text
        assert "entry_points" in tr_text


class TestDirectorEventWorkflow:
    def test_review_keep_touches_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(store, _review_response(), turn_index=2)

        assert result.mode == "review_active"
        assert result.notes == ["reviewed_existing_active_event", "kept_active_event"]
        assert store.get_active().updated_turn == 2

    def test_review_marks_player_involved_once_true(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(
            store,
            _review_response(
                player_involved=True,
                reason="玩家已经直接调查该事件。",
                evidence=["玩家询问暗拍请柬来源。"],
            ),
            turn_index=2,
        )

        assert result.review_result.player_involved is True
        assert store.get_active().player_involved is True

    def test_review_archive_archives_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        _run_review(
            store,
            _review_response(decision="archive_active", reason="事件已经完成。"),
            turn_index=2,
        )

        archived = store.get_by_id(event.event_id)
        assert archived.status == "archived"
        assert archived.resolved_reason == "事件已经完成。"
        assert archived.resolved_turn == 2

    def test_review_suspend_suspends_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        _run_review(
            store,
            _review_response(
                decision="suspend_active",
                reason="暂时不可达。",
                suspended_reason="暂时不可达。",
            ),
            turn_index=2,
        )

        suspended = store.get_by_id(event.event_id)
        assert suspended.status == "suspended"
        assert suspended.suspended_reason == "暂时不可达。"

    def test_cooldown_after_archive_skips_selection(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)
        store.archive_active(event.event_id, turn_index=5, resolved_reason="已结束")

        result = update_director_event_workflow(
            user_input="继续。",
            scene_text="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=6,
            store=store,
            reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
            selector=DirectorEventSelector(MockLLM().build_gateway()),
            standby_director_npcs=[_standby_npc()],
        )

        assert result.mode == "cooldown"
        assert result.changed_event_id == event.event_id

    def test_select_creates_new_event_bound_to_standby_npc(self):
        store = DirectorEventStore()
        selection = {
            "suspension_evaluation": "没有可激活事件。",
            "backstage_undercurrent": "旧教团残党正在转移账册。",
            "action": "create_new",
            "reason": "该事件适合当前区域。",
            "activate_event_id": None,
            "new_event": _event_draft().model_dump(mode="json"),
        }
        mock = MockLLM()
        mock.set_responses([json.dumps(selection, ensure_ascii=False)])

        result = update_director_event_workflow(
            user_input="继续。",
            scene_text="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(mock.build_gateway()),
            selector=DirectorEventSelector(mock.build_gateway()),
            standby_director_npcs=[_standby_npc()],
        )

        active = store.get_active()
        assert result.mode == "select_or_create"
        assert active.title == "旧教团的账册"
        assert active.entry_points
        assert "旧教团残党" in active.related_npcs

    def test_create_new_requires_standby_npc_relation(self):
        store = DirectorEventStore()
        draft = _event_draft().model_copy(update={"related_npcs": ["陌生人"]})
        selection = {
            "suspension_evaluation": "没有可激活事件。",
            "backstage_undercurrent": "陌生人正在行动。",
            "action": "create_new",
            "reason": "创建。",
            "activate_event_id": None,
            "new_event": draft.model_dump(mode="json"),
        }
        mock = MockLLM()
        mock.set_responses([json.dumps(selection, ensure_ascii=False)])

        with pytest.raises(ValueError):
            update_director_event_workflow(
                user_input="继续。",
                scene_text="没有新事件。",
                current_state=_state(),
                core_memory=_core(),
                scene_summaries=[],
                turn_index=2,
                store=store,
                reviewer=DirectorEventReviewer(mock.build_gateway()),
                selector=DirectorEventSelector(mock.build_gateway()),
                standby_director_npcs=[_standby_npc()],
            )


class TestDirectorEventSelector:
    def test_selector_receives_archived_entry_points_and_standby_secrets(self):
        response = {
            "suspension_evaluation": "没有可激活事件。",
            "backstage_undercurrent": "旧教团残党正在转移账册。",
            "action": "create_new",
            "reason": "创建。",
            "activate_event_id": None,
            "new_event": _event_draft().model_dump(mode="json"),
        }
        spy = _SpyGateway(response)
        DirectorEventSelector(spy).select_or_create(
            suspended_events=[],
            archived_events=[_event(status="archived")],
            standby_director_npcs=[_standby_npc()],
            scene_text="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        archived_events = json.loads(spy.calls[0]["input_data"]["archived_events_json"])
        standby_npcs = json.loads(spy.calls[0]["input_data"]["standby_director_npcs_json"])

        assert archived_events[0]["entry_points"]
        assert archived_events[0]["player_involved"] is False
        assert standby_npcs[0]["hidden_secret"]
