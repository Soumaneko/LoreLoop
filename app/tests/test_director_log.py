from app.tests.factories.domains import make_pending_turn
from app.tests.factories.stores import open_director_log_store
import json
from app.domain.director.log import assemble_director_log_entry
from app.domain.director.schemas import DirectorDecisionBatch
from app.runtime.paths import CampaignPaths
from app.runtime.session import get_campaign_runtime
from app.runtime.uow import CampaignUnitOfWork


def test_assemble_entry_shape(tmp_path):
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path))
    decision = DirectorDecisionBatch(reasoning="引入林澈。")
    entry = assemble_director_log_entry(
        turn_index=3,
        phase="generation",
        decision=decision,
        stores=stores,
    )
    assert entry["turn_index"] == 3
    assert entry["phase"] == "generation"
    assert entry["reasoning"] == decision.reasoning


def test_recent_director_log_store_upserts_prunes_and_orders(monkeypatch):
    monkeypatch.setattr(
        "app.domain.director.log_store.utc_now",
        lambda: "2026-01-01T00:00:00+00:00",
    )
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


def test_pending_turn_persists_director_log_entry(campaign_runtime):
    runtime = get_campaign_runtime()
    pending = make_pending_turn(turn_id="turn-log",
        group_id="turn-log",
        turn_index=3,
        director_log_entry={
            "turn_index": 3,
            "phase": "generation",
            "reasoning": "引入林澈。",
            "measures": "引入NPC「林澈」(face_to_face)",
        })
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
        "runtime_services": services,
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
    assert "周汐" in entry["measures"]
    assert "柴行简" in entry["measures"]
