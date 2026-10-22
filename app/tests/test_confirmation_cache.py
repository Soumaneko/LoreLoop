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

    def test_preload_never_admitted(self):
        data: dict = {}
        ConfirmationCache(data).admit(
            {
                "scene_facts": "facts",
                "director_npc_profile_ids": ["npc_1"],
            },
            write_db=True,
        )
        assert data == {"scene_facts": "facts"}


class TestBegin:
    def test_scene_text_change_clears_cache_and_sets_key(self):
        data = {
            "key": "old",
            "scene_facts": "facts",
        }
        cache = ConfirmationCache(data)
        cache.begin("new scene")
        assert data == {"key": "new scene"}

    def test_same_scene_text_is_a_no_op(self):
        data = {"key": "scene", "scene_facts": "facts"}
        ConfirmationCache(data).begin("scene")
        assert data == {"key": "scene", "scene_facts": "facts"}


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


def test_wrapper_is_a_view_over_the_raw_dict():
    raw: dict = {"key": "scene"}
    cache = ConfirmationCache(raw)
    cache.set_result("scene_facts", "facts")
    assert raw == {"key": "scene", "scene_facts": "facts"}


class TestRegistryEnforcement:
    def test_set_result_unknown_slot_raises(self):
        with pytest.raises(ValueError, match="unknown confirmation cache slot"):
            ConfirmationCache({}).set_result("typo_slot", 1)


    def test_registered_slots_still_write(self):
        data: dict = {}
        cache = ConfirmationCache(data)
        cache.set_result("scene_facts", "facts")
        cache.set_result("director_decision", "decision")
        cache.set_result("director_npc_profile_ids", ["npc_1"])
        assert data == {
            "scene_facts": "facts",
            "director_decision": "decision",
            "director_npc_profile_ids": ["npc_1"],
        }
