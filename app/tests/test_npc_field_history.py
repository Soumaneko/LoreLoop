from app.tests.factories.stores import open_npc_profile_store
import json
import sqlite3

import pytest

from app.domain.confirmed_history import history_document_text_hash
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.npc_profile.changes import (
    AcceptedNPCAbilityAddChange,
    AcceptedNPCScalarChange,
)
from app.domain.npc_profile.field_history import prepare_accepted_field_changes
from app.domain.npc_profile.schemas import KnownAbility, NPCProfile
from app.domain.npc_profile.store import NPCProfileStore


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
) -> AcceptedNPCScalarChange:
    # 生产 writer 对累积稳定字段必写 change_basis（首设即整值）；helper 默认
    # 显式携带 basis，避免测试掩盖"写入 API 必须提供 change_basis"的契约。
    return AcceptedNPCScalarChange(
        npc_id=npc_id,
        canonical_name="顾青",
        field="role",
        old_value=None,
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


def test_fresh_store_creates_v9_field_history_schema(tmp_path):
    db_path = tmp_path / "npcs.db"
    with open_npc_profile_store(str(db_path)):
        pass

    connection = sqlite3.connect(db_path)
    version = connection.execute(
        "SELECT version FROM schema_versions WHERE component='npc_profile'"
    ).fetchone()[0]
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    connection.close()

    assert version == 9
    assert {
        "npc_field_change_logs",
        "npc_field_change_evidence",
        "npc_field_change_processed_turns",
    } <= tables


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


def test_newer_or_unknown_version_is_rejected_without_creating_v7_tables(tmp_path):
    db_path = tmp_path / "unknown.db"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE npc_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            profile_json TEXT NOT NULL,
            source_scene_ids_json TEXT NOT NULL DEFAULT '[]',
            updated_at TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            name_hit_count INTEGER NOT NULL DEFAULT 0,
            alias_hit_counts_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE TABLE schema_versions (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO schema_versions(component, version)
        VALUES ('npc_profile', 10);
        """
    )
    connection.close()

    with pytest.raises(RuntimeError, match="expected 9, got 10"):
        open_npc_profile_store(str(db_path))

    connection = sqlite3.connect(db_path)
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='npc_field_change_logs'
        """
    ).fetchone()
    connection.close()
    assert table is None


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
