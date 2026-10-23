from app.tests.factories.stores import open_npc_profile_store
import pytest
from app.domain.confirmed_history import history_document_text_hash
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.npc_profile.changes import AcceptedNPCScalarChange
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from types import SimpleNamespace
from app.domain.continuity import ContinuityProjection
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.scene_summary.schemas import SceneSummary
from app.graph.confirmation_lifecycle import (
    ConfirmationCommitServices,
    ConfirmationCommitter,
)
from app.runtime.paths import CampaignPaths
from app.runtime.uow import CampaignUnitOfWork
from app.runtime.turn_history import CampaignHistory


def _profile(npc_id: str = "npc-a", name: str = "顾青") -> NPCProfile:
    return NPCProfile(npc_id=npc_id, name=name, updated_at="2026-08-13T00:00:00Z")


def _document(
    evidence_id: str = "turn_3_p001",
    text: str = "她接过铜印，正式成为北门巡检。",
    *,
    turn_index: int = 3,
    scene_id: str = "scene-3",
    source_type: str = "scene_paragraph",
) -> HistoryDocument:
    return HistoryDocument(
        document_id=evidence_id,
        source_type=source_type,
        turn_index=turn_index,
        scene_id=scene_id,
        paragraph_index=1 if source_type == "scene_paragraph" else None,
        text=text,
    )


def _role_change(
    value: str = "北门巡检",
    *,
    npc_id: str = "npc-a",
    turn_index: int = 3,
    evidence_ids: tuple[str, ...] = ("turn_3_p001",),
    basis: str | None = None,
    old_value: str | None = None,
) -> AcceptedNPCScalarChange:
    # 生产 writer 对累积稳定字段必写 change_basis（首设即整值）；helper 默认
    # 显式携带 basis，避免测试掩盖"写入 API 必须提供 change_basis"的契约。
    return AcceptedNPCScalarChange(
        npc_id=npc_id,
        canonical_name="顾青",
        field="role",
        old_value=old_value,
        new_value=value,
        change_basis=basis if basis is not None else value,
        evidence_ids=evidence_ids,
        turn_index=turn_index,
        source="proposal",
    )


def _record(
    store: NPCProfileStore,
    changes,
    documents,
    *,
    turn_index: int = 3,
    scene_id: str = "scene-3",
    reset: bool = False,
):
    for change in changes:
        if not isinstance(change, AcceptedNPCScalarChange):
            continue
        if change.field not in {
            "role", "appearance", "faction", "last_scene_summary"
        }:
            continue
        profile = store.get_by_id(change.npc_id)
        if profile is None:
            continue
        setattr(profile, change.field, change.new_value)
        if change.field == "last_scene_summary":
            profile.last_scene_turn = change.turn_index
        store.upsert(profile)
    return store.record_accepted_field_changes(
        changes=changes,
        evidence_documents=documents,
        turn_index=turn_index,
        scene_id=scene_id,
        reset_before_record=reset,
    )


def test_record_rejects_change_that_does_not_match_committed_snapshot():
    document = _document()
    with open_npc_profile_store(":memory:") as store:
        store.upsert(_profile())

        with pytest.raises(ValueError, match="committed profile snapshot"):
            store.record_accepted_field_changes(
                changes=[_role_change()],
                evidence_documents=[document],
                turn_index=3,
                scene_id="scene-3",
            )

        assert store.list_field_change_logs() == []
        assert store.field_change_batch_hash(3) is None


def test_store_rejects_last_scene_summary_turn_mismatch():
    document = _document(turn_index=3)
    with open_npc_profile_store(":memory:") as store:
        profile = _profile()
        profile.last_scene_summary = "她当众接任北门巡检。"
        profile.last_scene_turn = 2
        store.upsert(profile)
        change = AcceptedNPCScalarChange(
            npc_id="npc-a",
            canonical_name="顾青",
            field="last_scene_summary",
            old_value=None,
            new_value="她当众接任北门巡检。",
            evidence_ids=(document.document_id,),
            turn_index=3,
            source="proposal",
        )
        with pytest.raises(ValueError, match="last_scene_turn"):
            store.record_accepted_field_changes(
                changes=[change],
                evidence_documents=[document],
                turn_index=3,
                scene_id="scene-3",
            )
        assert store.list_field_change_logs() == []
        assert store.field_change_batch_hash(3) is None


def test_record_round_trip_preserves_exact_values_position_and_hash():
    first = _document()
    second = _document("turn_3_p002", "众人当场确认了这项任命。")
    long_value = "北门巡检" + "甲" * 600
    change = _role_change(
        long_value,
        evidence_ids=(first.document_id, second.document_id),
        basis="长值仍需短 basis",
    )

    with open_npc_profile_store(":memory:") as store:
        store.upsert(_profile())
        result = _record(store, [change], [second, first])
        reverse = store.field_change_logs_for_evidence([second.document_id])

    assert result.replayed is False
    assert result.reset_performed is False
    assert len(result.logs) == 1
    log = result.logs[0]
    assert log.new_value == long_value
    assert [item.position for item in log.evidence] == [0, 1]
    assert [item.evidence_id for item in log.evidence] == [
        first.document_id,
        second.document_id,
    ]
    assert [item.evidence_hash for item in log.evidence] == [
        history_document_text_hash(first),
        history_document_text_hash(second),
    ]
    assert [item.log_id for item in reverse] == [log.log_id]


def test_record_accepts_scene_summary_with_six_evidence():
    documents = [
        _document(f"turn_3_p00{index}", f"第 {index} 段正文。")
        for index in range(1, 7)
    ]
    change = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="last_scene_summary",
        old_value=None,
        new_value="顾青连续确认了六项事务，并逐一登记在册。",
        evidence_ids=tuple(document.document_id for document in documents),
        turn_index=3,
        source="proposal",
    )

    with open_npc_profile_store(":memory:") as store:
        store.upsert(_profile())
        result = _record(store, [change], documents)

    assert len(result.logs) == 1
    log = result.logs[0]
    assert [item.position for item in log.evidence] == list(range(6))
    assert [item.evidence_id for item in log.evidence] == [
        document.document_id for document in documents
    ]


def _services(tmp_path) -> tuple[CampaignUnitOfWork, ConfirmationCommitServices]:
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(tmp_path / "save"))
    return stores, ConfirmationCommitServices(
        current_state_store=stores.current_state,
        short_term_memory_store=stores.short_term,
        confirmed_history_store=stores.confirmed_history,
        npc_profile_store=stores.npc_profile,
        continuity_store=stores.continuity,
        scene_summary_store=stores.scene_summary,
    )


def _confirmation_state(*, cache: dict, write_db: bool = True, reset: bool = False) -> dict:
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


def test_confirmation_commits_npc_field_logs_from_persisted_history(tmp_path):
    stores, services = _services(tmp_path)
    with stores.npc_profile as npc_store:
        npc_store.upsert(_profile().model_copy(update={"role": "北门巡检"}))
    cache = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(
                accepted_changes=[_role_change(basis="改任北门巡检", old_value="守卫")]
            )
        )
    }

    ConfirmationCommitter(services).commit(_confirmation_state(cache=cache))

    with stores.npc_profile as npc_store:
        logs = npc_store.list_field_change_logs(turn_index=3)
        assert [(item.npc_id, item.field_name) for item in logs] == [
            ("npc-a", "role")
        ]
        assert [item.evidence[0].evidence_id for item in logs] == [
            "turn_3_p001"
        ]


def test_confirmation_records_empty_npc_field_batch_and_dry_run_does_not(
    tmp_path,
):
    stores, services = _services(tmp_path)
    empty_cache: dict = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(accepted_changes=[])
        )
    }
    ConfirmationCommitter(services).commit(_confirmation_state(cache=empty_cache))

    with stores.npc_profile as npc_store:
        assert npc_store.field_change_batch_hash(3) is not None

    dry_stores, dry_services = _services(tmp_path / "dry")
    dry_cache: dict = {}
    ConfirmationCommitter(dry_services).commit(
        _confirmation_state(cache=dry_cache, write_db=False)
    )
    with dry_stores.npc_profile as npc_store:
        assert npc_store.field_change_batch_hash(3) is None


def test_confirmation_rejects_missing_npc_turn_result(tmp_path):
    stores, services = _services(tmp_path)
    cache: dict = {}

    with pytest.raises(RuntimeError, match="missing npc_turn_result"):
        ConfirmationCommitter(services).commit(_confirmation_state(cache=cache))

    with stores.npc_profile as npc_store:
        assert npc_store.field_change_batch_hash(3) is None


def test_opening_reset_clears_older_npc_field_history(tmp_path):
    stores, services = _services(tmp_path)
    with stores.npc_profile as npc_store:
        npc_store.upsert(_profile().model_copy(update={"role": "北门巡检"}))
    first_cache = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(
                accepted_changes=[_role_change(basis="改任北门巡检", old_value="守卫")]
            )
        )
    }
    committer = ConfirmationCommitter(services)
    committer.commit(_confirmation_state(cache=first_cache))

    reset_cache: dict = {
        "npc_turn_result": SimpleNamespace(
            process_result=SimpleNamespace(accepted_changes=[])
        )
    }
    committer.commit(_confirmation_state(cache=reset_cache, reset=True))

    with stores.npc_profile as npc_store:
        assert npc_store.list_field_change_logs() == []
        assert npc_store.field_change_batch_hash(3) is not None


def test_failed_confirmation_transaction_rolls_back_npc_field_history(tmp_path):
    data_dir = tmp_path / "save"
    from app.runtime.campaign_migration import ensure_campaign_database
    ensure_campaign_database(CampaignPaths.from_data_dir(data_dir))
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
    with stores.npc_profile as npc_store:
        npc_store.upsert(_profile().model_copy(update={"role": "北门巡检"}))
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-3",
        kind="turn",
        group_id="group-3",
        turn_index=3,
        payload={"turn_id": "turn-3"},
    )
    history.begin_confirmation("turn-3", write_db=True, scene_text=None)
    with pytest.raises(RuntimeError, match="commit failed"):
        with stores.transaction():
            with stores.npc_profile as npc_store:
                npc_store.record_accepted_field_changes(
                    changes=[_role_change(basis="改任北门巡检", old_value="守卫")],
                    evidence_documents=[_document()],
                    turn_index=3,
                    scene_id="scene-3",
                )
                assert len(npc_store.list_field_change_logs()) == 1

            raise RuntimeError("commit failed")
    stores.close()
    history.fail_confirmation("turn-3", RuntimeError("commit failed"))

    stores2 = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
    with stores2.npc_profile as npc_store:
        assert npc_store.list_field_change_logs() == []
        assert npc_store.field_change_batch_hash(3) is None


def test_undo_redo_restore_npc_field_history_with_profile_database(tmp_path):
    data_dir = tmp_path / "save"
    from app.runtime.campaign_migration import ensure_campaign_database
    ensure_campaign_database(CampaignPaths.from_data_dir(data_dir))
    stores = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
    with stores.npc_profile as npc_store:
        npc_store.upsert(_profile().model_copy(update={"role": "北门巡检"}))
    history = CampaignHistory(data_dir)
    history.save_pending(
        turn_id="turn-3",
        kind="turn",
        group_id="group-3",
        turn_index=3,
        payload={"turn_id": "turn-3"},
    )
    history.begin_confirmation("turn-3", write_db=True, scene_text=None)
    with stores.npc_profile as npc_store:
        npc_store.record_accepted_field_changes(
            changes=[_role_change(basis="改任北门巡检", old_value="守卫")],
            evidence_documents=[_document()],
            turn_index=3,
            scene_id="scene-3",
        )
    with history._campaign_connect() as campaign:
        campaign.execute("BEGIN")
        history.record_confirmation(campaign, "turn-3", scene_text="场景正文")
    history.complete_confirmation("turn-3", "group-3")

    stores.close()
    history.undo()
    stores2 = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
    with stores2.npc_profile as npc_store:
        assert npc_store.list_field_change_logs() == []
    stores2.close()

    history.redo()
    stores3 = CampaignUnitOfWork(CampaignPaths.from_data_dir(data_dir))
    with stores3.npc_profile as npc_store:
        assert len(npc_store.list_field_change_logs()) == 1
