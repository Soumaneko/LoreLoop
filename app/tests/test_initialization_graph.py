import pytest

from app.graph import initialization_graph
from app.runtime.initialization_draft import (
    load_initialization_draft,
    save_initialization_draft,
)
from app.runtime.session import get_campaign_runtime


def _payload():
    return {
        "config": {
            "version": 3,
            "director_enabled": True,
            "world_name": "艾兰",
            "world_style": "西幻",
            "ability_system": "魔法",
            "protagonist_name": "卢西安",
            "story_expectation": "探索、成长并建立真实关系。",
            "failure_severity": "适中",
        },
        "campaign_setup": {
            "version": 1,
            "currency_unit": "金币",
            "public_power_baseline": {
                "source": "魔法",
                "prevalence": "少数人掌握",
                "learning_difficulty": "困难",
                "limitation": "不能复活死者。",
            },
        },
    }


def _foundation_payload():
    return {
        "version": 1,
        "starting_location": {
            "name": "白塔港",
            "description": "一座依山而建的贸易港。",
        },
        "protagonist_opening_situation": (
            "卢西安刚抵达白塔港，登记大厅停止办理入城手续。"
        ),
        "initial_resources": {
            "inventory": [{"name": "旅行包", "quantity": 1}],
            "money": 8,
            "fixed_assets": [],
        },
    }


class RecordingGateway:
    def __init__(self, *, fail_first_opening=False):
        self.calls = []
        self.fail_first_opening = fail_first_opening
        self.opening_attempts = 0

    def set_trace_context(self, **_kwargs):
        pass

    def run(self, task, input_data, output_schema, **_kwargs):
        self.calls.append((task, input_data))
        if task == "opening_foundation":
            return output_schema.model_validate(_foundation_payload())
        if task == "opening_scene":
            self.opening_attempts += 1
            if self.fail_first_opening and self.opening_attempts == 1:
                raise RuntimeError("opening failed")
            return output_schema.model_validate({
                "scene_text": "清晨，你抵达白塔港，登记大厅却突然封门。"
            })
        raise AssertionError(f"unexpected task: {task}")


def test_initialization_graph_has_only_foundation_then_opening():
    graph = initialization_graph.build_initialization_graph()
    assert set(graph.get_graph().nodes) == {
        "__start__",
        "opening_foundation",
        "opening_scene",
        "__end__",
    }


def test_setup_is_persisted_before_private_foundation_call(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()

    class AssertingGateway(RecordingGateway):
        def run(self, task, input_data, output_schema, **kwargs):
            if task == "opening_foundation":
                record = runtime.initialization.load()
                assert record is not None
                assert record.phase == "core_memory_committed"
                assert runtime.stores.core_memory().load() == record.core_memory
                assert runtime.stores.current_state().load() is None
            return super().run(task, input_data, output_schema, **kwargs)

    gateway = AssertingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    events = list(initialization_graph.initialization_stream(_payload()))
    record = runtime.initialization.load()

    assert [task for task, _ in gateway.calls] == [
        "opening_foundation",
        "opening_scene",
    ]
    assert [event["step"] for event in events] == [
        "initialization_started",
        "opening_foundation_ready",
        "opening_scene_done",
        "initialization_pending",
    ]
    assert record is not None and record.phase == "opening_pending"
    assert record.foundation is not None
    assert runtime.stores.current_state().load() is None
    assert len(runtime.pending_openings.values()) == 1


def test_opening_retry_reuses_same_persisted_foundation(
    configure_runtime_services,
):
    gateway = RecordingGateway(fail_first_opening=True)
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    with pytest.raises(RuntimeError, match="opening failed"):
        list(initialization_graph.initialization_stream(_payload()))
    events = list(initialization_graph.initialization_stream(_payload()))

    assert [task for task, _ in gateway.calls] == [
        "opening_foundation",
        "opening_scene",
        "opening_scene",
    ]
    ready = next(
        event for event in events
        if event["step"] == "opening_foundation_ready"
    )
    assert ready["data"]["reused"] is True


def test_submitting_setup_clears_public_draft(
    configure_runtime_services,
):
    save_initialization_draft({
        "version": 3,
        "stage": "campaign_setup",
        "form": {"world_name": "艾兰"},
        "config": _payload()["config"],
        "campaign_setup": _payload()["campaign_setup"],
    })
    gateway = RecordingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    list(initialization_graph.initialization_stream(_payload()))

    assert load_initialization_draft() is None


def test_pending_persistence_failure_keeps_foundation_retryable_without_ghost(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    gateway = RecordingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)
    runtime.pending_openings.configure_callbacks(
        on_add=lambda _pending: (_ for _ in ()).throw(
            RuntimeError("pending persistence failed")
        ),
        on_remove=runtime.history.discard,
    )

    with pytest.raises(RuntimeError, match="pending persistence failed"):
        list(initialization_graph.initialization_stream(_payload()))

    record = runtime.initialization.load()
    assert runtime.pending_openings.values() == []
    assert record is not None
    assert record.phase == "foundation_ready"
    assert record.foundation is not None
