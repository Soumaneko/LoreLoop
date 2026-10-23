import json
import pytest
from app.core.llm import MockLLM
from app.domain.action.parser import ActionParser
from app.domain.action.schemas import ActionRetrievalTopic
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, ResourcesState, SceneState


def test_action_parser_returns_retrieval_topics():
    parser = _make_parser([
        json.dumps({
            "atomic_actions": [{
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "hide_or_stealth",
                "raw_text": "埋伏在山脚下的必经之路",
                "verb": "埋伏",
                "target": "山脚下的必经之路",
                "method": None,
                "condition": None,
                "likely_needs_roll": True,
                "risk_level": "medium",
            }],
            "retrieval_topics": [{
                "text": "埋伏在山脚下",
            }],
        }, ensure_ascii=False)
    ])

    result = parser.parse(
        "我埋伏在山脚下的必经之路。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert result.retrieval_topics == [ActionRetrievalTopic(
        text="埋伏在山脚下",
    )]


def _make_current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_010",
            location="码头仓库区",
            participants=["主角", "伊莱娜", "马洛"],
        ),
        player=PlayerState(name="主角"),
        resources=ResourcesState(),
    )


def _core_memory() -> CoreMemory:
    return CoreMemory(items=[])


def _make_parser(responses: list[str]) -> ActionParser:
    mock = MockLLM()
    mock.set_responses(responses)
    return ActionParser(mock.build_gateway())


def _build_response(actions: list[dict]) -> str:
    return json.dumps({
        "atomic_actions": actions,
    }, ensure_ascii=False)


# ══════════════════════════════════════════════════════════════════
#  Failure policy: salvage, no-effect, and hidden-event filtering
# ══════════════════════════════════════════════════════════════════

def test_action_parser_provider_failure_propagates():
    from app.core.llm import MockLLM
    from app.core.llm.errors import LLMProviderError

    mock = MockLLM()
    mock.set_fail_provider(True)
    parser = ActionParser(mock.build_gateway())

    with pytest.raises(LLMProviderError):
        parser.parse(
            "我拔出剑。",
            _make_current_state(),
            core_memory=_core_memory(),
        )


def test_action_parser_first_invalid_retries_and_second_valid_subset_wins():
    first = json.dumps({
        "analysis": "第一次解析",
        "atomic_actions": [
            {
                "action_id": "A1",
                "sequence": 1,
                "actor": ["主角"],
                "action_type": "observe",
                "raw_text": "观察四周",
                "verb": "观察",
                "likely_needs_roll": False,
            },
            {
                "action_id": "  ",
                "sequence": 2,
                "actor": ["主角"],
                "action_type": "attack",
                "raw_text": "挥剑",
                "verb": "挥",
                "likely_needs_roll": True,
            },
        ],
    }, ensure_ascii=False)
    second = _build_response([{
        "action_id": "A1",
        "sequence": 1,
        "actor": ["主角"],
        "action_type": "observe",
        "raw_text": "观察四周",
        "verb": "观察",
        "likely_needs_roll": False,
    }])

    parser = _make_parser([first, second])
    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert parser.last_warnings == []


def test_action_parser_filters_unknown_touched_event_id_after_retry():
    from types import SimpleNamespace

    visible_event = SimpleNamespace(
        event_id="evt_visible",
        title="商路封锁",
        subject="河务署",
        intent="控制渡河通道",
        hook_pool=[],
    )
    first = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "touched_event_ids": ["unknown_event"],
    }, ensure_ascii=False)
    second = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "touched_event_ids": ["evt_visible", "unknown_event_2"],
    }, ensure_ascii=False)

    parser = _make_parser([first, second])
    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
        events=[visible_event],
    )

    assert result.touched_event_ids == ["evt_visible"]
    assert any(
        warning.code == "action_dropped"
        for warning in parser.last_warnings
    )


def test_action_parser_reports_retrieval_topic_count_dropped_on_salvage():
    from app.core.llm import MockLLM

    topics = [{"text": f"topic_{index}"} for index in range(9)]
    response = json.dumps({
        "atomic_actions": [{
            "action_id": "A1",
            "sequence": 1,
            "actor": ["主角"],
            "action_type": "observe",
            "raw_text": "观察四周",
            "verb": "观察",
            "likely_needs_roll": False,
        }],
        "retrieval_topics": topics,
    }, ensure_ascii=False)
    mock = MockLLM()
    mock.set_responses([response, response])
    parser = ActionParser(mock.build_gateway())

    result = parser.parse(
        "我观察四周。",
        _make_current_state(),
        core_memory=_core_memory(),
    )

    assert [action.action_id for action in result.atomic_actions] == ["A1"]
    assert len(result.retrieval_topics) == 8
    assert any(
        warning.code == "action_dropped"
        and "keeping first 8" in warning.message
        for warning in parser.last_warnings
    )
