import json

import pytest

from app.core.llm.token_usage import publish_token_usage
from app.graph import initialization_graph
from app.runtime.initialization_draft import (
    load_initialization_draft,
    save_initialization_draft,
)
from app.runtime.session import get_campaign_runtime


def _payload():
    return {
        "config": {
            "version": 4,
            "world_name": "艾兰",
            "world_style": "西幻",
            "ability_system": "魔法",
            "protagonist_name": "卢西安",
            "story_expectation": "探索、成长并建立真实关系。",
            "failure_severity": "适中",
        },
        "campaign_setup": {
            "version": 2,
            "currency_unit": "金币",
            "public_power_baseline": {
                "source": "魔法",
                "prevalence": "少数人掌握",
                "learning_difficulty": "困难",
                "limitation": "不能复活死者。",
            },
            "peoples_and_beings": ["人类", "精灵"],
            "major_world_conflicts": ["帝国扩张持续挤压边境诸国。"],
            "dominant_powers": ["北方帝国", "圣辉教会"],
            "civilization_baseline": "以马车、冷兵器和手工业为主。",
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

    def run(self, task, input_data, output_schema, **kwargs):
        self.calls.append((task, input_data))
        on_attempt_start = kwargs.get("on_attempt_start")
        output_validator = kwargs.get("output_validator")
        if on_attempt_start is not None:
            on_attempt_start(1)
        def _maybe_validate(result):
            if output_validator is not None:
                output_validator(result)
            return result
        if task == "opening_foundation":
            return _maybe_validate(output_schema.model_validate(_foundation_payload()))
        if task == "opening_setting_requirements":
            return _maybe_validate(output_schema.model_validate({
                "specs": [
                    {
                        "kind": "faction",
                        "setting_description": (
                            "掌控北境商路的商会联盟，对边境贸易影响深远。"
                        ),
                    },
                    {
                        "kind": "location",
                        "setting_description": (
                            "一座依山而建的贸易港，作为故事开场的起点。"
                        ),
                    },
                    {
                        "kind": "lore",
                        "setting_description": (
                            "帝国以成文律法维系边境秩序，边境诸国受其挤压。"
                        ),
                    },
                ],
            }))
        if task == "director_setting_create":
            canned_by_kind = {
                "faction": {
                    "name": "北境商盟",
                    "summary": "掌控北境商路的商会联盟。",
                    "public_facts": ["统一收取过路费。"],
                },
                "location": {
                    "name": "白塔港",
                    "summary": "依山而建的贸易港，入城需登记。",
                    "public_facts": ["入城需在登记大厅办理手续。"],
                },
                "lore": {
                    "name": "大陆律法",
                    "summary": "帝国以成文律法维系边境秩序。",
                    "public_facts": ["帝国扩张持续挤压边境诸国。"],
                },
            }
            canned = canned_by_kind.get(input_data["kind"])
            if canned is None:
                raise AssertionError(f"unexpected setting kind: {input_data['kind']}")
            return _maybe_validate(output_schema.model_validate(canned))
        if task == "opening_scene":
            self.opening_attempts += 1
            if self.fail_first_opening and self.opening_attempts == 1:
                raise RuntimeError("opening failed")
            return _maybe_validate(output_schema.model_validate({
                "scene_text": "清晨，你抵达白塔港，登记大厅却突然封门。"
            }))
        raise AssertionError(f"unexpected task: {task}")


def test_initialization_graph_has_only_foundation_then_settings_then_opening():
    graph = initialization_graph.build_initialization_graph()
    assert set(graph.get_graph().nodes) == {
        "__start__",
        "opening_foundation",
        "opening_setting",
        "opening_scene",
        "__end__",
    }


def test_setup_and_initial_state_are_persisted_before_their_consumers(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()

    class AssertingGateway(RecordingGateway):
        def run(self, task, input_data, output_schema, **kwargs):
            if task == "opening_foundation":
                record = runtime.initialization.load()
                assert record is not None
                assert record.phase == "core_memory_committed"
                assert runtime.stores.core_memory.load() == record.core_memory
                assert runtime.stores.current_state.load() is None
            if task == "opening_scene":
                persisted = runtime.stores.current_state.load()
                assert persisted is not None
                assert persisted.scene.location == "白塔港"
                assert persisted.player.name == "卢西安"
                assert persisted.resources.money == 8
                assert persisted.resources.money_unit == "金币"
                assert [item.name for item in persisted.resources.inventory] == [
                    "旅行包"
                ]
                projected = json.loads(input_data["current_state_json"])
                assert projected["scene"]["location"] == "白塔港"
                assert projected["scene"]["participants"] == [{"name": "卢西安"}]
                assert projected["player"] == {
                    "name": "卢西安",
                    "visible_status": "男性",
                }
                assert projected["resources"] == {
                    "inventory": [{"name": "旅行包", "quantity": 1}],
                    "money": 8,
                    "money_unit": "金币",
                }
            return super().run(task, input_data, output_schema, **kwargs)

    gateway = AssertingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    events = list(initialization_graph.initialization_stream(_payload()))
    record = runtime.initialization.load()

    ordered_tasks = [
        task for task, _ in gateway.calls
        if task != "director_setting_create"
    ]
    assert ordered_tasks == [
        "opening_foundation",
        "opening_setting_requirements",
        "opening_scene",
    ]
    assert [task for task, _ in gateway.calls if task == "director_setting_create"] == [
        "director_setting_create",
        "director_setting_create",
        "director_setting_create",
    ]
    assert [event["step"] for event in events] == [
        "initialization_started",
        "opening_foundation_ready",
        "opening_settings_started",
        "opening_settings_done",
        "opening_scene_done",
        "initialization_pending",
    ]
    assert record is not None and record.phase == "opening_pending"
    assert record.foundation is not None
    persisted = runtime.stores.current_state.load()
    assert persisted is not None
    assert persisted.scene.location == "白塔港"
    assert persisted.player.name == "卢西安"
    assert persisted.resources.money == 8
    assert persisted.resources.money_unit == "金币"
    assert [item.name for item in persisted.resources.inventory] == ["旅行包"]
    assert len(runtime.pending_openings.values()) == 1

    with runtime.stores.world_setting as store:
        settings = store.list_all()
    assert {setting.kind for setting in settings} == {"faction", "location", "lore"}
    assert {setting.name for setting in settings} == {"北境商盟", "白塔港", "大陆律法"}
    assert all(setting.created_turn == 0 for setting in settings)
    assert all(setting.hit_count == 0 for setting in settings)


def test_prepare_initialization_state_accepts_player_edits_and_maps_errors(
    configure_runtime_services,
):
    from app.core.client_error import ClientValidationError
    from app.graph.initialization_helpers import prepare_initialization_state

    configure_runtime_services(gateway_factory=lambda **_kwargs: RecordingGateway())
    runtime = get_campaign_runtime()

    payload = _payload()
    payload["campaign_setup"]["public_power_baseline"]["limitation"] = (
        "不能羽化飞升；违者走火入魔。"
    )
    payload["campaign_setup"]["civilization_baseline"] = "农耕为主\n手工业与舟船"
    state = prepare_initialization_state(payload)
    assert state["campaign_setup"].public_power_baseline.limitation == (
        "不能羽化飞升；违者走火入魔。"
    )
    persisted = runtime.initialization.load()
    assert persisted is not None
    assert persisted.campaign_setup == state["campaign_setup"]

    bad = _payload()
    bad["campaign_setup"]["public_power_baseline"]["limitation"] = "   "
    with pytest.raises(ClientValidationError, match="力量限制：不能为空"):
        prepare_initialization_state(bad)

    with pytest.raises(ClientValidationError, match="初始化配置缺失"):
        prepare_initialization_state({
            "config": None,
            "campaign_setup": _payload()["campaign_setup"],
        })
    with pytest.raises(ClientValidationError, match="公开世界惯例缺失"):
        prepare_initialization_state({
            "config": _payload()["config"],
            "campaign_setup": None,
        })

    empty_list = _payload()
    empty_list["campaign_setup"]["peoples_and_beings"] = []
    with pytest.raises(ClientValidationError, match="主要存在主体：至少 1 项"):
        prepare_initialization_state(empty_list)

    over_list = _payload()
    over_list["campaign_setup"]["major_world_conflicts"] = ["一", "二", "三"]
    with pytest.raises(ClientValidationError, match="世界主要冲突：最多 2 项"):
        prepare_initialization_state(over_list)


def test_opening_retry_reuses_same_persisted_foundation(
    configure_runtime_services,
):
    gateway = RecordingGateway(fail_first_opening=True)
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    with pytest.raises(RuntimeError, match="opening failed"):
        list(initialization_graph.initialization_stream(_payload()))
    state_after_failure = get_campaign_runtime().stores.current_state.load()
    assert state_after_failure is not None
    assert state_after_failure.scene.location == "白塔港"
    assert state_after_failure.resources.money == 8

    events = list(initialization_graph.initialization_stream(_payload()))

    ordered_tasks = [
        task for task, _ in gateway.calls
        if task != "director_setting_create"
    ]
    assert ordered_tasks == [
        "opening_foundation",
        "opening_setting_requirements",
        "opening_scene",
        "opening_scene",
    ]
    assert [task for task, _ in gateway.calls if task == "director_setting_create"] == [
        "director_setting_create",
        "director_setting_create",
        "director_setting_create",
    ]
    ready = next(
        event for event in events
        if event["step"] == "opening_foundation_ready"
    )
    assert ready["data"]["reused"] is True
    settings_done = next(
        event for event in events
        if event["step"] == "opening_settings_done"
    )
    assert settings_done["data"]["reused"] is True
    assert get_campaign_runtime().stores.current_state.load() == state_after_failure
    with get_campaign_runtime().stores.world_setting as store:
        assert len(store.list_all()) == 3


def test_opening_settings_node_generates_once_and_reuses(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()
    gateway = RecordingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    from app.domain.opening_foundation import OpeningFoundation
    from app.graph.initialization_helpers import prepare_initialization_state
    from app.graph.initialization_nodes import opening_setting_node

    state = {
        **prepare_initialization_state(_payload()),
        "opening_foundation": OpeningFoundation.model_validate(_foundation_payload()),
    }

    first = opening_setting_node(state)
    assert first["events"][0]["data"]["reused"] is False
    with runtime.stores.world_setting as store:
        assert len(store.list_all()) == 3

    requirement_input = next(
        data for task, data in gateway.calls
        if task == "opening_setting_requirements"
    )["core_memory_json"]
    assert "卢西安" not in requirement_input
    assert "探索、成长" not in requirement_input
    assert "金币" in requirement_input
    assert "人类、精灵" in requirement_input

    setting_input = next(
        data for task, data in gateway.calls
        if task == "director_setting_create"
    )["core_memory_json"]
    assert "卢西安" not in setting_input
    assert "金币" in setting_input

    second = opening_setting_node(state)
    assert second["events"][0]["data"]["reused"] is True
    assert [task for task, _ in gateway.calls if task == "director_setting_create"] == [
        "director_setting_create",
        "director_setting_create",
        "director_setting_create",
    ]
    with runtime.stores.world_setting as store:
        assert len(store.list_all()) == 3


def test_submitting_setup_clears_public_draft(
    configure_runtime_services,
):
    save_initialization_draft({
        "version": 4,
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
    persisted = runtime.stores.current_state.load()
    assert persisted is not None
    assert persisted.scene.location == "白塔港"
    assert persisted.resources.money == 8


def test_initialization_binds_opening_trace_so_generation_usage_is_recorded(
    configure_runtime_services,
):
    runtime = get_campaign_runtime()

    class UsagePublishingGateway(RecordingGateway):
        def run(self, task, input_data, output_schema, **kwargs):
            if task == "opening_scene":
                publish_token_usage(
                    trace_id=kwargs.get("trace_id"),
                    call_id="opening-call",
                    task="opening_scene",
                    attempt=1,
                    token_usage={"input_tokens": 120, "output_tokens": 40},
                    session_type="init",
                )
            return super().run(task, input_data, output_schema, **kwargs)

    gateway = UsagePublishingGateway()
    configure_runtime_services(gateway_factory=lambda **_kwargs: gateway)

    operation_id = runtime.operations.submit_stream(
        "initialization_opening",
        initialization_graph.initialization_stream,
        _payload(),
    )
    snapshot = runtime.operations.wait(operation_id, timeout=2)
    assert snapshot.status == "succeeded"

    openings = list(runtime.pending_openings.values())
    assert len(openings) == 1
    token_usage = openings[0].token_usage
    assert token_usage is not None
    assert token_usage["generation"] is not None
    assert token_usage["generation"]["input_tokens"] == 120
    assert token_usage["generation"]["output_tokens"] == 40
