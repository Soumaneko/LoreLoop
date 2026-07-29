import pytest

from app.domain.scene_writer.schemas import SceneWriterResult
from app.graph import initialization_graph
from app.graph.initialization_helpers import prepare_initialization_state
from app.runtime.initialization_draft import (
    load_initialization_draft,
    save_initialization_draft,
)


def _payload(*, story_expectation="探索与成长"):
    return {
        "config": {
            "version": 1,
            "fanfic": False,
            "world_name": "艾兰",
            "world_style": "西幻",
            "protagonist_name": "卢西安",
            "story_expectation": story_expectation,
            "extra_core_memory_items": [],
        },
        "story_direction": {
            "content": "长期探索世界并建立关系。",
            "currency_unit": "金币",
        },
    }


def _initial_setting_payload():
    return {
        "settings": [
            {
                "setting_id": "setting_01",
                "label": "巡夜钟塔",
                "kind": "place",
                "definition": "巡夜钟塔为艾兰城郊提供夜间警戒信号。",
                "setting_facts": ["钟塔使用不同钟声表示城郊风险等级。"],
                "relation_to_known_world": ["钟塔位于艾兰城郊。"],
                "world_effects": ["夜间商旅会根据钟声改变路线。"],
                "scope_and_boundaries": ["钟声只能覆盖艾兰城郊。"],
            },
            {
                "setting_id": "setting_02",
                "label": "冬粮联保契",
                "kind": "institution_or_custom",
                "definition": "冬粮联保契要求邻近村镇共同维持最低粮食储备。",
                "setting_facts": ["签约村镇必须公开每月粮仓账目。"],
                "relation_to_known_world": ["联保契适用于艾兰周边村镇。"],
                "world_effects": ["隐瞒储备会导致其他村镇停止援助。"],
                "scope_and_boundaries": ["联保契不约束未签约领地。"],
            },
        ]
    }


def test_initialization_graph_has_expected_nodes():
    graph = initialization_graph.build_initialization_graph()

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "initial_world_setting",
        "opening_scene",
        "__end__",
    }


def test_initialization_graph_generates_initial_settings_then_opening(monkeypatch):
    calls = []

    def _initial_world_setting(_state):
        calls.append("initial_world_setting")
        return {
            "initial_world_settings": [],
            "events": [{"step": "initial_world_settings_ready", "data": {}}],
        }

    def _opening_scene(_state):
        calls.append("opening_scene")
        return {
            "opening_scene": SceneWriterResult(scene_text="开场"),
            "events": [{"step": "opening_scene_done", "data": {}}],
        }

    monkeypatch.setattr(
        initialization_graph,
        "initial_world_setting_node",
        _initial_world_setting,
    )
    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload())
    )

    assert calls == ["initial_world_setting", "opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "initial_world_settings_ready",
        "opening_scene_done"
    ]


def test_initialization_state_applies_edited_story_direction_before_opening(monkeypatch):
    calls = []

    def _initial_world_setting(state):
        calls.append("initial_world_setting")
        assert state["story_direction"].content.startswith("长期探索")
        assert any(
            item.id == "core_story_direction"
            for item in state["core_memory"].items
        )
        return {
            "initial_world_settings": [],
            "events": [{"step": "initial_world_settings_ready", "data": {}}],
        }

    def _opening_scene(state):
        calls.append("opening_scene")
        assert any(
            item.id == "core_story_direction"
            for item in state["core_memory"].items
        )
        assert any(
            item.id == "core_currency_system"
            for item in state["core_memory"].items
        )
        assert state["current_state"].resources.money_unit == "金币"
        return {
            "opening_scene": SceneWriterResult(scene_text="开场"),
            "events": [{"step": "opening_scene_done", "data": {}}],
        }

    monkeypatch.setattr(
        initialization_graph,
        "initial_world_setting_node",
        _initial_world_setting,
    )
    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload())
    )

    assert calls == ["initial_world_setting", "opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "initial_world_settings_ready",
        "opening_scene_done",
    ]


def test_initialization_stream_emits_progress_and_pending_boundary(monkeypatch):
    def _initial_world_setting(_state):
        return {
            "initial_world_settings": [],
            "events": [{"step": "initial_world_settings_ready", "data": {}}],
        }

    def _opening_scene(_state):
        return {
            "opening_scene": SceneWriterResult(scene_text="开场"),
            "events": [{"step": "opening_scene_done", "data": {}}],
        }

    monkeypatch.setattr(
        initialization_graph,
        "initial_world_setting_node",
        _initial_world_setting,
    )
    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)
    save_initialization_draft({
        "version": 1,
        "stage": "story_direction",
        "form": {"fields": {"world_name": "艾兰"}},
        "config": _payload()["config"],
        "story_direction": _payload()["story_direction"],
    })

    events = list(initialization_graph.initialization_stream(_payload()))

    assert [event["step"] for event in events] == [
        "initialization_started",
        "initial_world_settings_ready",
        "opening_scene_done",
        "initialization_pending",
    ]
    assert events[-1]["data"]["status"] == "awaiting_confirm"
    assert load_initialization_draft() is None


def test_opening_retry_reuses_initial_settings_without_second_llm_call(
    configure_runtime_services,
):
    calls = []
    opening_attempts = 0

    class RecordingGateway:
        def run(self, task, input_data, output_schema, **kwargs):
            nonlocal opening_attempts
            calls.append(task)
            if task == "init_world_setting_create":
                return output_schema.model_validate(_initial_setting_payload())
            if task == "opening_scene":
                opening_attempts += 1
                if opening_attempts == 1:
                    raise RuntimeError("opening failed")
                return output_schema.model_validate({"scene_text": "开场"})
            raise AssertionError(f"unexpected task: {task}")

        def set_trace_context(self, **kwargs):
            pass

    configure_runtime_services(gateway_factory=RecordingGateway)

    with pytest.raises(RuntimeError, match="opening failed"):
        list(initialization_graph.initialization_stream(_payload()))
    events = list(initialization_graph.initialization_stream(_payload()))

    assert calls == [
        "init_world_setting_create",
        "opening_scene",
        "opening_scene",
    ]
    ready = next(
        event for event in events
        if event["step"] == "initial_world_settings_ready"
    )
    assert ready["data"]["reused"] is True
