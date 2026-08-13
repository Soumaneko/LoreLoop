import pytest

from app.graph.confirmation_cache import ConfirmationCache


class TestResultLifecycle:
    def test_produced_distinguishes_empty_value_from_missing(self):
        cache = ConfirmationCache({"updated_npc_profile_ids": []})
        assert cache.produced("updated_npc_profile_ids") is True
        assert cache.result("updated_npc_profile_ids") == []
        assert cache.produced("missing") is False
        assert cache.result("missing") is None

    def test_compute_if_missing_runs_factory_once(self):
        data: dict = {}
        cache = ConfirmationCache(data)
        calls = []

        def factory():
            calls.append("compute")
            return "value"

        assert cache.compute_if_missing("scene_facts", factory) == "value"
        assert cache.compute_if_missing("scene_facts", factory) == "value"
        assert calls == ["compute"]

    def test_atomic_recomputes_everything_when_partial(self):
        data = {"director_decision": "decision"}
        cache = ConfirmationCache(data)
        calls = []

        def factory():
            calls.append("compute")
            return ("decision2", "executions2")

        assert cache.atomic(
            "director_decision",
            "director_executions",
            factory=factory,
        ) == ("decision2", "executions2")
        assert data["director_executions"] == "executions2"
        assert calls == ["compute"]

    def test_atomic_reuses_complete_pair(self):
        data = {"director_decision": "d", "director_executions": "e"}
        cache = ConfirmationCache(data)
        calls = []

        def factory():
            calls.append("compute")
            return ("d2", "e2")

        assert cache.atomic(
            "director_decision",
            "director_executions",
            factory=factory,
        ) == ("d", "e")
        assert calls == []


class TestAdmit:
    def test_always_results_admitted_without_write(self):
        data: dict = {}
        ConfirmationCache(data).admit(
            {"scene_facts": "facts", "npc_turn_result": "npc"},
            write_db=False,
        )
        assert data == {"scene_facts": "facts"}

    def test_write_results_admitted_only_for_write(self):
        data: dict = {}
        ConfirmationCache(data).admit(
            {"scene_facts": "facts", "npc_turn_result": "npc"},
            write_db=True,
        )
        assert data == {"scene_facts": "facts", "npc_turn_result": "npc"}

    def test_preload_and_markers_never_admitted(self):
        data: dict = {}
        ConfirmationCache(data).admit(
            {
                "scene_facts": "facts",
                "director_npc_profile_ids": ["npc_1"],
                "state_saved": True,
            },
            write_db=True,
        )
        assert data == {"scene_facts": "facts"}


class TestBegin:
    def test_scene_text_change_clears_cache_and_sets_key(self):
        data = {
            "key": "old",
            "scene_facts": "facts",
            "state_saved": True,
        }
        cache = ConfirmationCache(data)
        cache.begin("new scene")
        assert data == {"key": "new scene"}

    def test_same_scene_text_is_a_no_op(self):
        data = {"key": "scene", "scene_facts": "facts", "state_saved": True}
        ConfirmationCache(data).begin("scene")
        assert data == {"key": "scene", "scene_facts": "facts", "state_saved": True}


class TestRollbackReset:
    def test_keeps_pure_results_and_key(self):
        marker = object()
        data = {
            "key": "scene",
            "scene_facts": marker,
            "updated_state": marker,
            "state_change_set": marker,
            "state_update_warnings": marker,
            "turn_summary": marker,
            "action_preview": marker,
            "continuity_projection": marker,
            "npc_turn_result": marker,
            "director_decision": marker,
            "director_executions": marker,
            "director_event_reviews": marker,
            "recorded_setting_ids": ["setting_1"],
            "director_npc_profile_ids": ["npc_2"],
            "confirmed_director_npc_drafts": marker,
            "existing_npc_profiles": marker,
            "state_saved": True,
            "turn_summary_saved": True,
            "short_term_saved": True,
            "confirmed_history_saved": True,
            "npc_field_logs_saved": True,
            "scene_summary_scheduled": True,
        }
        ConfirmationCache(data).reset_for_rollback()
        assert data == {
            "key": "scene",
            "scene_facts": marker,
            "updated_state": marker,
            "state_change_set": marker,
            "state_update_warnings": marker,
            "turn_summary": marker,
            "action_preview": marker,
            "continuity_projection": marker,
        }

    def test_empty_cache_resets_cleanly(self):
        data: dict = {}
        ConfirmationCache(data).reset_for_rollback()
        assert data == {}


class TestCompletionGuards:
    def test_completed_and_mark_completed(self):
        data: dict = {}
        cache = ConfirmationCache(data)
        assert cache.completed("state_saved") is False
        cache.mark_completed("state_saved")
        assert cache.completed("state_saved") is True
        assert data == {"state_saved": True}


def test_wrapper_is_a_view_over_the_raw_dict():
    raw: dict = {"key": "scene"}
    cache = ConfirmationCache(raw)
    cache.set_result("scene_facts", "facts")
    cache.mark_completed("state_saved")
    assert raw == {"key": "scene", "scene_facts": "facts", "state_saved": True}


class TestRegistryEnforcement:
    def test_set_result_unknown_slot_raises(self):
        with pytest.raises(ValueError, match="unknown confirmation cache slot"):
            ConfirmationCache({}).set_result("typo_slot", 1)

    def test_compute_if_missing_unknown_slot_raises(self):
        with pytest.raises(ValueError, match="unknown confirmation cache slot"):
            ConfirmationCache({}).compute_if_missing("typo_slot", lambda: 1)

    def test_atomic_unknown_slot_raises(self):
        with pytest.raises(ValueError, match="unknown confirmation cache slot"):
            ConfirmationCache({}).atomic("typo_slot", factory=lambda: (1,))

    def test_atomic_arity_mismatch_raises_and_leaves_cache_untouched(self):
        data: dict = {}
        with pytest.raises(ValueError, match="returned 1 values for 2 slots"):
            ConfirmationCache(data).atomic(
                "director_decision",
                "director_executions",
                factory=lambda: ("only-one",),
            )
        assert data == {}

    def test_atomic_arity_overflow_raises_and_leaves_cache_untouched(self):
        data: dict = {}
        with pytest.raises(ValueError, match="returned 3 values for 2 slots"):
            ConfirmationCache(data).atomic(
                "director_decision",
                "director_executions",
                factory=lambda: ("a", "b", "c"),
            )
        assert data == {}

    def test_atomic_no_slots_raises(self):
        with pytest.raises(ValueError, match="at least one cache slot"):
            ConfirmationCache({}).atomic(factory=lambda: ())

    def test_atomic_duplicate_slots_raises_and_leaves_cache_untouched(self):
        data: dict = {}
        with pytest.raises(ValueError, match="slots must be unique"):
            ConfirmationCache(data).atomic("a", "a", factory=lambda: ("x", "y"))
        assert data == {}

    def test_completed_non_marker_raises(self):
        with pytest.raises(ValueError, match="completion marker"):
            ConfirmationCache({}).completed("scene_facts")

    def test_mark_completed_non_marker_raises(self):
        with pytest.raises(ValueError, match="completion marker"):
            ConfirmationCache({}).mark_completed("scene_facts")

    def test_registered_slots_still_write(self):
        data: dict = {}
        cache = ConfirmationCache(data)
        cache.set_result("scene_facts", "facts")
        cache.set_result("director_decision", "decision")
        cache.set_result("director_npc_profile_ids", ["npc_1"])
        cache.set_result("turn_summary_id", "summary_1")
        cache.mark_completed("state_saved")
        assert data == {
            "scene_facts": "facts",
            "director_decision": "decision",
            "director_npc_profile_ids": ["npc_1"],
            "turn_summary_id": "summary_1",
            "state_saved": True,
        }
