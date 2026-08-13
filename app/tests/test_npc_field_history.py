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
) -> AcceptedNPCScalarChange:
    return AcceptedNPCScalarChange(
        npc_id=npc_id,
        canonical_name="顾青",
        field="role",
        old_value=None,
        new_value=value,
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
            "public_summary", "role", "appearance", "faction"
        }:
            continue
        profile = store.get_by_id(change.npc_id)
        if profile is None:
            continue
        setattr(profile, change.field, change.new_value)
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
    with NPCProfileStore(":memory:") as store:
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


def test_fresh_store_creates_v7_field_history_schema(tmp_path):
    db_path = tmp_path / "npcs.db"
    with NPCProfileStore(str(db_path)):
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

    assert version == 7
    assert {
        "npc_field_change_logs",
        "npc_field_change_evidence",
        "npc_field_change_processed_turns",
    } <= tables


def test_v6_migration_is_additive_and_preserves_profile_json(tmp_path):
    db_path = tmp_path / "v6.db"
    profile = _profile()
    raw_profile_json = profile.model_dump_json()
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
        VALUES ('npc_profile', 6);
        """
    )
    connection.execute(
        """
        INSERT INTO npc_profiles(
            id, name, aliases_json, profile_json, source_scene_ids_json,
            updated_at, active, name_hit_count, alias_hit_counts_json
        ) VALUES (?, ?, '[]', ?, '[]', ?, 1, 0, '{}')
        """,
        (profile.npc_id, profile.name, raw_profile_json, profile.updated_at),
    )
    connection.commit()
    connection.close()

    with NPCProfileStore(str(db_path)) as store:
        assert store.get_by_id(profile.npc_id) is not None

    connection = sqlite3.connect(db_path)
    migrated_json = connection.execute(
        "SELECT profile_json FROM npc_profiles WHERE id = ?",
        (profile.npc_id,),
    ).fetchone()[0]
    version = connection.execute(
        "SELECT version FROM schema_versions WHERE component='npc_profile'"
    ).fetchone()[0]
    connection.close()

    assert migrated_json == raw_profile_json
    assert version == 7


def test_failed_v6_migration_rolls_back_version_and_new_tables(tmp_path):
    db_path = tmp_path / "broken-v6.db"
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
        VALUES ('npc_profile', 6);
        CREATE TABLE npc_field_change_logs (log_id INTEGER PRIMARY KEY);
        """
    )
    connection.close()

    with pytest.raises((RuntimeError, sqlite3.OperationalError)):
        NPCProfileStore(str(db_path))

    connection = sqlite3.connect(db_path)
    version = connection.execute(
        "SELECT version FROM schema_versions WHERE component='npc_profile'"
    ).fetchone()[0]
    evidence_table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='npc_field_change_evidence'
        """
    ).fetchone()
    connection.close()
    assert version == 6
    assert evidence_table is None


def test_record_round_trip_preserves_exact_values_position_and_hash():
    first = _document()
    second = _document("turn_3_p002", "众人当场确认了这项任命。")
    long_value = "北门巡检" + "甲" * 600
    change = _role_change(
        long_value,
        evidence_ids=(first.document_id, second.document_id),
    )

    with NPCProfileStore(":memory:") as store:
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


@pytest.mark.parametrize(
    ("field_name", "new_value", "summary_delta"),
    [
        ("role", "北门巡检", None),
        ("appearance", "左颊有新月形伤痕", None),
        ("faction", "白鹿堂", None),
        ("public_summary", "新任北门巡检。", "正式接任北门巡检。"),
    ],
)
def test_all_bridgeable_scalar_fields_round_trip(
    field_name,
    new_value,
    summary_delta,
):
    document = _document()
    change = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field=field_name,
        old_value="旧值",
        new_value=new_value,
        evidence_ids=(document.document_id,),
        turn_index=3,
        summary_delta=summary_delta,
        source="proposal",
    )
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = _record(store, [change], [document])

    assert result.logs[0].field_name == field_name
    assert result.logs[0].old_value == "旧值"
    assert result.logs[0].new_value == new_value
    assert result.logs[0].summary_delta == summary_delta


def test_only_proposal_stable_scalar_changes_are_recorded():
    document = _document()
    current_intent = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="current_intent",
        new_value="巡查北门",
        turn_index=3,
        source="proposal",
    )
    synced_location = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="last_known_location",
        new_value="北门",
        turn_index=3,
        source="current_state_sync",
    )
    ability = AcceptedNPCAbilityAddChange(
        npc_id="npc-a",
        canonical_name="顾青",
        additions=[KnownAbility(name="巡城经验", description="熟悉城门巡查")],
        evidence_ids=(document.document_id,),
        turn_index=3,
    )

    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        result = _record(
            store,
            [current_intent, synced_location, ability],
            [document],
        )

        assert result.logs == []
        assert store.field_change_batch_hash(3) == result.batch_hash


def test_batch_hash_is_document_order_independent_but_change_order_sensitive():
    first = _document()
    second = _document("turn_3_p002", "她在名册上确认归属白鹿堂。")
    role = _role_change(evidence_ids=(first.document_id,))
    faction = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="faction",
        new_value="白鹿堂",
        evidence_ids=(second.document_id,),
        turn_index=3,
    )

    forward = prepare_accepted_field_changes(
        changes=[role, faction],
        evidence_documents=[first, second],
        turn_index=3,
        scene_id="scene-3",
    )
    reordered_documents = prepare_accepted_field_changes(
        changes=[role, faction],
        evidence_documents=[second, first],
        turn_index=3,
        scene_id="scene-3",
    )
    reversed_changes = prepare_accepted_field_changes(
        changes=[faction, role],
        evidence_documents=[first, second],
        turn_index=3,
        scene_id="scene-3",
    )
    changed_text = prepare_accepted_field_changes(
        changes=[role, faction],
        evidence_documents=[
            _document(text="她接过另一枚铜印，正式成为北门巡检。"),
            second,
        ],
        turn_index=3,
        scene_id="scene-3",
    )

    assert reordered_documents.batch_hash == forward.batch_hash
    assert reversed_changes.batch_hash != forward.batch_hash
    assert changed_text.batch_hash != forward.batch_hash


@pytest.mark.parametrize(
    ("documents", "match"),
    [
        ([], "unknown HistoryDocument"),
        ([_document(turn_index=2)], "another turn"),
        ([_document(scene_id="other-scene")], "another scene"),
        ([_document(source_type="player_input")], "scene paragraph"),
    ],
)
def test_pointer_validation_rejects_whole_batch(documents, match):
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        with pytest.raises(ValueError, match=match):
            _record(store, [_role_change()], documents)

        assert store.list_field_change_logs() == []
        assert store.field_change_batch_hash(3) is None


def test_duplicate_node_or_evidence_rejects_whole_batch():
    document = _document()
    duplicate_evidence = _role_change(
        evidence_ids=(document.document_id, document.document_id),
    )
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        with pytest.raises(ValueError, match="duplicate evidence"):
            _record(store, [duplicate_evidence], [document])
        with pytest.raises(ValueError, match="duplicate NPC field-change node"):
            _record(store, [_role_change(), _role_change("守门官")], [document])
        assert store.list_field_change_logs() == []


def test_unknown_profile_rejects_before_resetting_existing_history():
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        original = _record(store, [_role_change()], [document])
        unknown = _role_change("客卿", npc_id="missing")

        with pytest.raises(ValueError, match="unknown profiles"):
            _record(store, [unknown], [document], reset=True)

        assert store.field_change_batch_hash(3) == original.batch_hash
        assert len(store.list_field_change_logs()) == 1


def test_same_replay_is_noop_and_different_replay_fails_closed():
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        first = _record(store, [_role_change()], [document])
        replay = _record(store, [_role_change()], [document])

        assert replay.replayed is True
        assert replay.logs[0].log_id == first.logs[0].log_id
        with pytest.raises(ValueError, match="different batch"):
            _record(store, [_role_change("守门官")], [document])
        assert store.list_field_change_logs()[0].new_value == "北门巡检"


def test_different_batch_reset_replaces_same_turn_atomically():
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        first = _record(store, [_role_change()], [document])
        replacement = _record(
            store,
            [_role_change("守门官")],
            [document],
            reset=True,
        )

        assert replacement.reset_performed is True
        assert replacement.replayed is False
        assert replacement.batch_hash != first.batch_hash
        assert [log.new_value for log in store.list_field_change_logs()] == [
            "守门官"
        ]


def test_reset_rolls_back_old_batch_when_insert_fails(monkeypatch):
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        first = _record(store, [_role_change()], [document])
        original_insert = store._insert_prepared_field_batch

        def insert_then_fail(prepared):
            original_insert(prepared)
            raise RuntimeError("simulated post-insert failure")

        monkeypatch.setattr(store, "_insert_prepared_field_batch", insert_then_fail)
        with pytest.raises(RuntimeError, match="post-insert failure"):
            _record(
                store,
                [_role_change("守门官")],
                [document],
                reset=True,
            )

        assert store.field_change_batch_hash(3) == first.batch_hash
        assert [log.new_value for log in store.list_field_change_logs()] == [
            "北门巡检"
        ]


def test_reset_atomically_replaces_old_turns_and_marker_gap_retry_is_noop():
    turn_three = _document()
    turn_four = _document(
        "turn_4_p001",
        "她换上白鹿堂制服，正式加入白鹿堂。",
        turn_index=4,
        scene_id="scene-4",
    )
    faction = AcceptedNPCScalarChange(
        npc_id="npc-a",
        canonical_name="顾青",
        field="faction",
        new_value="白鹿堂",
        evidence_ids=(turn_four.document_id,),
        turn_index=4,
    )
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        _record(store, [_role_change()], [turn_three])
        second = _record(
            store,
            [faction],
            [turn_four],
            turn_index=4,
            scene_id="scene-4",
        )
        reset = _record(
            store,
            [faction],
            [turn_four],
            turn_index=4,
            scene_id="scene-4",
            reset=True,
        )
        retry = _record(
            store,
            [faction],
            [turn_four],
            turn_index=4,
            scene_id="scene-4",
            reset=True,
        )

        assert reset.reset_performed is True
        assert reset.replayed is False
        assert retry.replayed is True
        assert retry.reset_performed is False
        assert store.field_change_batch_hash(3) is None
        assert store.field_change_batch_hash(4) == second.batch_hash
        assert [(log.turn_index, log.field_name) for log in store.list_field_change_logs()] == [
            (4, "faction")
        ]


def test_profile_delete_invalidates_whole_affected_turn_batch():
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        store.upsert(_profile("npc-b", "陆衡"))
        other = AcceptedNPCScalarChange(
            npc_id="npc-b",
            canonical_name="陆衡",
            field="role",
            new_value="南仓管事",
            evidence_ids=(document.document_id,),
            turn_index=3,
        )
        _record(store, [_role_change(), other], [document])
        store.delete_by_id("npc-a")

        assert store.list_field_change_logs() == []
        assert store.field_change_logs_for_evidence([document.document_id]) == []
        assert store.field_change_batch_hash(3) is None

        # Recreating the profile allows the complete batch to be recorded;
        # no stale processed marker can misreport an empty replay.
        store.upsert(_profile())
        replay = _record(store, [_role_change(), other], [document])
        assert replay.replayed is False
        assert len(replay.logs) == 2


def test_clear_field_history_keeps_profiles():
    document = _document()
    with NPCProfileStore(":memory:") as store:
        store.upsert(_profile())
        _record(store, [_role_change()], [document])
        store.clear_field_change_history()

        assert store.get_by_id("npc-a") is not None
        assert store.list_field_change_logs() == []
        assert store.field_change_batch_hash(3) is None


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
        VALUES ('npc_profile', 8);
        """
    )
    connection.close()

    with pytest.raises(RuntimeError, match="expected 7, got 8"):
        NPCProfileStore(str(db_path))

    connection = sqlite3.connect(db_path)
    table = connection.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name='npc_field_change_logs'
        """
    ).fetchone()
    connection.close()
    assert table is None


def test_claimed_v7_without_required_constraints_is_rejected(tmp_path):
    db_path = tmp_path / "fake-v7.db"
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
        VALUES ('npc_profile', 7);
        CREATE TABLE npc_field_change_logs (
            log_id INTEGER, npc_id TEXT, field_name TEXT, turn_index INTEGER,
            old_value TEXT, new_value TEXT, summary_delta TEXT, source TEXT
        );
        CREATE TABLE npc_field_change_evidence (
            log_id INTEGER, position INTEGER, evidence_id TEXT,
            evidence_hash TEXT
        );
        CREATE TABLE npc_field_change_processed_turns (
            turn_index INTEGER, batch_hash TEXT
        );
        """
    )
    connection.close()

    with pytest.raises(RuntimeError, match="field-history contract"):
        NPCProfileStore(str(db_path))
