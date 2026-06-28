import json

import pytest

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director_event import (
    DirectorEvent,
    DirectorEventReviewResult,
    DirectorEventReviewer,
    DirectorEventSelector,
    DirectorEventStages,
    DirectorEventStore,
)
from app.domain.director_event.views import (
    plot_outline_director_event_instructions,
    project_director_event,
    turn_resolution_director_event_instructions,
)
from app.workflows.update_director_event import update_director_event_workflow


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="北郡", participants=["卢西安"]),
        player=PlayerState(name="卢西安", position="北郡"),
    )


def _core() -> CoreMemory:
    return CoreMemory(items=[])


def _stages() -> DirectorEventStages:
    return DirectorEventStages(
        latent="一名信使在北郡附近秘密失踪，绑架者正在检查其随身信件。",
        manifesting="信使失踪已导致北郡多条通信中断，守卫开始搜查道路和废弃建筑。",
        crisis="绑架者开始利用截获的信件袭击后续信使，北郡交通与通信陷入公开危机。",
    )


def _manifesting_hooks() -> list[str]:
    return [
        "找到并公开被截获信件的藏匿点",
        "说服旧磨坊看守交出绑架者路线",
        "切断绑架者继续截获信件的道路暗哨",
    ]


def _crisis_hooks() -> list[str]:
    return [
        "救出被用作诱饵的后续信使",
        "夺回能证明袭击命令来源的密信",
        "迫使绑架者首领在守卫面前暴露身份",
    ]


def _event(title="失踪的信使", status="active") -> DirectorEvent:
    return DirectorEvent(
        title=title,
        status=status,
        phase="latent",
        phase_turns_elapsed=0,
        stages=_stages(),
        related_npcs=["信使"],
        location_constraints=["北郡"],
        manifesting_prevention_hooks=_manifesting_hooks(),
        crisis_resolution_hooks=_crisis_hooks(),
        known_to_player=None,
        hidden_developments=None,
        created_turn=1,
        updated_turn=1,
    )


def _review_response(**updates):
    response = {
        "decision": "keep_active",
        "reason": "事件仍在发展。",
        "evidence": [],
        "suspended_reason": None,
        "phase_advance_recommended": False,
        "phase_advance_reason": None,
        "known_to_player_updates": None,
        "hidden_development_updates": None,
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


class TestDirectorEventStore:
    def test_new_event_starts_in_latent_phase(self):
        event = _event()

        assert event.phase == "latent"
        assert event.phase_turns_elapsed == 0
        assert event.known_to_player is None
        assert event.hidden_developments is None

    def test_only_one_active_event_and_suspension_preserves_phase(self):
        store = DirectorEventStore()
        first = _event("第一事件").model_copy(
            update={"phase": "manifesting", "phase_turns_elapsed": 2}
        )
        second = _event("第二事件")

        store.save(first)
        store.save(second)

        assert store.get_active().event_id == second.event_id
        suspended = store.list_suspended()
        assert len(suspended) == 1
        assert suspended[0].event_id == first.event_id
        assert suspended[0].phase == "manifesting"
        assert suspended[0].phase_turns_elapsed == 2

    def test_suspend_preserves_phase(self):
        store = DirectorEventStore()
        event = _event().model_copy(
            update={"phase": "manifesting", "phase_turns_elapsed": 1}
        )
        store.save(event)

        assert store.suspend(event.event_id, "暂时不可达", turn_index=2) is True

        suspended = store.get_by_id(event.event_id)
        assert suspended.status == "suspended"
        assert suspended.phase == "manifesting"
        assert suspended.phase_turns_elapsed == 1

    def test_suspended_cannot_archive_through_active_archive_api(self):
        store = DirectorEventStore()
        event = _event(status="suspended")
        store.save(event)

        assert store.archive_active(event.event_id, turn_index=2) is False
        assert store.get_by_id(event.event_id).status == "suspended"

    def test_dynamic_field_updates_merge_and_dedupe(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        store.update_dynamic_fields(
            event.event_id,
            known_to_player_updates=["主角听闻信使失踪", "主角听闻信使失踪", ""],
            hidden_development_updates=["绑架者转移信件"],
            turn_index=2,
        )
        store.update_dynamic_fields(
            event.event_id,
            known_to_player_updates=["主角听闻信使失踪", "主角发现北郡脚印"],
            hidden_development_updates=["绑架者转移信件", "信使被带往旧磨坊"],
            turn_index=3,
        )

        updated = store.get_by_id(event.event_id)
        assert updated.known_to_player == ["主角听闻信使失踪", "主角发现北郡脚印"]
        assert updated.hidden_developments == ["绑架者转移信件", "信使被带往旧磨坊"]

    def test_phase_operations_only_advance_one_way(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        assert store.increment_active_phase_turn(event.event_id, turn_index=2)
        assert store.get_active().phase_turns_elapsed == 1
        assert store.advance_active_phase(event.event_id, turn_index=2)
        assert store.get_active().phase == "manifesting"
        assert store.get_active().phase_turns_elapsed == 0
        assert store.advance_active_phase(event.event_id, turn_index=3)
        assert store.get_active().phase == "crisis"
        assert store.advance_active_phase(event.event_id, turn_index=4) is False


class TestDirectorEventReviewSchema:
    def test_advance_recommendation_requires_reason_and_evidence(self):
        with pytest.raises(ValueError):
            DirectorEventReviewResult.model_validate(
                _review_response(phase_advance_recommended=True)
            )

    def test_non_recommendation_rejects_reason(self):
        with pytest.raises(ValueError):
            DirectorEventReviewResult.model_validate(
                _review_response(phase_advance_reason="不应存在")
            )

    def test_review_result_requires_dynamic_update_fields(self):
        response = _review_response()
        del response["known_to_player_updates"]
        with pytest.raises(ValueError):
            DirectorEventReviewResult.model_validate(response)


class TestDirectorEventReviewerInput:
    def _review(self, event):
        spy = _SpyGateway(_review_response())
        DirectorEventReviewer(spy).review(
            active_event=event,
            user_input="我继续行动。",
            scene_text="卢西安完成了本轮行动。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )
        return json.loads(spy.calls[0]["input_data"]["active_event_json"])

    def test_reviewer_omits_known_to_player_in_crisis(self):
        event = _event().model_copy(update={
            "phase": "crisis",
            "known_to_player": ["主角已经知道的旧线索"],
        })

        sent = self._review(event)

        assert "known_to_player" not in sent
        assert sent["current_description"] == _stages().crisis

    def test_reviewer_keeps_known_to_player_before_crisis(self):
        event = _event().model_copy(update={
            "phase": "manifesting",
            "known_to_player": ["主角已经知道的旧线索"],
        })

        sent = self._review(event)

        assert sent["known_to_player"] == ["主角已经知道的旧线索"]


class TestDirectorEventViews:
    def test_semantic_view_only_exposes_current_stage_description(self):
        projected = json.loads(project_director_event(_event()))

        assert projected["phase"] == "latent"
        assert projected["current_description"] == _stages().latent
        assert "stages" not in projected

    def test_crisis_projection_omits_known_to_player(self):
        event = _event().model_copy(update={
            "phase": "crisis",
            "known_to_player": ["主角已经知道的旧线索"],
        })

        projected = json.loads(project_director_event(event))

        assert "known_to_player" not in projected
        assert projected["current_description"] == _stages().crisis

    def test_pre_crisis_projection_keeps_known_to_player(self):
        event = _event().model_copy(update={
            "phase": "manifesting",
            "known_to_player": ["主角已经知道的旧线索"],
        })

        projected = json.loads(project_director_event(event))

        assert projected["known_to_player"] == ["主角已经知道的旧线索"]

    def test_recent_archived_projection_uses_resolution_hook_json(self):
        event = _event().model_copy(
            update={
                "status": "archived",
                "resolved_by_hook": _crisis_hooks()[0],
                "resolved_reason": "诱饵信使已获救。",
                "resolved_turn": 4,
                "updated_turn": 4,
            }
        )

        projected = json.loads(project_director_event(None, recent_archived_event=event))

        assert projected["status"] == "recently_archived"
        assert projected["resolved_by_hook"] == _crisis_hooks()[0]
        assert projected["message"] == f"由于{_crisis_hooks()[0]}，『失踪的信使』事件已收束。"

    def test_projection_hides_hooks_while_latent(self):
        projected = json.loads(project_director_event(_event()))

        assert "manifesting_prevention_hooks" not in projected
        assert "crisis_resolution_hooks" not in projected

    def test_projection_exposes_only_manifesting_hooks(self):
        event = _event().model_copy(update={"phase": "manifesting"})

        projected = json.loads(project_director_event(event))

        assert projected["manifesting_prevention_hooks"] == _manifesting_hooks()
        assert "crisis_resolution_hooks" not in projected

    def test_projection_exposes_only_crisis_hooks(self):
        event = _event().model_copy(update={"phase": "crisis"})

        projected = json.loads(project_director_event(event))

        assert "manifesting_prevention_hooks" not in projected
        assert projected["crisis_resolution_hooks"] == _crisis_hooks()

    def test_latent_instructions_are_optional_and_relevance_based(self):
        plot = plot_outline_director_event_instructions(_event())
        resolution = turn_resolution_director_event_instructions(_event())

        assert "没有自然交点时可以不使用" in plot
        assert "否则不要使用" in resolution

    def test_manifesting_instructions_focus_on_world_effects(self):
        event = _event().model_copy(update={"phase": "manifesting"})

        assert "世界影响" in plot_outline_director_event_instructions(event)
        assert "不要退回到重复投放神秘线索" in turn_resolution_director_event_instructions(event)

    def test_crisis_instructions_require_real_impact(self):
        event = _event().model_copy(update={"phase": "crisis"})

        assert "可操作抓手" in plot_outline_director_event_instructions(event)
        assert "必须考虑危机" in turn_resolution_director_event_instructions(event)

    def test_missing_event_gets_no_prompt_instructions(self):
        assert plot_outline_director_event_instructions(None) == ""
        assert turn_resolution_director_event_instructions(None) == ""


class TestDirectorEventWorkflow:
    def test_reviews_active_and_archives_without_advancing_phase(self):
        store = DirectorEventStore()
        active = _event()
        store.save(active)

        result = _run_review(
            store,
            _review_response(
                decision="archive_active",
                reason="信使已经被救出。",
                evidence=["信使被安全带回北郡"],
                known_to_player_updates=["主角知道信使已获救"],
                hidden_development_updates=["绑架者失去信使筹码"],
            ),
        )

        assert result.review_result.decision == "archive_active"
        archived = store.get_by_id(active.event_id)
        assert archived.status == "archived"
        assert archived.phase_turns_elapsed == 0
        assert archived.known_to_player == ["主角知道信使已获救"]

    def test_keep_active_updates_dynamic_fields_and_turn_counter(self):
        store = DirectorEventStore()
        active = _event()
        store.save(active)

        _run_review(
            store,
            _review_response(
                evidence=["卢西安听见有人提到失踪信使"],
                known_to_player_updates=["主角听闻北郡信使失踪"],
                hidden_development_updates=["幕后者开始转移信使随身信件"],
            ),
        )

        active_after = store.get_active()
        assert active_after.phase == "latent"
        assert active_after.phase_turns_elapsed == 1
        assert active_after.known_to_player == ["主角听闻北郡信使失踪"]

    def test_program_advances_latent_after_four_active_turns(self):
        store = DirectorEventStore()
        active = _event().model_copy(update={"phase_turns_elapsed": 3})
        store.save(active)

        result = _run_review(store, _review_response(), turn_index=5)

        assert store.get_active().phase == "manifesting"
        assert store.get_active().phase_turns_elapsed == 0
        assert "phase_advanced_by_turn_limit" in result.notes

    def test_program_advances_manifesting_after_three_active_turns(self):
        store = DirectorEventStore()
        active = _event().model_copy(
            update={"phase": "manifesting", "phase_turns_elapsed": 2}
        )
        store.save(active)

        _run_review(store, _review_response(), turn_index=6)

        assert store.get_active().phase == "crisis"
        assert store.get_active().phase_turns_elapsed == 0

    def test_reviewer_can_recommend_early_single_step_advance(self):
        store = DirectorEventStore()
        store.save(_event())

        result = _run_review(
            store,
            _review_response(
                phase_advance_recommended=True,
                phase_advance_reason="绑架者已公开袭击第二名信使，秘密行动无法维持。",
                evidence=["第二名信使在北郡大道遭到公开袭击"],
            ),
        )

        assert store.get_active().phase == "manifesting"
        assert "phase_advanced_by_reviewer" in result.notes

    def test_manifesting_hook_archives_event_before_crisis(self):
        store = DirectorEventStore()
        active = _event().model_copy(update={"phase": "manifesting", "phase_turns_elapsed": 2})
        store.save(active)

        result = _run_review(
            store,
            _review_response(
                reason="主角已经切断绑架者继续截信的暗哨，事件不会进入公开危机。",
                evidence=["卢西安拆除了道路暗哨，并把暗哨交给守卫"],
                hidden_development_updates=["绑架者失去继续截获信件的前置据点"],
                matched_manifesting_prevention_hook=_manifesting_hooks()[2],
            ),
            turn_index=6,
        )

        archived = store.get_by_id(active.event_id)
        assert archived.status == "archived"
        assert archived.resolved_by_hook == _manifesting_hooks()[2]
        assert archived.resolved_reason == result.review_result.reason
        assert archived.resolved_turn == 6
        assert "archived_by_manifesting_prevention_hook" in result.notes

    def test_crisis_hook_archives_event(self):
        store = DirectorEventStore()
        active = _event().model_copy(update={"phase": "crisis", "phase_turns_elapsed": 1})
        store.save(active)

        result = _run_review(
            store,
            _review_response(
                reason="主角救出了作为诱饵的信使，危机失去继续扩大的核心条件。",
                evidence=["卢西安把后续信使从伏击圈中救出"],
                hidden_development_updates=["绑架者的诱饵计划失败，交通危机开始收束"],
                matched_crisis_resolution_hook=_crisis_hooks()[0],
            ),
            turn_index=7,
        )

        archived = store.get_by_id(active.event_id)
        assert archived.status == "archived"
        assert "archived_by_crisis_resolution_hook" in result.notes

    def test_no_active_event_enters_three_turn_cooldown_after_archive(self):
        store = DirectorEventStore()
        active = _event()
        store.save(active)
        store.archive_active(
            active.event_id,
            turn_index=4,
            resolved_by_hook=_crisis_hooks()[0],
            resolved_reason="诱饵信使已获救。",
        )
        gateway = _SpyGateway(
            {
                "action": "create_new",
                "suspension_evaluation": "无暂停事件可自然衔接当前局势，进入新事件构思。",
                "backstage_undercurrent": "一支隐秘的走私团伙正在暗中囤积军械。",
                "player_connection": "走私活动波及主角当前所在区域的补给线。",
                "reason": "冷却结束后创建新事件。",
                "activate_event_id": None,
                "new_event": {
                    "title": "钟楼下的旧教团",
                    "stages": _stages().model_dump(),
                    "related_npcs": ["旧教团残党"],
                    "location_constraints": ["北郡钟楼"],
                    "manifesting_prevention_hooks": _manifesting_hooks(),
                    "crisis_resolution_hooks": _crisis_hooks(),
                },
            }
        )

        cooling = update_director_event_workflow(
            user_input="继续。",
            scene_text="卢西安继续前进。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=7,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert cooling.mode == "cooldown"
        assert store.get_active() is None
        assert gateway.calls == []

        created = update_director_event_workflow(
            user_input="继续。",
            scene_text="卢西安继续前进。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=8,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert created.mode == "select_or_create"
        assert store.get_active().title == "钟楼下的旧教团"

    def test_any_matched_hook_archives_even_if_phase_is_unexpected(self):
        store = DirectorEventStore()
        active = _event().model_copy(update={"phase": "latent", "phase_turns_elapsed": 1})
        store.save(active)

        _run_review(
            store,
            _review_response(
                reason="模型判断玩家行动已经满足事件阻断抓手。",
                evidence=["卢西安公开了被截获信件的藏匿点"],
                matched_manifesting_prevention_hook=_manifesting_hooks()[0],
            ),
            turn_index=4,
        )

        archived = store.get_by_id(active.event_id)
        assert archived.status == "archived"

    def test_selects_suspended_event_when_no_active(self):
        store = DirectorEventStore()
        suspended = _event(status="suspended")
        store.save(suspended)
        gateway = _SpyGateway(
            {
                "action": "activate_suspended",
                "suspension_evaluation": "暂停事件可自然衔接当前局势，决定重新激活。",
                "reason": "主角回到北郡，事件重新适合当前剧情。",
                "activate_event_id": suspended.event_id,
                "new_event": None,
            }
        )

        update_director_event_workflow(
            user_input="我回到北郡看看。",
            scene_text="卢西安回到北郡。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=5,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert store.get_active().event_id == suspended.event_id

    def test_rejects_unknown_suspended_event_id(self):
        store = DirectorEventStore()
        gateway = _SpyGateway(
            {
                "action": "activate_suspended",
                "suspension_evaluation": "无激活理由。",
                "reason": "错误引用。",
                "activate_event_id": "missing_event",
                "new_event": None,
            }
        )

        with pytest.raises(ValueError, match="not suspended"):
            update_director_event_workflow(
                user_input="继续。",
                scene_text="卢西安继续前进。",
                current_state=_state(),
                core_memory=_core(),
                scene_summaries=[],
                turn_index=3,
                store=store,
                reviewer=DirectorEventReviewer(gateway),
                selector=DirectorEventSelector(gateway),
            )

    def test_creates_new_three_stage_event_when_no_active(self):
        store = DirectorEventStore()
        gateway = _SpyGateway(
            {
                "action": "create_new",
                "suspension_evaluation": "无暂停事件可自然衔接当前局势，进入新事件构思。",
                "backstage_undercurrent": "一支隐秘的走私团伙正在暗中囤积军械。",
                "player_connection": "走私活动波及主角当前所在区域的补给线。",
                "reason": "当前剧情需要一个与北郡相关的新后台事件。",
                "activate_event_id": None,
                "new_event": {
                    "title": "钟楼下的旧教团",
                    "stages": _stages().model_dump(),
                    "related_npcs": ["旧教团残党"],
                    "location_constraints": ["北郡钟楼"],
                    "manifesting_prevention_hooks": _manifesting_hooks(),
                    "crisis_resolution_hooks": _crisis_hooks(),
                },
            }
        )

        update_director_event_workflow(
            user_input="我在修道院附近停留。",
            scene_text="卢西安在修道院附近停留。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=1,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        active = store.get_active()
        assert active.phase == "latent"
        assert active.stages.crisis == _stages().crisis
        assert active.manifesting_prevention_hooks == _manifesting_hooks()
        assert active.crisis_resolution_hooks == _crisis_hooks()


class TestDirectorEventAgents:
    def test_reviewer_includes_user_input(self):
        gateway = _SpyGateway(_review_response(evidence=["玩家购买装备"]))

        DirectorEventReviewer(gateway).review(
            active_event=_event(),
            user_input="我先买些绷带，再继续追查信使。",
            scene_text="卢西安购买了绷带。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        assert gateway.calls[0]["input_data"]["user_input"] == "我先买些绷带，再继续追查信使。"

    def test_reviewer_only_receives_current_stage_description(self):
        gateway = _SpyGateway(_review_response())
        event = _event().model_copy(update={"phase": "manifesting"})

        DirectorEventReviewer(gateway).review(
            active_event=event,
            user_input="我继续调查。",
            scene_text="卢西安检查了道路。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        active_event_json = json.loads(
            gateway.calls[0]["input_data"]["active_event_json"]
        )
        assert active_event_json["phase"] == "manifesting"
        assert active_event_json["current_description"] == event.stages.manifesting
        assert "stages" not in active_event_json
        assert event.stages.crisis not in gateway.calls[0]["input_data"]["active_event_json"]

    def test_selector_includes_archived_events_for_dedupe(self):
        archived = _event(title="旧修道院足迹", status="archived")
        gateway = _SpyGateway(
            {
                "action": "create_new",
                "suspension_evaluation": "无暂停事件可自然衔接当前局势，进入新事件构思。",
                "backstage_undercurrent": "一名流亡贵族正在暗中招募旧部，图谋复辟。",
                "player_connection": "流亡贵族的招募活动延伸到主角所在的北郡钟楼一带。",
                "reason": "当前剧情需要一个不重复的新后台事件。",
                "activate_event_id": None,
                "new_event": {
                    "title": "钟楼上的黑旗",
                    "stages": _stages().model_dump(),
                    "related_npcs": ["旧教团残党"],
                    "location_constraints": ["北郡钟楼"],
                    "manifesting_prevention_hooks": _manifesting_hooks(),
                    "crisis_resolution_hooks": _crisis_hooks(),
                },
            }
        )

        DirectorEventSelector(gateway).select_or_create(
            suspended_events=[],
            archived_events=[archived],
            scene_text="卢西安来到北郡钟楼附近。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        assert "旧修道院足迹" in gateway.calls[0]["input_data"]["archived_events_json"]
