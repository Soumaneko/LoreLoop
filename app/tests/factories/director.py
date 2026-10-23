from types import SimpleNamespace

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState, WorldClock
from app.domain.director import DirectorDecisionBatch, DirectorObservation
from app.domain.director_npc import DirectorNPCDraft
from app.domain.scene_facts.schemas import SceneFactSet


def run_generation_director_agent(**kwargs):
    from app.domain.director.generation import DirectorGenerationExecutor

    return DirectorGenerationExecutor(**kwargs).run()


def make_decision_batch(**kwargs) -> DirectorDecisionBatch:
    return DirectorDecisionBatch(**kwargs)


def make_director_observation(phase: str = "generation") -> DirectorObservation:
    return DirectorObservation(
        phase=phase,  # type: ignore[arg-type]
        turn_index=3,
        player_input="前往码头调查。",
        core_memory={"world_name": "测试世界"},
        current_state={"scene": {"location": "旧码头"}},
    )


def make_current_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_1",
            location="河岸社区",
            participants=["主角"],  # type: ignore[list-item]
        ),
        player=PlayerState(name="主角"),
    )


def make_director_npc() -> DirectorNPCDraft:
    from app.domain.director_npc.schemas import KnownAbility

    return DirectorNPCDraft(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        personality_trait="见人先问诊，习惯主动掌握对方近况",
        known_abilities=[KnownAbility(name="急救", description="处理外伤与常见急症")],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        created_turn=2,
    )


def make_event_selection_output(actor_roles: list[dict] | None = None) -> dict:
    return {
        "current_story_direction": "一条围绕河岸码头旧案的复仇线。",
        "conception": "世界缺乏明确的长期目标。",
        "title": "沉船账本",
        "duration_phases": 7,
        "selected_setting_fact_ids": ["fact_1"],
        "actor_roles": actor_roles or [],
        "subject": "码头旧账房",
        "intent": "追回被私吞的账目",
        "method": "暗中盘查往来货船",
        "macro_intent": "旧账房为保全码头世家的账目不择手段。",
        "hook_pool": [
            {
                "hook_id": "hook_rumor",
                "kind": "rumor",
                "content": "码头流传有人在深夜打捞沉船残骸的传闻。",
                "trigger_anchors": ["码头传闻", "河岸码头"],
            }
        ],
        "world_impact": {
            "description": "码头货运被旧账房势力暗中垄断。",
        },
    }


def seed_deadline_event(stores, *, stage="hidden"):
    from app.domain.director_event.schemas import DirectorEventSelectionResult

    selection = DirectorEventSelectionResult.model_validate(
        make_event_selection_output()
    )
    event = selection.to_event(
        turn_index=1,
        current_clock=WorldClock(day=1, phase="清晨"),
    )
    with stores.director_event as store:
        event_id = store.save(event)
        if stage == "engaged":
            store.set_stage(event_id, "engaged", turn_index=2)
    return event_id


def make_intrude_event():
    from app.domain.director_event.schemas import (
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    return DirectorEventSelectionResult(
        current_story_direction="商路日益吃紧。",
        conception="独立世界事件。",
        title="商路封锁",
        duration_phases=7,
        actor_roles=[],
        subject="河务署",
        intent="控制渡河通道",
        method="封锁旧桥",
        macro_intent="改变商路格局。",
        hook_pool=[
            EventHook(
                hook_id="hook_barrier",
                kind="environment",
                content="旧桥口设了新的盘查关卡。",
                trigger_anchors=["旧桥"],
            )
        ],
        world_impact=WorldImpact(description="渡河成本大涨。"),
    ).to_event(turn_index=1, current_clock=WorldClock())


def make_review_node_state(
    stores,
    gateway,
    *,
    turn_index=3,
    world_clock: WorldClock | None = None,
):
    updated_state = make_current_state()
    if world_clock is not None:
        updated_state.world_clock = world_clock
    return {
        "write_db": True,
        "pipeline_input": SimpleNamespace(
            user_input="深夜去旧码头看看。",
            turn_index=turn_index,
        ),
        "cache": {},
        "trace_id": "confirmation-director",
        "runtime_services": SimpleNamespace(
            stores=stores,
            gateway=lambda **kwargs: gateway,
        ),
        "core_memory": CoreMemory(items=[]),
        "updated_state": updated_state,
        "scene_facts": SceneFactSet(scene_focus="深夜码头。"),
        "recent_summaries": [],
        "events": [],
    }


class ScriptedDirectorGateway:
    """A scripted gateway that answers each Director task from a fixed payload.

    Records the task ids and input payloads it was called with so tests can
    assert on what was observed and executed.
    """

    def __init__(
        self,
        *,
        decision: dict,
        npc_output: dict | None = None,
        backfill_output: dict | None = None,
        event_selection_output: dict | None = None,
        event_review_output: dict | None = None,
        setting_output: dict | None = None,
    ):
        self.decision = decision
        self.npc_output = npc_output
        self.backfill_output = backfill_output
        self.event_selection_output = event_selection_output
        self.event_review_output = event_review_output
        self.setting_output = setting_output
        self.tasks: list[str] = []
        self.input_data: list[dict] = []

    def run(self, **kwargs):
        task = kwargs["task"]
        self.tasks.append(task)
        self.input_data.append(kwargs.get("input_data") or {})
        if task in {
            "director_agent_decide_generation",
            "director_agent_decide_confirmation",
        }:
            payload = dict(self.decision)
            payload.setdefault("requests", [])
        elif task == "director_event_select" and (
            self.event_selection_output is not None
        ):
            payload = self.event_selection_output
        elif task.startswith("director_event_review_") and (
            self.event_review_output is not None
        ):
            payload = self.event_review_output
        elif task == "director_npc_create" and self.npc_output is not None:
            payload = self.npc_output
        elif task == "npc_profile_backfill" and self.backfill_output is not None:
            payload = self.backfill_output
        elif task == "director_setting_create" and self.setting_output is not None:
            payload = (
                self.setting_output(kwargs.get("input_data") or {})
                if callable(self.setting_output)
                else self.setting_output
            )
        else:
            raise AssertionError(f"unexpected Director integration task: {task}")
        result = kwargs["output_schema"].model_validate(payload)
        validator = kwargs.get("output_validator")
        if validator is not None:
            validator(result)
        return result


def make_review_selection(title="无灯船", **changes):
    from app.domain.director_event.schemas import (
        DirectorEventSelectionResult,
        EventHook,
        WorldImpact,
    )

    values = dict(
        current_story_direction=f"{title} 出现异常动静。",
        conception="独立世界事件。",
        title=title,
        duration_phases=7,
        actor_roles=[],
        subject="陷空山脚猎户",
        intent="查明灵气异动",
        method="组队搜山",
        macro_intent="猎户为了生存不择手段。",
        hook_pool=[
            EventHook(
                hook_id="hook_sound",
                kind="auditory",
                content="深夜水面传来无人摇橹声。",
                trigger_anchors=["旧码头"],
            )
        ],
        world_impact=WorldImpact(description="外围变为凡人活动区。"),
    )
    values.update(changes)
    return DirectorEventSelectionResult(**values)
