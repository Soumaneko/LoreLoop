import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director_event import (
    DirectorEvent,
    DirectorEventDraft,
    DirectorEventEntryPoint,
    DirectorEventReviewResult,
    DirectorEventSelectionResult,
    DirectorEventReviewer,
    DirectorEventSelector,
    DirectorEventStore,
)
from app.domain.director_event.views import (
    project_director_event,
    project_plot_director_event,
)
from app.domain.director_npc.schemas import DirectorNPCDraft
from app.domain.director_event.workflows import update_director_event_workflow


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
            trigger_anchors=["北郡", "驿站", "信使"],
            visible_sign="驿站开始拒收部分无印信件。",
            immediate_effect="北郡的通信与交易出现可被察觉的迟滞。",
        )
    ]


def _milestones() -> list[str]:
    return [
        "地方势力扣住信使并截走往来信件。",
        "被截信件开始改变北郡商人和官吏的判断。",
        "信件被公开利用或被夺回，通信秩序形成明确结果。",
    ]


def _event(title="失踪的信使", status="active") -> DirectorEvent:
    return DirectorEvent(
        title=title,
        status=status,
        premise="一名信使被地方势力扣押，其随身信件正被用于牵动北郡关系网。",
        actors=["信使", "地方势力"],
        objective="利用截获信件影响北郡的关系和交易秩序。",
        bridge_anchors=["北郡", "驿站", "商路"],
        milestones=_milestones(),
        milestone_interval_steps=[2, 3],
        next_progress_clock=_state().world_clock.advance_phase(2),
        progress_index=0,
        exposure="hidden",
        entry_points=_entry_points(),
        created_turn=1,
        updated_turn=1,
    )


def _event_draft() -> DirectorEventDraft:
    return DirectorEventDraft(
        title="旧教团的账册",
        premise="旧教团残党暗中转移一本能撬动地方商路的账册。",
        actors=["旧教团残党", "地方商人"],
        objective="利用账册重新控制北郡的商路债务。",
        bridge_anchors=["北郡", "商路", "账册"],
        milestones=[
            "旧教团残党把账册从藏匿点转入商路网络。",
            "账册内容开始影响地方商人的债务与站队。",
            "账册落入某一方手中，北郡商路关系形成明确变化。",
        ],
        milestone_interval_days=[0.5, 0.5],
        entry_points=[
            DirectorEventEntryPoint(
                trigger_anchors=["商铺", "客栈", "账册"],
                visible_sign="几名商人突然回避公开记账。",
                immediate_effect="交易场合出现可被利用或旁观的利益冲突。",
            )
        ],
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
        "action": "hold",
        "reason": "事件仍在世界中发展。",
        "exposure": "hidden",
        "player_contact_state": "玩家尚未接触该事件的任何前台征兆。",
        "evidence": ["本轮没有显示事件完成。"],
        "resolved_reason": None,
    }
    response.update(updates)
    return response


def _selection_response(**updates):
    response = {
        "reason": "该事件有独立目标，并共享北郡商路锚点。",
        "action": "create_new",
        "new_event": _event_draft().model_dump(mode="json"),
    }
    response.update(updates)
    return response


def _run_review(store, response, *, turn_index=2):
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    gateway = mock.build_gateway()
    return update_director_event_workflow(
        user_input="我继续处理眼前的事情。",
        scene_facts="卢西安完成了本轮行动。",
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
    def test_event_shape_uses_world_event_fields(self):
        data = _event().model_dump()

        assert data["entry_points"]
        assert set(data) == {
            "event_id",
            "title",
            "status",
            "premise",
            "actors",
            "objective",
            "bridge_anchors",
            "milestones",
            "milestone_interval_steps",
            "next_progress_clock",
            "progress_index",
            "exposure",
            "player_contact_state",
            "entry_points",
            "last_presented_turn",
            "resolved_reason",
            "resolved_turn",
            "created_turn",
            "updated_turn",
            "created_at",
            "updated_at",
        }

    def test_draft_requires_three_milestones(self):
        data = _event_draft().model_dump(mode="json")
        data["milestones"] = ["阶段一", "阶段二"]
        with pytest.raises(ValueError):
            DirectorEventDraft.model_validate(data)

    def test_draft_rejects_time_anchors_in_premise_and_entry_points(self):
        with pytest.raises(ValueError):
            DirectorEventDraft(
                title="带时间锚点的事件",
                premise="旧教团残党三日后转移账册。",
                actors=["旧教团残党"],
                objective="控制账册。",
                bridge_anchors=["北郡"],
                milestones=_milestones(),
                milestone_interval_days=[0.5, 0.5],
                entry_points=_entry_points(),
            )

        with pytest.raises(ValueError):
            DirectorEventEntryPoint(
                trigger_anchors=["北郡"],
                visible_sign="入夜后有人接触主角。",
                immediate_effect="产生短暂压力。",
            )


class TestDirectorEventStore:
    def test_only_one_active_event_and_previous_active_resolves(self):
        store = DirectorEventStore()
        first = _event("第一事件")
        second = _event("第二事件")

        store.save(first)
        store.save(second)

        assert store.get_active().event_id == second.event_id
        resolved = store.list_resolved()
        assert len(resolved) == 1
        assert resolved[0].event_id == first.event_id
        assert resolved[0].resolved_reason == "被新的导演事件替换"

    def test_resolve_touch_and_exposure(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        assert store.touch(event.event_id, turn_index=2) is True
        assert store.get_active().updated_turn == 2
        assert store.replace_player_contact(
            event.event_id,
            exposure="foreshadowed",
            player_contact_state="玩家已经看见驿站拒收无印信件。",
            turn_index=3,
        ) is True
        assert store.get_active().exposure == "foreshadowed"
        assert store.get_active().player_contact_state == "玩家已经看见驿站拒收无印信件。"
        assert store.get_active().last_presented_turn == 3
        assert store.resolve_active(event.event_id, turn_index=5, resolved_reason="事件已结束")

        resolved = store.get_by_id(event.event_id)
        assert resolved.status == "resolved"
        assert resolved.resolved_turn == 5
        assert resolved.resolved_reason == "事件已结束"

class TestDirectorEventReviewSchema:
    def test_resolve_fills_reason_when_missing(self):
        result = DirectorEventReviewResult.model_validate(
            _review_response(
                action="resolve",
                reason="事件已经完成。",
                resolved_reason=None,
            )
        )

        assert result.resolved_reason == "事件已经完成。"


class TestDirectorEventReviewerInput:
    def test_reviewer_receives_new_event_fields(self):
        spy = _SpyGateway(_review_response())
        DirectorEventReviewer(spy).review(
            active_event=_event(),
            user_input="我继续行动。",
            scene_facts="卢西安完成了本轮行动。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        event_json = json.loads(spy.calls[0]["input_data"]["active_event_json"])
        assert event_json["entry_points"]
        assert set(event_json) == {
            "event_id",
            "title",
            "premise",
            "actors",
            "objective",
            "bridge_anchors",
            "milestones",
            "milestone_interval_steps",
            "next_progress_clock",
            "progress_index",
            "exposure",
            "player_contact_state",
            "entry_points",
            "last_presented_turn",
            "status",
            "created_turn",
            "updated_turn",
        }


class TestDirectorEventViews:
    def test_active_projection_uses_semantic_view_only(self):
        data = json.loads(project_director_event(_event()))

        assert data["entry_points"]
        assert data["exposure"] == "hidden"

    def test_active_projection_omits_entry_points_after_engaged(self):
        data = json.loads(project_director_event(_event().model_copy(update={"exposure": "engaged"})))

        assert data["exposure"] == "engaged"
        assert "entry_points" not in data

    def test_recent_resolved_projection_uses_player_contact_not_internal_reason(self):
        event = _event(status="resolved").model_copy(
            update={
                "resolved_turn": 3,
                "resolved_reason": "内部审计理由",
                "player_contact_state": "玩家已阻止信件被截留。",
            }
        )
        data = json.loads(project_director_event(None, recent_resolved_event=event))

        assert data["status"] == "recently_resolved"
        assert data["player_contact_state"] == "玩家已阻止信件被截留。"
        assert "resolved_reason" not in data

    def test_plot_payload_uses_instruction_for_hidden_event(self):
        data = json.loads(
            project_plot_director_event(
                _event(),
                user_input="我经过北郡驿站。",
                current_state=_state(),
            )
        )

        assert data["event_use_instruction"]["mode"] == "foreshadow"
        assert data["event_use_instruction"]["allowed_effect"] == _entry_points()[0].visible_sign
        assert data["event_hint"] == {"visible_sign": _entry_points()[0].visible_sign}

    def test_plot_payload_hides_hidden_event_when_not_matched(self):
        data = json.loads(
            project_plot_director_event(
                _event(),
                user_input="我整理随身物品。",
                current_state=_state().model_copy(
                    update={
                        "scene": _state().scene.model_copy(update={"location": "别处"}),
                        "player": _state().player.model_copy(update={"position": "别处"}),
                    }
                ),
            )
        )

        assert data["event_use_instruction"]["mode"] == "none"
        assert data["event_hint"] is None

    def test_plot_payload_intersect_includes_current_milestone(self):
        contact_state = "玩家已经看见驿站拒收无印信件。"
        data = json.loads(
            project_plot_director_event(
                _event().model_copy(update={
                    "exposure": "foreshadowed",
                    "player_contact_state": contact_state,
                }),
                user_input="我经过北郡驿站并查看拒收的信件。",
                current_state=_state(),
            )
        )

        assert data["event_use_instruction"]["mode"] == "intersect"
        assert data["event_use_instruction"]["allowed_effect"] == _milestones()[0]
        assert data["event_hint"]["player_contact_state"] == contact_state
        assert data["event_hint"]["current_milestone"] == _milestones()[0]
        assert "premise" not in data["event_hint"]
        assert "visible_sign" not in data["event_hint"]

    def test_plot_payload_foreground_includes_current_state_and_contact(self):
        contact_state = "玩家正在调查被截留的信件。"
        data = json.loads(
            project_plot_director_event(
                _event().model_copy(update={
                    "exposure": "engaged",
                    "player_contact_state": contact_state,
                }),
                user_input="继续调查信件。",
                current_state=_state(),
            )
        )

        assert data["event_use_instruction"]["mode"] == "foreground"
        assert data["event_use_instruction"]["allowed_effect"] == _milestones()[0]
        assert data["event_hint"]["current_milestone"] == _milestones()[0]
        assert data["event_hint"]["player_contact_state"] == contact_state
        assert data["event_hint"]["canonical_premise"] == _event().premise
        assert "premise" not in data["event_hint"]


class TestDirectorEventWorkflow:
    def test_review_keep_touches_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(store, _review_response(), turn_index=2)

        assert result.mode == "review_active"
        assert result.notes == ["reviewed_existing_active_event", "kept_active_event"]
        assert store.get_active().updated_turn == 2

    def test_review_updates_exposure(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(
            store,
            _review_response(
                exposure="engaged",
                player_contact_state="玩家已直接询问暗拍请柬的来源。",
                reason="玩家已经直接调查该事件。",
                evidence=["玩家询问暗拍请柬来源。"],
            ),
            turn_index=2,
        )

        assert result.review_result.exposure == "engaged"
        assert store.get_active().exposure == "engaged"

    def test_review_replaces_complete_player_contact_state(self):
        store = DirectorEventStore()
        event = _event().model_copy(update={
            "exposure": "foreshadowed",
            "player_contact_state": "玩家看见驿站拒收无印信件。",
        })
        store.save(event)

        complete_state = "玩家看见驿站拒收无印信件，并已向驿站职员询问原因。"
        _run_review(
            store,
            _review_response(
                exposure="engaged",
                player_contact_state=complete_state,
                reason="玩家开始直接调查。",
            ),
            turn_index=2,
        )

        assert store.get_active().player_contact_state == complete_state

    def test_player_contact_state_is_bounded_to_current_snapshot(self):
        with pytest.raises(ValidationError):
            DirectorEventReviewResult(
                reason="测试长度限制。",
                action="hold",
                exposure="engaged",
                player_contact_state="过" * 51,
            )

        prompt = Path("app/prompts/confirmation/director_event_review_v1.md").read_text(
            encoding="utf-8"
        )
        assert "50 个汉字" in prompt
        assert "不是发生过什么的流水账" in prompt
        assert "当前接触情况" in prompt

    def test_review_resolves_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        _run_review(
            store,
            _review_response(action="resolve", reason="事件已经完成。"),
            turn_index=2,
        )

        resolved = store.get_by_id(event.event_id)
        assert resolved.status == "resolved"
        assert resolved.resolved_reason == "事件已经完成。"
        assert resolved.resolved_turn == 2

    def test_player_intervention_resolves_without_entering_default_ending(self):
        store = DirectorEventStore()
        event = _event().model_copy(update={"progress_index": 1})
        store.save(event)

        _run_review(
            store,
            _review_response(
                action="resolve",
                reason="玩家销毁关键对象，后续默认结局已不可能发生。",
            ),
            turn_index=2,
        )

        resolved = store.get_by_id(event.event_id)
        assert resolved.status == "resolved"
        assert resolved.progress_index == 1
        assert resolved.resolved_reason == "玩家销毁关键对象，后续默认结局已不可能发生。"

    def test_review_advances_active_event_progress(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(
            store,
            _review_response(action="advance", reason="玩家提前推动下一状态成立。"),
            turn_index=2,
        )

        assert result.notes == ["reviewed_existing_active_event", "advanced_active_event"]
        assert store.get_active().progress_index == 1
        assert store.get_active().next_progress_clock == _state().world_clock.advance_phase(3)

    def test_default_route_advance_to_final_state_resolves(self):
        store = DirectorEventStore()
        event = _event().model_copy(update={"progress_index": 1})
        store.save(event)

        _run_review(
            store,
            _review_response(
                action="advance",
                reason="无人阻止，默认最终状态已经形成。",
            ),
            turn_index=2,
        )

        resolved = store.get_by_id(event.event_id)
        assert resolved.status == "resolved"
        assert resolved.progress_index == 2
        assert resolved.resolved_reason == "无人阻止，默认最终状态已经形成。"

    def test_review_hold_auto_advances_when_due(self):
        store = DirectorEventStore()
        event = _event().model_copy(update={"next_progress_clock": _state().world_clock})
        store.save(event)

        result = _run_review(store, _review_response(), turn_index=2)

        assert "auto_advanced_active_event" in result.notes
        assert store.get_active().progress_index == 1

    def test_auto_advance_to_final_resolves_event(self):
        store = DirectorEventStore()
        event = _event().model_copy(
            update={
                "progress_index": 1,
                "next_progress_clock": _state().world_clock,
            }
        )
        store.save(event)

        result = _run_review(store, _review_response(), turn_index=2)

        assert "auto_resolved_active_event" in result.notes
        assert store.get_by_id(event.event_id).status == "resolved"

    def test_cooldown_after_resolve_skips_selection(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)
        store.resolve_active(event.event_id, turn_index=5, resolved_reason="已结束")

        result = update_director_event_workflow(
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=6,
            store=store,
            reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
            selector=DirectorEventSelector(MockLLM().build_gateway()),
            standby_director_npcs=[],
        )

        assert result.mode == "cooldown"
        assert result.changed_event_id == event.event_id

    def test_select_creates_new_event_without_required_standby_npc_binding(self):
        store = DirectorEventStore()
        draft = _event_draft().model_copy(update={"actors": ["税务官"], "bridge_anchors": ["北郡", "粮车"]})
        selection = _selection_response(new_event=draft.model_dump(mode="json"))
        mock = MockLLM()
        mock.set_responses([json.dumps(selection, ensure_ascii=False)])

        result = update_director_event_workflow(
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(mock.build_gateway()),
            selector=DirectorEventSelector(mock.build_gateway()),
            standby_director_npcs=[],
        )

        active = store.get_active()
        assert result.mode == "select_or_create"
        assert active.title == "旧教团的账册"
        assert active.milestones and len(active.milestones) == 3
        assert active.bridge_anchors == ["北郡", "粮车"]

    def test_selector_can_skip_creation(self):
        store = DirectorEventStore()
        selection = _selection_response(
            action="none",
            new_event=None,
            reason="没有足够稳定桥梁。",
        )
        mock = MockLLM()
        mock.set_responses([json.dumps(selection, ensure_ascii=False)])

        result = update_director_event_workflow(
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(mock.build_gateway()),
            selector=DirectorEventSelector(mock.build_gateway()),
            standby_director_npcs=[],
        )

        assert result.changed_event_id is None
        assert store.get_active() is None

    def test_selection_schema_orders_fields_and_rejects_unknown_fields(self):
        properties = DirectorEventSelectionResult.model_json_schema()["properties"]

        assert list(properties) == ["reason", "action", "new_event"]
        with pytest.raises(ValueError):
            DirectorEventSelectionResult.model_validate({
                "reason": "存在稳定桥梁。",
                "action": "none",
                "new_event": None,
                "unexpected_field": "不应接受",
            })


class TestDirectorEventSelector:
    def test_milestones_are_state_checkpoints_not_scripts(self):
        selector_prompt = Path(
            "app/prompts/confirmation/director_event_select_v1.md"
        ).read_text(encoding="utf-8")
        reviewer_prompt = Path(
            "app/prompts/confirmation/director_event_review_v1.md"
        ).read_text(encoding="utf-8")

        assert "世界状态检查点" in selector_prompt
        assert "不得绑定唯一执行者、唯一方法" in selector_prompt
        assert "玩家推动下一状态成立应 advance" in reviewer_prompt
        assert "不受 progress_index 限制" in reviewer_prompt
        assert "不得把“玩家介入”本身作为 resolve 依据" in reviewer_prompt

    def test_selector_receives_resolved_events_and_standby_secrets_without_scene_text(self):
        spy = _SpyGateway(_selection_response())
        DirectorEventSelector(spy).select_or_create(
            resolved_events=[_event(status="resolved")],
            standby_director_npcs=[_standby_npc()],
            current_state=_state(),
            core_memory=_core(),
        )

        input_data = spy.calls[0]["input_data"]
        resolved_events = json.loads(input_data["resolved_events_json"])
        standby_npcs = json.loads(input_data["standby_director_npcs_json"])

        assert resolved_events[0]["entry_points"]
        assert resolved_events[0]["exposure"] == "hidden"
        assert standby_npcs[0]["hidden_secret"]
        assert "recent_summaries_json" in input_data
        assert "entity_profiles_json" in input_data
        assert "npc_profiles_json" in input_data
        assert "scene_text" not in input_data
        assert "scene_summaries_json" not in input_data
