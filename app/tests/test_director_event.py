import json

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director_event import (
    DirectorEvent,
    DirectorEventReviewResult,
    DirectorEventReviewer,
    DirectorEventSelector,
    DirectorEventStore,
)
from app.domain.director_event.views import (
    plot_outline_director_event_instructions,
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


def _event(title="失踪的信使", status="active") -> DirectorEvent:
    return DirectorEvent(
        title=title,
        status=status,
        exposure_phase="hidden",
        description="一名信使在北郡附近失踪，相关线索会自然牵动主角。",
        related_npcs=["信使"],
        location_constraints=["北郡"],
        known_to_player=None,
        hidden_developments=None,
        created_turn=1,
        updated_turn=1,
    )


class _SpyGateway:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


class TestDirectorEventStore:
    def test_new_event_dynamic_fields_start_null(self):
        event = _event()

        assert event.exposure_phase == "hidden"
        assert event.known_to_player is None
        assert event.hidden_developments is None

    def test_only_one_active_event(self):
        store = DirectorEventStore()
        first = _event("第一事件")
        first = first.model_copy(update={"exposure_phase": "engaged"})
        second = _event("第二事件")

        store.save(first)
        store.save(second)

        assert store.get_active().event_id == second.event_id
        suspended = store.list_suspended()
        assert len(suspended) == 1
        assert suspended[0].event_id == first.event_id
        assert suspended[0].exposure_phase == "hidden"

    def test_suspended_cannot_archive_through_active_archive_api(self):
        store = DirectorEventStore()
        event = _event(status="suspended")
        store.save(event)

        assert store.archive_active(event.event_id, turn_index=2) is False
        assert store.get_by_id(event.event_id).status == "suspended"

    def test_suspend_resets_exposure_phase_to_hidden(self):
        store = DirectorEventStore()
        event = _event().model_copy(update={"exposure_phase": "engaged"})
        store.save(event)

        assert store.suspend(event.event_id, "暂时不可达", turn_index=2) is True

        suspended = store.get_by_id(event.event_id)
        assert suspended.status == "suspended"
        assert suspended.exposure_phase == "hidden"

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

    def test_dynamic_field_updates_can_change_exposure_phase(self):
        store = DirectorEventStore()
        event = _event()
        store.save(event)

        store.update_dynamic_fields(
            event.event_id,
            exposure_phase_update="engaged",
            known_to_player_updates=["主角确认信使失踪和旧磨坊有关"],
            turn_index=2,
        )

        updated = store.get_by_id(event.event_id)
        assert updated.exposure_phase == "engaged"
        assert updated.known_to_player == ["主角确认信使失踪和旧磨坊有关"]

    def test_review_result_rejects_hidden_exposure_update(self):
        try:
            DirectorEventReviewResult.model_validate({
                "decision": "keep_active",
                "reason": "不能回退。",
                "evidence": [],
                "suspended_reason": None,
                "exposure_phase_update": "hidden",
            })
        except ValueError:
            return
        raise AssertionError("hidden exposure_phase_update should be rejected")

    def test_review_result_requires_dynamic_update_fields(self):
        try:
            DirectorEventReviewResult.model_validate({
                "decision": "keep_active",
                "reason": "旧格式缺少动态字段。",
                "evidence": [],
                "suspended_reason": None,
            })
        except ValueError:
            return
        raise AssertionError("review result should require dynamic update fields")


class TestDirectorEventViews:
    def test_hidden_active_event_gets_prompt_instructions(self):
        event = _event()

        assert "本回合必须" in plot_outline_director_event_instructions(event)
        assert "本回合必须使用它" in turn_resolution_director_event_instructions(event)

    def test_engaged_or_missing_event_gets_no_prompt_instructions(self):
        event = _event().model_copy(update={"exposure_phase": "engaged"})

        assert plot_outline_director_event_instructions(event) == ""
        assert turn_resolution_director_event_instructions(event) == ""
        assert plot_outline_director_event_instructions(None) == ""
        assert turn_resolution_director_event_instructions(None) == ""


class TestDirectorEventWorkflow:
    def test_reviews_active_and_archives(self):
        store = DirectorEventStore()
        active = _event()
        store.save(active)
        mock = MockLLM()
        mock.set_responses([
            json.dumps({
                "decision": "archive_active",
                "reason": "信使已经被救出。",
                "evidence": ["信使被安全带回北郡"],
                "suspended_reason": None,
                "exposure_phase_update": None,
                "known_to_player_updates": ["主角知道信使已获救"],
                "hidden_development_updates": ["绑架者失去信使筹码"],
            }, ensure_ascii=False)
        ])
        gateway = mock.build_gateway()

        result = update_director_event_workflow(
            user_input="我护送信使回北郡。",
            scene_text="信使被安全带回北郡。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert result.mode == "review_active"
        assert result.review_result.decision == "archive_active"
        assert store.get_active() is None
        archived = store.get_by_id(active.event_id)
        assert archived.status == "archived"
        assert archived.known_to_player == ["主角知道信使已获救"]
        assert archived.hidden_developments == ["绑架者失去信使筹码"]

    def test_reviews_active_and_updates_dynamic_fields(self):
        store = DirectorEventStore()
        active = _event()
        store.save(active)
        mock = MockLLM()
        mock.set_responses([
            json.dumps({
                "decision": "keep_active",
                "reason": "事件线索被主角注意到，但尚未解决。",
                "evidence": ["卢西安听见有人提到失踪信使"],
                "suspended_reason": None,
                "exposure_phase_update": "engaged",
                "known_to_player_updates": ["主角听闻北郡信使失踪"],
                "hidden_development_updates": ["幕后者开始转移信使随身信件"],
            }, ensure_ascii=False)
        ])
        gateway = mock.build_gateway()

        result = update_director_event_workflow(
            user_input="我打听信使的消息。",
            scene_text="卢西安听见有人提到失踪信使。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=2,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert result.mode == "review_active"
        active_after = store.get_active()
        assert active_after.exposure_phase == "engaged"
        assert active_after.known_to_player == ["主角听闻北郡信使失踪"]
        assert active_after.hidden_developments == ["幕后者开始转移信使随身信件"]

    def test_selects_suspended_event_when_no_active(self):
        store = DirectorEventStore()
        suspended = _event(status="suspended")
        store.save(suspended)
        mock = MockLLM()
        mock.set_responses([
            json.dumps({
                "action": "activate_suspended",
                "reason": "主角回到了北郡，失踪信使事件重新适合当前剧情。",
                "activate_event_id": suspended.event_id,
                "new_event": None,
            }, ensure_ascii=False)
        ])
        gateway = mock.build_gateway()

        result = update_director_event_workflow(
            user_input="我回到北郡看看。",
            scene_text="卢西安回到北郡。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
            turn_index=3,
            store=store,
            reviewer=DirectorEventReviewer(gateway),
            selector=DirectorEventSelector(gateway),
        )

        assert result.mode == "select_or_create"
        assert store.get_active().event_id == suspended.event_id

    def test_creates_new_event_when_no_active(self):
        store = DirectorEventStore()
        mock = MockLLM()
        mock.set_responses([
            json.dumps({
                "action": "create_new",
                "reason": "当前剧情需要一个与北郡相关的后台事件。",
                "activate_event_id": None,
                "new_event": {
                    "title": "修道院外的陌生足迹",
                    "description": "北郡修道院外出现陌生足迹，牵出有人暗中接近主角的事件。",
                    "related_npcs": ["陌生人"],
                    "location_constraints": ["北郡修道院"],
                },
            }, ensure_ascii=False)
        ])
        gateway = mock.build_gateway()

        result = update_director_event_workflow(
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

        assert result.mode == "select_or_create"
        active = store.get_active()
        assert active.title == "修道院外的陌生足迹"
        assert active.created_turn == 1


class TestDirectorEventAgents:
    def test_reviewer_includes_user_input(self):
        gateway = _SpyGateway({
            "decision": "keep_active",
            "reason": "玩家只是在准备。",
            "evidence": ["玩家购买装备"],
            "suspended_reason": None,
            "exposure_phase_update": None,
            "known_to_player_updates": None,
            "hidden_development_updates": None,
        })

        DirectorEventReviewer(gateway).review(
            active_event=_event(),
            user_input="我先买些绷带，再继续追查信使。",
            scene_text="卢西安购买了绷带。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        input_data = gateway.calls[0]["input_data"]
        assert input_data["user_input"] == "我先买些绷带，再继续追查信使。"

    def test_selector_includes_archived_events_for_dedupe(self):
        archived = _event(title="旧修道院足迹", status="archived")
        gateway = _SpyGateway({
            "action": "create_new",
            "reason": "当前剧情需要一个不重复的新后台事件。",
            "activate_event_id": None,
            "new_event": {
                "title": "钟楼上的黑旗",
                "description": "钟楼上出现黑旗，牵出有人暗中召集旧教团残党的事件。",
                "related_npcs": ["旧教团残党"],
                "location_constraints": ["北郡钟楼"],
            },
        })

        DirectorEventSelector(gateway).select_or_create(
            suspended_events=[],
            archived_events=[archived],
            scene_text="卢西安来到北郡钟楼附近。",
            current_state=_state(),
            core_memory=_core(),
            scene_summaries=[],
        )

        input_data = gateway.calls[0]["input_data"]
        assert "旧修道院足迹" in input_data["archived_events_json"]
