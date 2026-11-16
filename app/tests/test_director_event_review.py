import json
import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.core.llm.errors import LLMOutputValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.director.confirmation import DirectorConfirmationExecutor
from app.domain.director_event.schemas import DirectorEventFact
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.tests.factories.director import (
    ScriptedDirectorGateway, make_current_state, make_review_selection, make_review_node_state,
)


def _executor(stores, gateway):
    return DirectorConfirmationExecutor(
        gateway=gateway, stores=stores, core_memory=CoreMemory(items=[]),
        current_state=make_current_state(), user_input="烧毁渡船。",
        scene_facts={"scene_focus": "接应渡船被烧毁。", "facts": [{"fact": "玩家烧毁了渡船。"}]},
        recent_summaries=[], selection_summaries=[], turn_index=3,
        trace_id="event-maintenance", scene_text="玩家烧毁了接应用的渡船。",
    )


def _decision(event_id, tasks):
    return {"reasoning": "记录本轮事件变化。", "requests": [{
        "request_id": "maintain", "skill": "maintain_event",
        "arguments": {"event_id": event_id, "tasks": tasks,
                      "fact_changes": "接应渡船已被玩家烧毁。" if "update_situation" in tasks else ""},
        "guidance": "渡船已烧毁，记录这一事实并使可用渡船的旧事实失效。",
    }] if tasks else []}


@pytest.mark.parametrize("tasks,stage", [
    (["update_situation", "engage"], "engaged"),
    (["engage"], "engaged"), (["resolve"], "resolved"), ([], "hidden"),
])
def test_confirmation_maintains_event_facts_and_stage(tmp_path, monkeypatch, tasks, stage):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    ship = DirectorEventFact(fact="接应方有可用渡船。")
    goal = DirectorEventFact(fact="逃亡者持有账册。")
    old = DirectorEventFact(fact="旧接头点可用。", active=False)
    event = make_review_selection("接应逃亡者").to_event(turn_index=1, current_clock=WorldClock())
    event.current_situation = [ship, goal, old]
    stores.director_event.save(event)
    event = stores.director_event.get_by_id(event.event_id)
    mock = MockLLM()
    responses = [json.dumps(_decision(event.event_id, tasks), ensure_ascii=False)]
    if "update_situation" in tasks:
        responses.append(json.dumps({"reason": "渡船已毁。", "add_facts": ["不应保存的失败尝试。"],
                                     "invalidate_fact_ids": ["unknown"]}, ensure_ascii=False))
        responses.append(json.dumps({"reason": "正文确认玩家烧毁接应渡船，原有可用渡船事实不再成立。",
                                     "add_facts": ["玩家已经烧毁接应渡船。"],
                                     "invalidate_fact_ids": [ship.fact_id]}, ensure_ascii=False))
    mock.set_responses(responses)
    gateway = mock.build_gateway()
    calls = []
    run = gateway.run
    def record(**kwargs):
        calls.append(kwargs)
        return run(**kwargs)
    monkeypatch.setattr(gateway, "run", record)
    decision, executions = _executor(stores, gateway).run()
    assert len(decision.requests) == bool(tasks)
    saved = stores.director_event.get_by_id(event.event_id)
    assert saved.stage == stage
    assert saved.intent == event.intent and saved.plan == event.plan
    assert [f.fact_id for f in saved.current_situation[:3]] == [ship.fact_id, goal.fact_id, old.fact_id]
    assert [f.fact for f in saved.current_situation[:3]] == [ship.fact, goal.fact, old.fact]
    assert [f.active for f in saved.current_situation[:3]] == ["update_situation" not in tasks, True, False]
    assert [c["task"] for c in calls] == ["director_agent_decide_confirmation"] + (
        ["director_event_update"] if "update_situation" in tasks else [])
    if "update_situation" in tasks:
        assert saved.current_situation[-1].fact == "玩家已经烧毁接应渡船。"
        assert saved.current_situation[-1].active
        assert all(f.fact != "不应保存的失败尝试。" for f in saved.current_situation)
        assert stores.director_event.get_by_id(event.event_id).current_situation == saved.current_situation
        supplied = calls[-1]["input_data"]
        assert next(iter(calls[-1]["output_schema"].model_json_schema()["properties"])) == "reason"
        assert supplied["fact_changes"] == "接应渡船已被玩家烧毁。"
        assert "director_guidance" not in supplied
        assert supplied["scene_text"] == "玩家烧毁了接应用的渡船。"
        assert "旧接头点可用" not in supplied["event_json"]
    if tasks:
        assert executions.executions[0].output["stage_before"] == "hidden"
    if "resolve" in tasks:
        assert saved.resolved_reason == decision.requests[0].guidance
        assert saved.resolved_turn == 3 and saved.final_world_impact is None
    assert not any(h.consumed for h in saved.hook_pool)


def test_maintenance_schema_binds_tasks_to_each_active_event(tmp_path):
    from app.domain.director.skill_specs import maintain_event_arguments
    hidden = make_review_selection("潜伏").to_event(turn_index=1, current_clock=WorldClock())
    engaged = make_review_selection("介入").to_event(turn_index=1, current_clock=WorldClock())
    engaged.stage = "engaged"
    model = maintain_event_arguments([hidden, engaged])
    schema = model.model_json_schema()
    branches = [schema["$defs"][item["$ref"].split("/")[-1]] for item in schema["anyOf"]]
    options = {b["properties"]["event_id"]["const"]: b["properties"]["tasks"]["items"]["enum"]
               for b in branches}
    assert options == {hidden.event_id: ["update_situation", "engage", "resolve"],
                       engaged.event_id: ["update_situation", "resolve"]}
    assert all(list(b["properties"]) == ["event_id", "tasks", "fact_changes"] for b in branches)
    for event_id, task in [(engaged.event_id, "engage"), (hidden.event_id, "expire_involved"),
                           ("unknown", "resolve")]:
        with pytest.raises(ValidationError):
            model.model_validate({"event_id": event_id, "tasks": [task], "fact_changes": ""})
    model.model_validate({"event_id": hidden.event_id, "tasks": ["update_situation", "engage"],
                          "fact_changes": "渡船已烧毁。"})
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    stores.director_event.save(hidden)
    stores.director_event.save(engaged)
    decision = _decision(hidden.event_id, ["engage"])
    decision["requests"].append({"request_id": "resolve", "skill": "maintain_event",
        "arguments": {"event_id": engaged.event_id, "tasks": ["resolve"], "fact_changes": ""},
        "guidance": "事件目标已经实现。"})
    gateway = ScriptedDirectorGateway(decision=decision)
    _executor(stores, gateway).run()
    assert stores.director_event.get_by_id(hidden.event_id).stage == "engaged"
    assert stores.director_event.get_by_id(engaged.event_id).stage == "resolved"


def test_failed_fact_update_keeps_event_and_actor_binding_unchanged(tmp_path):
    from app.domain.npc_profile.schemas import NPCProfile
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event = make_review_selection("接应").to_event(turn_index=1, current_clock=WorldClock())
    stores.director_event.save(event)
    event = stores.director_event.get_by_id(event.event_id)
    npc = NPCProfile(name="林澈", role="接应者")
    stores.npc_profile.upsert(npc)
    decision = _decision(event.event_id, ["update_situation", "engage"])
    decision["requests"].append({"request_id": "bind", "skill": "bind_event_actor",
        "arguments": {"event_id": event.event_id, "npc_id": npc.npc_id}, "guidance": "分配接应者。"})
    class Gateway(ScriptedDirectorGateway):
        def run(self, **kwargs):
            if kwargs["task"] == "director_event_update":
                raise LLMOutputValidationError("invalid fact update")
            return super().run(**kwargs)
    executor = _executor(stores, Gateway(decision=decision))
    result, _ = executor.run()
    assert result.requests == []
    assert stores.director_event.get_by_id(event.event_id) == event
    assert executor.last_warnings[-1].code == "director_execution_fallback"


@pytest.mark.parametrize("stage", ["hidden", "engaged"])
def test_no_fixed_review_before_deadline(tmp_path, stage):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    event = make_review_selection("未到期").to_event(turn_index=1, current_clock=WorldClock())
    event.stage = stage
    stores.director_event.save(event)
    event = stores.director_event.get_by_id(event.event_id)
    gateway = ScriptedDirectorGateway(decision={"reasoning": "无变化。", "requests": []})
    result = confirmation_nodes.director_event_expiry_node(make_review_node_state(stores, gateway))
    assert result["director_event_reviews"] == []
    assert gateway.tasks == []
    assert stores.director_event.get_by_id(event.event_id) == event
