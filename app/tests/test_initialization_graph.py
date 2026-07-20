from app.domain.scene_writer.schemas import SceneWriterResult
from app.graph import initialization_graph
from app.graph.initialization_helpers import prepare_initialization_state


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


def test_initialization_graph_has_only_llm_nodes():
    graph = initialization_graph.build_initialization_graph()

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "opening_scene",
        "__end__",
    }


def test_initialization_graph_only_generates_opening(monkeypatch):
    calls = []

    def _opening_scene(_state):
        calls.append("opening_scene")
        return {
            "opening_scene": SceneWriterResult(scene_text="开场"),
            "events": [{"step": "opening_scene_done", "data": {}}],
        }

    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload())
    )

    assert calls == ["opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "opening_scene_done"
    ]


def test_initialization_state_applies_edited_story_direction_before_opening(monkeypatch):
    calls = []

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

    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload())
    )

    assert calls == ["opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "opening_scene_done",
    ]
