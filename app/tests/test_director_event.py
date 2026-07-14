import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director_event import (
    DirectorEvent,
    DirectorEventConsumption,
    DirectorEventEntryPoint,
    DirectorEventReviewResult,
    DirectorEventSelectionResult,
    DirectorEventReviewer,
    DirectorEventSelector,
    DirectorEventStore,
)
from app.domain.director_event.consumer import DirectorEventConsumer
from app.domain.director_event.views import (
    project_director_event,
    project_director_event_consumptions,
)
from app.domain.context_facts.schemas import ContextFactSet
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


def _event(title="失踪的信使", status="active", role="ambient") -> DirectorEvent:
    return DirectorEvent(
        role=role,
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


def _selection_result() -> DirectorEventSelectionResult:
    return DirectorEventSelectionResult(
        current_story_direction="卢西安正在处理北郡眼前事务。",
        reason="不能生成的内容：不得延续卢西安眼前事务。",
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
                trigger_anchors=["北郡", "商路", "账册"],
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
    response = _selection_result().model_dump(mode="json")
    response.update(updates)
    return response


def _run_review(store, response, *, turn_index=2):
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    gateway = mock.build_gateway()
    return update_director_event_workflow(
        role="ambient",
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
            "role",
            "title",
            "status",
            "premise",
            "director_npc_id",
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
        data = _selection_result().model_dump(mode="json")
        data["milestones"] = ["阶段一", "阶段二"]
        with pytest.raises(ValueError):
            DirectorEventSelectionResult.model_validate(data)

    def test_draft_requires_actor_and_allows_semantic_entry_point_anchors(self):
        data = _selection_result().model_dump(mode="json")
        data["actors"] = []
        with pytest.raises(ValueError):
            DirectorEventSelectionResult.model_validate(data)

        data = _selection_result().model_dump(mode="json")
        data["entry_points"][0]["trigger_anchors"] = ["临时出现的信使"]
        result = DirectorEventSelectionResult.model_validate(data)
        assert result.entry_points[0].trigger_anchors == ["临时出现的信使"]

    def test_schema_allows_time_language_for_prompt_level_review(self):
        result = DirectorEventSelectionResult(
            current_story_direction="当前剧情走向。",
            reason="禁止升级内容。",
            title="带时间表达的事件",
            premise="旧教团残党三日后转移账册。",
            actors=["旧教团残党"],
            objective="控制账册。",
            bridge_anchors=["北郡"],
            milestones=_milestones(),
            milestone_interval_days=[0.5, 0.5],
            entry_points=[
                DirectorEventEntryPoint(
                    trigger_anchors=["北郡"],
                    visible_sign="入夜后有人接触主角。",
                    immediate_effect="产生短暂压力。",
                )
            ],
        )

        assert result.premise == "旧教团残党三日后转移账册。"


class TestDirectorEventStore:
    def test_arc_and_ambient_active_events_coexist(self):
        store = DirectorEventStore()
        arc = _event("主线事件", role="arc")
        ambient = _event("环境事件", role="ambient")

        store.save(arc)
        store.save(ambient)

        assert store.get_active("arc").event_id == arc.event_id
        assert store.get_active("ambient").event_id == ambient.event_id
        assert {event.role for event in store.list_active()} == {"arc", "ambient"}

    def test_only_one_active_event_and_previous_active_resolves(self):
        store = DirectorEventStore()
        first = _event("第一事件")
        second = _event("第二事件")

        store.save(first)
        store.save(second)

        assert store.get_active("ambient").event_id == second.event_id
        resolved = store.list_resolved()
        assert len(resolved) == 1
        assert resolved[0].event_id == first.event_id
        assert resolved[0].resolved_reason == "被新的导演事件替换"

    def test_resolve_touch_and_exposure(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        assert store.touch(event.event_id, turn_index=2) is True
        assert store.get_active("ambient").updated_turn == 2
        assert store.replace_player_contact(
            event.event_id,
            exposure="foreshadowed",
            player_contact_state="玩家已经看见驿站拒收无印信件。",
            turn_index=3,
        ) is True
        assert store.get_active("ambient").exposure == "foreshadowed"
        assert store.get_active("ambient").player_contact_state == "玩家已经看见驿站拒收无印信件。"
        assert store.get_active("ambient").last_presented_turn == 3
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
    def test_reviewer_receives_event_fields(self):
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
            "role",
            "title",
            "premise",
            "director_npc_id",
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
    def test_hidden_projection_exposes_only_current_progress_and_entry_points(self):
        event = _event()
        data = json.loads(project_director_event(event))

        assert data["entry_points"]
        assert data["exposure"] == "hidden"
        assert data["already_happened"] == [
            f"主角当前接触情况：{event.player_contact_state}"
        ]
        assert data["currently_happening"] == event.milestones[0]
        assert "milestones" not in data
        assert "progress_index" not in data
        assert "player_contact_state" not in data
        assert "milestone_interval_steps" not in data
        assert "next_progress_clock" not in data

    @pytest.mark.parametrize("exposure", ["foreshadowed", "engaged"])
    def test_visible_projection_omits_entry_points_and_merges_past_state(self, exposure):
        event = _event().model_copy(update={
            "exposure": exposure,
            "progress_index": 1,
            "player_contact_state": "玩家已经看过驿站公告。",
        })
        data = json.loads(project_director_event(event))

        assert data["exposure"] == exposure
        assert "entry_points" not in data
        assert data["already_happened"] == [
            event.milestones[0],
            "主角当前接触情况：玩家已经看过驿站公告。",
        ]
        assert data["currently_happening"] == event.milestones[1]

    def test_plot_projection_exposes_only_consumption_boundaries(self):
        consumptions = [
            DirectorEventConsumption(
                event_id="event_arc",
                reason="当前位置没有真实接触，仅保留相关事实边界。",
                use_event=False,
                immutable_constraints=["公告仍位于冒险者工会告示板。"],
                forbidden_inferences=["不得改成希尔达随身持有公告。"],
            ),
            DirectorEventConsumption(
                event_id="event_ambient",
                reason="当前位于旅店，可以自然看见留言板。",
                use_event=True,
                player_contact_state="玩家已经看过旅店留言板上的木偶师便条。",
                allowed_facts=["旅店留言板上有一张木偶师便条。"],
                immutable_constraints=["便条固定在旅店留言板上。"],
                forbidden_inferences=["不得推断张贴者身份。"],
            ),
        ]

        data = json.loads(project_director_event_consumptions(consumptions))

        assert len(data) == 1
        assert all(set(item) == {
            "player_contact_state",
            "allowed_facts",
            "immutable_constraints",
            "forbidden_inferences",
        } for item in data)
        assert data[0]["player_contact_state"] == "玩家已经看过旅店留言板上的木偶师便条。"
        assert data[0]["allowed_facts"] == ["旅店留言板上有一张木偶师便条。"]
        assert "event_arc" not in json.dumps(data, ensure_ascii=False)
        assert "reason" not in json.dumps(data, ensure_ascii=False)


class TestDirectorEventConsumer:
    def test_prompt_distinguishes_private_facts_from_forbidden_conclusions(self):
        prompt = Path(
            "app/prompts/generation/director_event_consume_v1.md"
        ).read_text(encoding="utf-8")

        assert "已经成立、真实存在，但本回合不应向角色或玩家公开" in prompt
        assert "防止下游 Plot 发生知识泄露" in prompt
        assert "already_happened" in prompt
        assert "currently_happening" in prompt
        assert "player_contact_state：原样复制" not in prompt
        assert "不能再次成为 allowed_facts" in prompt
        assert "allowed_facts 必须完成前台投影" in prompt
        assert "通过什么地点、人物、公开行为、环境变化或可观察征兆具体表现" in prompt
        assert "这是照抄 already_happened" in prompt

    def test_consumption_schema_keeps_use_decision_consistent(self):
        with pytest.raises(ValidationError):
            DirectorEventConsumption(
                event_id="event_1",
                reason="决定使用。",
                use_event=True,
            )
        with pytest.raises(ValidationError):
            DirectorEventConsumption(
                event_id="event_1",
                reason="决定不使用。",
                use_event=False,
                allowed_facts=["不应存在的前台事实"],
            )

    def test_consumer_receives_tr_context_and_returns_one_result_per_event(self):
        arc = _event("主线事件", role="arc")
        ambient = _event("环境事件", role="ambient")
        spy = _SpyGateway({
            "consumptions": [
                {
                    "event_id": arc.event_id,
                    "reason": "仅提到目标地点，没有真实接触。",
                    "use_event": False,
                    "allowed_facts": [],
                    "immutable_constraints": [],
                    "forbidden_inferences": ["不得表现远方现场征兆。"],
                },
                {
                    "event_id": ambient.event_id,
                    "reason": "当前场景存在自然接触。",
                    "use_event": True,
                    "allowed_facts": ["驿站开始拒收部分无印信件。"],
                    "immutable_constraints": ["征兆发生在北郡驿站。"],
                    "forbidden_inferences": ["不得确认幕后势力。"],
                },
            ]
        })

        result = DirectorEventConsumer(spy).consume(
            events=[arc, ambient],
            user_input="我询问北郡的消息。",
            current_state=_state(),
            core_memory=_core(),
            context_facts=ContextFactSet(turn_focus="询问消息", facts=[]),
            entity_profiles=[],
            npc_profiles=[],
        )

        assert [item.event_id for item in result] == [arc.event_id, ambient.event_id]
        assert [item.player_contact_state for item in result] == [
            arc.player_contact_state,
            ambient.player_contact_state,
        ]
        input_data = spy.calls[0]["input_data"]
        output_schema = json.loads(input_data["output_schema_json"])
        consumption_schema = output_schema["$defs"]["_DirectorEventConsumptionDraft"]
        assert "player_contact_state" not in consumption_schema["properties"]
        assert set(input_data) == {
            "core_memory_json",
            "current_state_json",
            "user_input",
            "context_facts_json",
            "entity_profiles_json",
            "npc_profiles_json",
            "director_events_json",
            "output_schema_json",
        }

    def test_consumer_rejects_missing_event_result(self):
        event = _event()
        spy = _SpyGateway({"consumptions": []})

        with pytest.raises(ValueError, match="exactly one result"):
            DirectorEventConsumer(spy).consume(
                events=[event],
                user_input="继续。",
                current_state=_state(),
                core_memory=_core(),
                context_facts=ContextFactSet(turn_focus="继续", facts=[]),
            )


class TestDirectorEventWorkflow:
    def test_review_keep_touches_active_event(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        result = _run_review(store, _review_response(), turn_index=2)

        assert result.mode == "review_active"
        assert result.notes == ["reviewed_existing_active_event", "kept_active_event"]
        assert store.get_active("ambient").updated_turn == 2

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
        assert store.get_active("ambient").exposure == "engaged"

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

        assert store.get_active("ambient").player_contact_state == complete_state

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
        assert store.get_active("ambient").progress_index == 1
        assert store.get_active("ambient").next_progress_clock == _state().world_clock.advance_phase(3)

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
        assert store.get_active("ambient").progress_index == 1

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
            role="ambient",
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=6,
            store=store,
            reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
            selector=DirectorEventSelector(MockLLM().build_gateway()),
        )

        assert result.mode == "cooldown"
        assert result.changed_event_id == event.event_id

    def test_cooldown_is_isolated_by_role(self):
        store = DirectorEventStore()
        ambient = _event(role="ambient")
        store.save(ambient)
        store.resolve_active(ambient.event_id, turn_index=5, resolved_reason="已结束")
        mock = MockLLM()
        mock.set_responses([json.dumps(_selection_response(), ensure_ascii=False)])

        result = update_director_event_workflow(
            role="arc",
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=6,
            store=store,
            reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
            selector=DirectorEventSelector(mock.build_gateway()),
        )

        assert result.mode == "select_or_create"
        assert store.get_active("arc") is not None

    def test_later_role_receives_event_created_by_earlier_role(self):
        store = DirectorEventStore()

        class _RecordingSelector:
            def __init__(self):
                self.calls = []

            def select_or_create(self, *, role, events, **_kwargs):
                self.calls.append((role, list(events)))
                return _selection_result()

        selector = _RecordingSelector()
        for role in ("arc", "ambient"):
            update_director_event_workflow(
                role=role,
                user_input="继续。",
                scene_facts="没有新事件。",
                current_state=_state(),
                core_memory=_core(),
                scene_summaries=[],
                turn_index=2,
                store=store,
                reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
                selector=selector,
            )

        assert selector.calls[0] == ("arc", [])
        ambient_events = selector.calls[1][1]
        assert len(ambient_events) == 1
        assert ambient_events[0].role == "arc"
        assert ambient_events[0].status == "active"

    def test_select_creates_event_without_required_standby_npc_binding(self):
        store = DirectorEventStore()
        draft = _selection_result().model_copy(
            update={
                "actors": ["税务官"],
                "bridge_anchors": ["北郡", "粮车"],
                "entry_points": [
                    DirectorEventEntryPoint(
                        trigger_anchors=["北郡", "粮车"],
                        visible_sign="粮车在北郡入口被税务官集中查验。",
                        immediate_effect="北郡粮食交易出现可见迟滞。",
                    )
                ],
            }
        )
        selection = draft.model_dump(mode="json")
        mock = MockLLM()
        mock.set_responses([json.dumps(selection, ensure_ascii=False)])

        result = update_director_event_workflow(
            role="ambient",
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(mock.build_gateway()),
            selector=DirectorEventSelector(mock.build_gateway()),
        )

        active = store.get_active("ambient")
        assert result.mode == "select_or_create"
        assert active.title == "旧教团的账册"
        assert active.milestones and len(active.milestones) == 3
        assert active.bridge_anchors == ["北郡", "粮车"]

    def test_ambient_event_persists_selected_standby_director_npc(self):
        store = DirectorEventStore()
        standby = _standby_npc()
        selection = _selection_result().model_copy(
            update={
                "director_npc_id": standby.draft_id,
                "actors": [standby.name, "地方商人"],
            }
        )

        result = update_director_event_workflow(
            role="ambient",
            user_input="继续。",
            scene_facts="没有新事件。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            standby_director_npcs=[standby],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
            selector=DirectorEventSelector(
                _SpyGateway(selection.model_dump(mode="json"))
            ),
        )

        assert result.active_after is not None
        assert result.active_after.director_npc_id == standby.draft_id

    def test_event_rejects_unknown_or_arc_director_npc_binding(self):
        standby = _standby_npc()
        selection = _selection_result().model_copy(
            update={
                "director_npc_id": standby.draft_id,
                "actors": [standby.name],
            }
        )

        for role, candidates in (("ambient", []), ("arc", [standby])):
            with pytest.raises(ValueError):
                update_director_event_workflow(
                    role=role,
                    user_input="继续。",
                    scene_facts="没有新事件。",
                    current_state=_state(),
                    core_memory=_core(),
                    scene_summaries=[],
                    standby_director_npcs=candidates,
                    turn_index=2,
                    store=DirectorEventStore(),
                    reviewer=DirectorEventReviewer(MockLLM().build_gateway()),
                    selector=DirectorEventSelector(
                        _SpyGateway(selection.model_dump(mode="json"))
                    ),
                )

    def test_selection_schema_orders_fields_and_rejects_unknown_fields(self):
        properties = DirectorEventSelectionResult.model_json_schema()["properties"]

        assert list(properties) == [
            "current_story_direction",
            "reason",
            "title",
            "director_npc_id",
            "actors",
            "objective",
            "bridge_anchors",
            "milestones",
            "milestone_interval_days",
            "premise",
            "entry_points",
        ]
        with pytest.raises(ValueError):
            DirectorEventSelectionResult.model_validate({
                **_selection_result().model_dump(mode="json"),
                "unexpected_field": "不应接受",
            })


class TestDirectorEventSelector:
    def test_arc_and_ambient_prompts_share_structure_but_split_direction_rules(self):
        arc = Path(
            "app/prompts/confirmation/director_event_select_arc_v1.md"
        ).read_text(encoding="utf-8")
        ambient = Path(
            "app/prompts/confirmation/director_event_select_ambient_v1.md"
        ).read_text(encoding="utf-8")

        arc_headers = [line for line in arc.splitlines() if line.startswith("#")]
        ambient_headers = [line for line in ambient.splitlines() if line.startswith("#")]
        assert arc_headers == [
            header
            for header in ambient_headers
            if header != "## Standby DirectorNPC（可选行动方）"
        ]
        for field in ("current_story_direction：", "reason：", "title：", "premise："):
            assert field in arc
            assert field in ambient
        assert arc.index("milestone_interval_days：") < arc.index("premise：")
        assert ambient.index("milestone_interval_days：") < ambient.index("premise：")
        assert "直接使用已有主线的地点、人物、物品、组织或设定作为锚点" in arc
        assert "横向补全（必须做）" in arc
        assert "平行原则" in arc
        assert "绝对不能成为完成主线任务的前提条件" in arc
        assert "严禁叙事螺旋与任务链化" in arc
        assert "不能生成的内容" in ambient
        assert "与当前剧情走向因果独立" in ambient
        assert "DirectorNPC" not in arc
        assert "{standby_director_npcs_json}" not in arc
        assert "{standby_director_npcs_json}" in ambient

    def test_selector_returns_flat_required_event_fields(self):
        selector_prompt = Path(
            "app/prompts/confirmation/director_event_select_ambient_v1.md"
        ).read_text(encoding="utf-8")
        assert "你没有拒绝输出的权限" in selector_prompt
        assert "新行动方与外部局势" in selector_prompt
        assert "action：" not in selector_prompt
        assert "new_event" not in selector_prompt

    def test_selector_puts_story_direction_before_reason(self):
        selector_prompt = Path(
            "app/prompts/confirmation/director_event_select_ambient_v1.md"
        ).read_text(encoding="utf-8")
        output_section = selector_prompt.split("按以下顺序为每个字段产出期望内容：", 1)[1]

        assert output_section.index("current_story_direction") < output_section.index("reason：")
        assert "不是复述全部历史" in selector_prompt
        assert "必须以“不能生成的内容：”开头" in selector_prompt
        assert "先列出本轮禁区" in selector_prompt

    def test_selector_excludes_current_direction_before_creating_external_event(self):
        selector_prompt = Path(
            "app/prompts/confirmation/director_event_select_ambient_v1.md"
        ).read_text(encoding="utf-8")

        assert "禁止内容必须覆盖该剧情走向中的核心人物目标" in selector_prompt
        assert "不得将禁止事件作为出发点" in selector_prompt
        assert "已经提出但尚未完成的计划" in selector_prompt
        assert "至少包含一个具有自主目标和行动能力" in selector_prompt
        assert "encounter_potential 允许" in selector_prompt
        assert "它可以让尚未出场的行动方" in selector_prompt
        assert "不需要与当前剧情已有直接联系" in selector_prompt
        assert "不得因为“尚未见面”或“当前不在同一建筑”直接拒绝使用" in selector_prompt
        assert "不得改变 NPC 的身份、动机、关系" in selector_prompt
        assert "把要素强行嫁接给该 NPC" in selector_prompt
        assert "不得使用 current_story_direction 已列入禁区的核心内容" in selector_prompt
        assert "trigger_anchors 必须来自 bridge_anchors" in selector_prompt
        assert "不得另造与事件无关的中间人或巧合链条" in selector_prompt

    def test_milestones_are_state_checkpoints_not_scripts(self):
        selector_prompt = Path(
            "app/prompts/confirmation/director_event_select_ambient_v1.md"
        ).read_text(encoding="utf-8")
        reviewer_prompt = Path(
            "app/prompts/confirmation/director_event_review_v1.md"
        ).read_text(encoding="utf-8")

        assert "世界状态检查点" in selector_prompt
        assert "不得限定唯一执行者、唯一方法" in selector_prompt
        assert "玩家推动下一状态成立应 advance" in reviewer_prompt
        assert "不受 progress_index 限制" in reviewer_prompt
        assert "不得把“玩家介入”本身作为 resolve 依据" in reviewer_prompt

    def test_selector_receives_event_list_and_standby_without_scene_text(self):
        spy = _SpyGateway(_selection_response())
        DirectorEventSelector(spy).select_or_create(
            role="ambient",
            events=[_event(status="resolved", role="arc"), _event(role="ambient")],
            standby_director_npcs=[_standby_npc()],
            current_state=_state(),
            core_memory=_core(),
        )

        input_data = spy.calls[0]["input_data"]
        events = json.loads(input_data["events_json"])
        standby_npcs = json.loads(input_data["standby_director_npcs_json"])

        assert [(event["role"], event["status"]) for event in events] == [
            ("arc", "resolved"),
            ("ambient", "active"),
        ]
        assert events[0]["entry_points"]
        assert events[0]["exposure"] == "hidden"
        assert standby_npcs[0]["hidden_secret"]
        assert "recent_summaries_json" in input_data
        assert "entity_profiles_json" in input_data
        assert "npc_profiles_json" in input_data
        assert "scene_text" not in input_data
        assert "scene_summaries_json" not in input_data

    def test_arc_selector_does_not_receive_standby_director_npcs(self):
        spy = _SpyGateway(_selection_response())
        DirectorEventSelector(spy).select_or_create(
            role="arc",
            events=[],
            standby_director_npcs=[_standby_npc()],
            current_state=_state(),
            core_memory=_core(),
        )

        assert "standby_director_npcs_json" not in spy.calls[0]["input_data"]
