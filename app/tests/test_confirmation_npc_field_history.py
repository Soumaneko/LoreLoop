from types import SimpleNamespace

import pytest

from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.continuity import ContinuityProjection
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.npc_profile.changes import AcceptedNPCScalarChange
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_summary.schemas import SceneSummary
from app.graph.confirmation_lifecycle import (
    ConfirmationCommitServices,
    ConfirmationCommitter,
)
from app.runtime.paths import CampaignPaths
from app.runtime.stores import StoreBundle
from app.runtime.turn_history import CampaignHistory


def _services(tmp_path) -> tuple[StoreBundle, ConfirmationCommitServices]:
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path / "save"))
    return stores, ConfirmationCommitServices(
        current_state_store=stores.current_state,
        short_term_memory_store=stores.short_term,
        confirmed_history_store=stores.confirmed_history,
        npc_profile_store=stores.npc_profile,
        continuity_store=stores.continuity,
        scene_summary_store=stores.scene_summary,
    )


def _state(*, cache: dict, write_db: bool = True, reset: bool = False) -> dict:
    updated_state = CurrentState(
        scene=SceneState(scene_id="scene-3", location="北门"),
        player=PlayerState(name="主角"),
    )
    return {
        "write_db": write_db,
        "pipeline_input": SimpleNamespace(
            turn_index=3,
            user_input="询问她的新职责。",
            scene_result=SimpleNamespace(closing_prompt="接下来怎么办？"),
            pending=SimpleNamespace(token_usage={}, player_resolution=None),
            clear_short_term_before_save=reset,
            clear_confirmed_history_before_save=reset,
            clear_continuity_before_save=reset,
        ),
        "cache": cache,
        "updated_state": updated_state,
        "turn_summary": SceneSummary(
            id="summary-3",
            level=1,
            content="顾青成为北门巡检。",
            scene_ids=["scene-3"],
            turn_start=3,
            turn_end=3,
        ),
        "effective_scene_text": "她接过铜印，正式成为北门巡检。",
        "continuity_projection": ContinuityProjection(),
        "trace_id": "trace-3",
    }


def _accepted_role_change() -> AcceptedNPCScalarChange:
    return AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="role",
        old_value="守卫",
        new_value="北门巡检",
        evidence_ids=("turn_3_p001",),
        turn_index=3,
        source="proposal",
    )


def _evidence_document() -> HistoryDocument:
    return HistoryDocument(
        document_id="turn_3_p001",
        source_type="scene_paragraph",
        turn_index=3,
        scene_id="scene-3",
        paragraph_index=1,
        text="她接过铜印，正式成为北门巡检。",
    )


def test_confirmation_commits_npc_field_logs_from_persisted_history(tmp_path):
    stores, services = _services(tmp_path)
    with stores.npc_profile() as npc_store:
        npc_store.upsert(NPCProfile(
            npc_id="npc-a",
            name="顾青",
            role="北门巡检",
            updated_at="2026-08-13T00:00:00Z",
        ))
    cache = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(
                accepted_changes=[_accepted_role_change()]
            )
        )
    }

    ConfirmationCommitter(services).commit(_state(cache=cache))

    assert cache["confirmed_history_saved"] is True
    assert cache["npc_field_logs_saved"] is True
    with stores.npc_profile() as npc_store:
        logs = npc_store.list_field_change_logs(turn_index=3)
        assert [(item.npc_id, item.field_name) for item in logs] == [
            ("npc-a", "role")
        ]
        assert [item.evidence[0].evidence_id for item in logs] == [
            "turn_3_p001"
        ]


def test_confirmation_npc_field_log_marker_gap_replays_without_duplicates(
    tmp_path,
):
    stores, services = _services(tmp_path)
    with stores.npc_profile() as npc_store:
        npc_store.upsert(NPCProfile(
            npc_id="npc-a",
            name="顾青",
            role="北门巡检",
            updated_at="2026-08-13T00:00:00Z",
        ))
    cache = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(
                accepted_changes=[_accepted_role_change()]
            )
        )
    }
    state = _state(cache=cache)
    committer = ConfirmationCommitter(services)
    committer.commit(state)
    del cache["npc_field_logs_saved"]

    committer.commit(state)

    assert cache["npc_field_logs_saved"] is True
    with stores.npc_profile() as npc_store:
        assert len(npc_store.list_field_change_logs(turn_index=3)) == 1


def test_confirmation_records_empty_npc_field_batch_and_dry_run_does_not(
    tmp_path,
):
    stores, services = _services(tmp_path)
    empty_cache: dict = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(accepted_changes=[])
        )
    }
    ConfirmationCommitter(services).commit(_state(cache=empty_cache))

    assert empty_cache["npc_field_logs_saved"] is True
    with stores.npc_profile() as npc_store:
        assert npc_store.field_change_batch_hash(3) is not None

    dry_stores, dry_services = _services(tmp_path / "dry")
    dry_cache: dict = {}
    ConfirmationCommitter(dry_services).commit(
        _state(cache=dry_cache, write_db=False)
    )
    assert "npc_field_logs_saved" not in dry_cache
    with dry_stores.npc_profile() as npc_store:
        assert npc_store.field_change_batch_hash(3) is None


def test_confirmation_rejects_missing_npc_turn_result(tmp_path):
    stores, services = _services(tmp_path)
    cache: dict = {}

    with pytest.raises(RuntimeError, match="missing npc_turn_result"):
        ConfirmationCommitter(services).commit(_state(cache=cache))

    assert "npc_field_logs_saved" not in cache
    with stores.npc_profile() as npc_store:
        assert npc_store.field_change_batch_hash(3) is None


def test_opening_reset_clears_older_npc_field_history(tmp_path):
    stores, services = _services(tmp_path)
    with stores.npc_profile() as npc_store:
        npc_store.upsert(NPCProfile(
            npc_id="npc-a",
            name="顾青",
            role="北门巡检",
            updated_at="2026-08-13T00:00:00Z",
        ))
    first_cache = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(
                accepted_changes=[_accepted_role_change()]
            )
        )
    }
    committer = ConfirmationCommitter(services)
    committer.commit(_state(cache=first_cache))

    reset_cache: dict = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(accepted_changes=[])
        )
    }
    committer.commit(_state(cache=reset_cache, reset=True))

    with stores.npc_profile() as npc_store:
        assert npc_store.list_field_change_logs() == []
        assert npc_store.field_change_batch_hash(3) is not None


def test_failed_confirmation_snapshot_restores_npc_field_history(tmp_path):
    data_dir = tmp_path / "save"
    stores = StoreBundle(CampaignPaths.from_data_dir(data_dir))
    with stores.npc_profile() as npc_store:
        npc_store.upsert(NPCProfile(
            npc_id="npc-a",
            name="顾青",
            role="北门巡检",
            updated_at="2026-08-13T00:00:00Z",
        ))
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-3",
        kind="turn",
        group_id="group-3",
        turn_index=3,
        payload={"turn_id": "turn-3"},
    )
    history.begin_confirmation("turn-3", write_db=True, scene_text=None)
    with stores.npc_profile() as npc_store:
        npc_store.record_accepted_field_changes(
            changes=[_accepted_role_change()],
            evidence_documents=[_evidence_document()],
            turn_index=3,
            scene_id="scene-3",
        )
        assert len(npc_store.list_field_change_logs()) == 1

    history.fail_confirmation("turn-3", RuntimeError("commit failed"))

    with stores.npc_profile() as npc_store:
        assert npc_store.list_field_change_logs() == []
        assert npc_store.field_change_batch_hash(3) is None


def test_undo_redo_restore_npc_field_history_with_profile_database(tmp_path):
    data_dir = tmp_path / "save"
    stores = StoreBundle(CampaignPaths.from_data_dir(data_dir))
    with stores.npc_profile() as npc_store:
        npc_store.upsert(NPCProfile(
            npc_id="npc-a",
            name="顾青",
            role="北门巡检",
            updated_at="2026-08-13T00:00:00Z",
        ))
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-3",
        kind="turn",
        group_id="group-3",
        turn_index=3,
        payload={"turn_id": "turn-3"},
    )
    history.begin_confirmation("turn-3", write_db=True, scene_text=None)
    with stores.npc_profile() as npc_store:
        npc_store.record_accepted_field_changes(
            changes=[_accepted_role_change()],
            evidence_documents=[_evidence_document()],
            turn_index=3,
            scene_id="scene-3",
        )
    history.complete_confirmation("turn-3", "group-3")

    history.undo()
    with stores.npc_profile() as npc_store:
        assert npc_store.list_field_change_logs() == []

    history.redo()
    with stores.npc_profile() as npc_store:
        assert len(npc_store.list_field_change_logs()) == 1
