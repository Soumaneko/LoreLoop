from app.runtime.stores import open_director_log_store
import json
from pathlib import Path

from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.director.log import (
    assemble_director_log_entry,
    summarize_decision,
)
from app.domain.director.schemas import (
    DirectorDecisionBatch,
    DirectorGuidance,
    DirectorSkillRequest,
)
from app.domain.director_npc import DirectorNPCDraft, KnownAbility
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer import SceneWriterResult
from app.domain.turn_resolution.schemas import TurnResolutionResult
from app.runtime.paths import CampaignPaths
from app.runtime.session import PendingTurn, get_campaign_runtime
from app.runtime.uow import CampaignUnitOfWork


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_001", location="测试地点"),
        player=PlayerState(name="主角"),
    )


def _request(skill: str, arguments: dict, guidance=None) -> DirectorSkillRequest:
    return DirectorSkillRequest(
        request_id=f"req_{skill}",
        skill=skill,
        arguments=arguments,
        guidance=guidance or "方向",
    )


def test_summarize_decision_covers_every_skill(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_npc as store:
        draft = DirectorNPCDraft(
            name="林澈",
            role="社区医生",
            appearance="深色风衣。",
            personality_trait="见人先问诊，习惯主动掌握对方近况",
            known_abilities=[KnownAbility(name="急救", description="处理外伤与常见急症")],
            core_motivation="保护社区居民。",
            hidden_secret="曾目击旧案。",
            conflict_style="先协商后行动。",
            social_style="克制而友善。",
            created_turn=1,
        )
        store.save(draft)
    with stores.npc_profile as store:
        npc_id = store.upsert(NPCProfile(name="顾晚棠", role="阁主"))

    generation_decision = DirectorDecisionBatch(
        reasoning="调度若干资源。",
        requests=[
            _request(
                "introduce_npc",
                {"npc_id": draft.draft_id, "view_mode": "face_to_face"},
            ),
            _request(
                "upgrade_disclosure",
                {"npc_id": npc_id, "new_disclosure_level": "intimate"},
            ),
            _request("present_event", {"event_id": "event_x"}),
            _request("foreshadow_setting", {"setting_id": "setting_x"}),
            _request("intrude_engaged_event", {"event_id": "event_x"}),
        ],
    )
    confirmation_decision = DirectorDecisionBatch(
        reasoning="创作资源。",
        requests=[
            _request(
                "create_npc",
                {"setting_requirements": [{"kind": "faction"}]},
                guidance=DirectorGuidance(
                    name="柴行简",
                    purpose="加入商盟。",
                    description="商盟执事。",
                ),
            ),
            _request(
                "create_event",
                {
                    "role": "arc",
                    "actors": [{"source": "new"}, {"source": "existing"}],
                    "setting_requirements": [{"kind": "location"}],
                },
            ),
            _request(
                "enrich_npc",
                {
                    "npc_id": npc_id,
                    "setting_requirements": [{"kind": "faction"}],
                },
            ),
            _request(
                "bind_event_actor",
                {"event_id": "event_x", "npc_id": draft.draft_id},
            ),
        ],
    )

    measures = "\n".join([
        summarize_decision(generation_decision, stores),
        summarize_decision(confirmation_decision, stores),
    ])

    assert "引入NPC「林澈」(face_to_face)" in measures
    assert "披露升级「顾晚棠」→intimate" in measures
    assert "呈现事件「event_x」" in measures
    assert "暗示设定「setting_x」" in measures
    assert "引入事件「event_x」" in measures
    assert "创建NPC「柴行简」依托faction" in measures
    assert "创建事件(arc) 演员2 依托location" in measures
    assert "补全NPC「顾晚棠」依托faction" in measures
    assert "绑定演员「event_x←林澈」" in measures


def test_summarize_decision_empty_batch(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    decision = DirectorDecisionBatch(reasoning="本轮无需干预。")
    assert summarize_decision(decision, stores) == "无措施"


def test_assemble_entry_shape(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    decision = DirectorDecisionBatch(reasoning="引入林澈。")
    entry = assemble_director_log_entry(
        turn_index=3,
        phase="generation",
        decision=decision,
        stores=stores,
    )
    assert entry == {
        "turn_index": 3,
        "phase": "generation",
        "reasoning": "引入林澈。",
        "measures": "无措施",
    }


def test_recent_director_log_store_upserts_prunes_and_orders():
    with open_director_log_store() as store:
        store.save_entries([
            {"turn_index": 1, "phase": "generation", "reasoning": "g1", "measures": "m1"},
            {"turn_index": 1, "phase": "confirmation", "reasoning": "c1", "measures": "m1c"},
            {"turn_index": 2, "phase": "generation", "reasoning": "g2", "measures": "m2"},
            {"turn_index": 3, "phase": "generation", "reasoning": "g3", "measures": "m3"},
            {"turn_index": 4, "phase": "generation", "reasoning": "g4", "measures": "m4"},
            {"turn_index": 4, "phase": "confirmation", "reasoning": "c4", "measures": "m4c"},
            {"turn_index": 5, "phase": "generation", "reasoning": "g5", "measures": "m5"},
            {"turn_index": 5, "phase": "confirmation", "reasoning": "c5", "measures": "m5c"},
        ])

        assert store.count() == 5

        recent = store.recent()
        assert len(recent) == 5
        assert recent[0]["turn_index"] == 5
        assert recent[0]["phase"] == "confirmation"
        assert recent[1]["turn_index"] == 5
        assert recent[1]["phase"] == "generation"

        store.save_entries([{
            "turn_index": 5,
            "phase": "confirmation",
            "reasoning": "c5b",
            "measures": "m5cb",
        }])
        overwritten = next(
            item for item in store.recent()
            if item["turn_index"] == 5 and item["phase"] == "confirmation"
        )
        assert overwritten["reasoning"] == "c5b"


def test_pending_turn_persists_director_log_entry():
    runtime = get_campaign_runtime()
    state = _state()
    pending = PendingTurn(
        turn_id="turn-log",
        group_id="turn-log",
        version_index=1,
        user_input="观察四周",
        difficulty="普通",
        max_chars=900,
        experience_mode="creator",
        state_before=state,
        context=NarrativeComposerContext(
            user_input="观察四周",
            current_state=state,
            turn_resolution=TurnResolutionResult(),
            core_memory=CoreMemory(items=[]),
        ),
        outline=PlotOutlineResult(
            planning_reasoning="计划",
            scene_stopping_point="停止点",
        ),
        scene_result=SceneWriterResult(scene_text="正文"),
        turn_index=3,
        director_log_entry={
            "turn_index": 3,
            "phase": "generation",
            "reasoning": "引入林澈。",
            "measures": "引入NPC「林澈」(face_to_face)",
        },
    )
    runtime.pending_turns.add(pending)
    try:
        records = runtime.history.pending_records()
        record = next(
            item for item in records if item["turn_id"] == "turn-log"
        )
        payload = json.loads(record["payload_json"])
        assert payload["director_log_entry"]["phase"] == "generation"
        assert "林澈" in payload["director_log_entry"]["measures"]
    finally:
        runtime.pending_turns.remove("turn-log")


def test_shared_decide_prompt_exposes_recent_director_log_section():
    prompt = Path(
        "app/prompts/director/_shared/director_decide_shared_v1.txt"
    ).read_text(encoding="utf-8")
    assert "近期导演工作日志" in prompt
    assert "{recent_director_log_json}" in prompt
    assert "不要重复执行日志中已经执行过的措施" in prompt


def test_finalizer_writes_gen_and_confirm_entries(configure_runtime_services):
    from types import SimpleNamespace

    from app.graph.confirmation_lifecycle import ConfirmationFinalizer

    services = configure_runtime_services()
    runtime = get_campaign_runtime()
    finalizer = ConfirmationFinalizer(
        runtime=runtime,
        committer=None,
        scheduler=None,
        event_builder=None,
    )
    state = {
        "write_db": True,
        "trace_id": "trace-log",
        "pipeline_input": SimpleNamespace(
            turn_index=4,
        ),
        "pending": SimpleNamespace(director_log_entry={
            "turn_index": 4,
            "phase": "generation",
            "reasoning": "本回合引入林澈。",
            "measures": "引入NPC「林澈」(face_to_face)",
        }),
        "director_decision": DirectorDecisionBatch(
            reasoning="确认端决定培育事件。",
            requests=[],
        ),
    }

    finalizer._write_recent_director_log(state)

    with services.stores.director_log as log_store:
        recent = log_store.recent()
    assert {entry["phase"] for entry in recent} == {
        "generation",
        "confirmation",
    }
    assert all(entry["turn_index"] == 4 for entry in recent)


def test_confirmation_entry_records_actual_execution_results(tmp_path):
    from app.domain.director.schemas import (
        DirectorSkillExecution,
        DirectorSkillExecutionBatch,
    )

    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    executions = DirectorSkillExecutionBatch(executions=[
        DirectorSkillExecution(
            request_id="r1",
            skill="create_event",
            output={
                "event_id": "director_event_abc",
                "role": "arc",
                "new_npc_ids": ["director_npc_1"],
                "actor_names": ["周汐"],
            },
        ),
        DirectorSkillExecution(
            request_id="r2",
            skill="create_npc",
            output={"npc_id": "director_npc_2", "name": "柴行简"},
        ),
    ])
    entry = assemble_director_log_entry(
        turn_index=4,
        phase="confirmation",
        decision=DirectorDecisionBatch(reasoning="创建事件与人物。", requests=[]),
        stores=stores,
        executions=executions,
    )

    assert "director_event_abc" in entry["measures"]
    assert "arc" in entry["measures"]
    assert "演员:周汐" in entry["measures"]
    assert "创建NPC「柴行简」" in entry["measures"]