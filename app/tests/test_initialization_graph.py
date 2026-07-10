from app.domain.core_memory import (
    CoreMemory,
    StoryDirectionResult,
)
from app.domain.scene_writer.schemas import SceneWriterResult
from app.graph import initialization_graph
from app.graph.initialization_helpers import prepare_initialization_state


def _payload(*, story_expectation=None):
    return {
        "version": 1,
        "fanfic": False,
        "world_name": "艾兰",
        "world_style": "西幻",
        "protagonist_name": "卢西安",
        "story_expectation": story_expectation,
        "extra_core_memory_items": [],
    }


def test_initialization_graph_has_only_llm_nodes():
    graph = initialization_graph.build_initialization_graph()

    assert set(graph.get_graph().nodes) == {
        "__start__",
        "story_direction",
        "opening_scene",
        "__end__",
    }


def test_initialization_graph_skips_story_direction_without_expectation(monkeypatch):
    calls = []

    def _story_direction(_state):
        raise AssertionError("story_direction must not run")

    def _opening_scene(_state):
        calls.append("opening_scene")
        return {
            "opening_scene": SceneWriterResult(scene_text="开场"),
            "events": [{"step": "opening_scene_done", "data": {}}],
        }

    monkeypatch.setattr(
        initialization_graph,
        "story_direction_node",
        _story_direction,
    )
    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload())
    )

    assert calls == ["opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "opening_scene_done"
    ]


def test_initialization_graph_applies_story_direction_before_opening(monkeypatch):
    calls = []

    def _story_direction(state):
        calls.append("story_direction")
        direction = StoryDirectionResult(
            content="长期探索世界并建立关系。",
            currency_unit="金币",
        )
        return {
            "story_direction": direction,
            "core_memory": CoreMemory(
                items=[
                    *state["core_memory"].items,
                    *direction.to_core_memory_items(),
                ]
            ),
            "current_state": state["current_state"].model_copy(
                update={
                    "resources": state["current_state"].resources.model_copy(
                        update={"money_unit": "金币"}
                    )
                }
            ),
            "events": [{"step": "story_direction_done", "data": {}}],
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
        "story_direction_node",
        _story_direction,
    )
    monkeypatch.setattr(initialization_graph, "opening_scene_node", _opening_scene)

    result = initialization_graph.build_initialization_graph().invoke(
        prepare_initialization_state(_payload(story_expectation="探索与成长"))
    )

    assert calls == ["story_direction", "opening_scene"]
    assert [event["step"] for event in result["events"]] == [
        "story_direction_done",
        "opening_scene_done",
    ]
