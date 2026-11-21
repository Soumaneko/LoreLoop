import pytest

from app.domain.current_state import WorldClock
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import seed_deadline_event
from app.tests.factories.director import make_review_node_state


@pytest.mark.parametrize("case", ["hidden_due", "engaged_due", "not_due", "dry_run", "director_failure"])
def test_hidden_event_deadline_resolves_after_contact_review(tmp_path, monkeypatch, case):
    from types import SimpleNamespace
    from app.core.llm.errors import LLMOutputParseError
    from app.domain.scene_writer import SceneWriterResult
    from app.graph import confirmation_graph

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores)
    decision = {"reasoning": "检查已有事件。", "requests": []}
    if case == "engaged_due":
        decision["requests"] = [{"request_id": "engage", "skill": "maintain_event",
            "arguments": {"event_id": event_id, "tasks": ["engage"], "fact_changes": ""},
            "guidance": "玩家正在直接调查事件主体。"}]
    gateway = ScriptedDirectorGateway(decision=decision)
    if case == "director_failure":
        def fail_decision(**kwargs):
            raise LLMOutputParseError("invalid director JSON")
        monkeypatch.setattr(gateway, "run", fail_decision)
    state = make_review_node_state(
        stores, gateway,
        world_clock=WorldClock(day=1, phase="深夜") if case == "not_due" else WorldClock(day=2, phase="清晨"),
    )
    state["pipeline_input"].scene_result = SceneWriterResult(scene_text="码头暂无新变化。")
    state.update(pending=SimpleNamespace(director_stage=None),
                 effective_scene_text="码头暂无新变化。", write_db=case != "dry_run")
    should_resolve = case in {"hidden_due", "engaged_due", "director_failure"}

    result = confirmation_graph.build_confirmation_director_graph().invoke(state)
    assert not result.get("node_errors")
    saved = stores.director_event.get_by_id(event_id)
    assert saved.stage == ("resolved" if should_resolve else "hidden")
    assert (saved.final_world_impact == saved.world_impact) == (case in {"hidden_due", "director_failure"})
    assert gateway.tasks == ([] if case in {"dry_run", "director_failure"} else
                             ["director_agent_decide_confirmation"])
    saved = stores.director_event.get_by_id(event_id)
    state["pipeline_input"].turn_index += 1
    confirmation_nodes.director_hidden_expiry_node(state)
    assert stores.director_event.get_by_id(event_id) == saved


@pytest.mark.parametrize(
    "player_still_involved, final_world_impact, reason",
    [
        (True, None, "玩家仍在追查沉船账本。"),
        (
            False,
            {"description": "码头垄断仍然形成，但失去一条主要货路。"},
            "玩家已经转向其他目标，但此前破坏了一条货路。",
        ),
    ],
)
def test_engaged_event_deadline_resolves(tmp_path, monkeypatch, player_still_involved, final_world_impact, reason):
    import json
    from types import SimpleNamespace
    from app.domain.scene_summary.schemas import SceneSummary
    from app.domain.scene_writer import SceneWriterResult
    from app.graph.confirmation_graph import build_confirmation_director_graph
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event_id = seed_deadline_event(stores, stage="engaged")
    from app.core.llm import MockLLM
    mock = MockLLM()
    mock.set_responses([
        json.dumps({"reasoning": "本轮无额外维护。", "requests": []}, ensure_ascii=False),
        json.dumps({"reviews": [{"event_id": event_id, "reason": reason,
            "player_still_involved": player_still_involved,
            "final_world_impact": final_world_impact}]}, ensure_ascii=False),
    ])
    gateway = mock.build_gateway()
    calls = []
    run = gateway.run
    def record(**kwargs):
        calls.append(kwargs)
        return run(**kwargs)
    monkeypatch.setattr(gateway, "run", record)

    state = make_review_node_state(stores, gateway, world_clock=WorldClock(day=2, phase="清晨"))
    turn_summary = SceneSummary(content="玩家回到码头查看沉船账本。", level=1, turn_start=3, turn_end=3)
    state["selection_summaries"] = [*state["recent_summaries"], turn_summary]
    state["pipeline_input"].scene_result = SceneWriterResult(scene_text="玩家在码头行动。")
    state.update(pending=SimpleNamespace(director_stage=None), effective_scene_text="玩家在码头行动。")
    result = build_confirmation_director_graph().invoke(state)
    assert not result.get("node_errors")
    assert [call["task"] for call in calls] == ["director_agent_decide_confirmation", "director_event_expire"]
    assert json.loads(calls[-1]["input_data"]["scene_summaries_json"])[-1]["content"] == turn_summary.content
    expiry_schema = json.loads(calls[-1]["input_data"]["output_schema_json"])
    result_fields = expiry_schema["$defs"]["DirectorEventExpiryResult"]["properties"]
    assert set(result_fields) == {"event_id", "reason", "player_still_involved", "final_world_impact"}
    [maintenance] = [skill for skill in json.loads(calls[0]["input_data"]["available_skills_json"])
                     if skill["name"] == "maintain_event"]
    assert maintenance["input_schema"]["properties"]["tasks"]["items"]["enum"] == ["update_situation", "resolve"]

    with stores.director_event as store:
        resolved = store.get_by_id(event_id)
    assert resolved is not None
    assert resolved.stage == "resolved"
    if final_world_impact is None:
        assert resolved.final_world_impact is None
    else:
        assert resolved.final_world_impact is not None
        assert resolved.final_world_impact.description == final_world_impact["description"]
