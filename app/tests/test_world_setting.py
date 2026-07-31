import sqlite3

from app.domain.world_setting import (
    SettingAnchorPacket,
    SettingSeedCandidate,
    SettingSourceDocument,
    WorldSettingStore,
)


def _save_seed(store: WorldSettingStore) -> str:
    document = SettingSourceDocument(
        source_key="scene:4",
        source_type="scene_text",
        source_ref="scene_4",
        visibility="player_known",
        content="河岸区每晚十点后关闭闸门。",
    )
    [setting_id] = store.apply_extraction(
        [
            SettingSeedCandidate(
                label="河岸区夜禁",
                kind="institution_or_custom",
                basic_concept="河岸区存在固定夜禁。",
                source_key=document.source_key,
                source_span="每晚十点后关闭闸门",
            )
        ],
        source_documents={document.source_key: document},
        turn_index=4,
    )
    return setting_id


def test_setting_store_has_independent_active_rejected_lifecycle():
    store = WorldSettingStore()
    setting_id = _save_seed(store)

    assert store.get_by_id(setting_id).status == "active"
    store.mark_rejected(setting_id, reason="正文证据不足。")

    rejected = store.get_by_id(setting_id)
    assert rejected.status == "rejected"
    assert rejected.rejection_reason == "正文证据不足。"
    assert store.list_active() == []
    store.close()


def test_setting_preparation_does_not_create_or_consume_npc():
    store = WorldSettingStore()
    setting_id = _save_seed(store)
    anchor = SettingAnchorPacket(
        setting_id=setting_id,
        label="河岸区夜禁",
        kind="institution_or_custom",
        definition="河岸区每晚十点关闭闸门。",
        setting_facts=["夜禁由区政府执行。"],
        player_known_facts=[0],
    )

    store.save_preparation(setting_id, anchor)

    setting = store.get_by_id(setting_id)
    assert setting.maturity == "usable"
    assert setting.status == "active"
    assert setting.prepared_anchor == anchor
    assert "consumed_by_director_npc_id" not in setting.model_dump()
    store.close()


def test_store_rejects_reservation_schema_instead_of_migrating_it(tmp_path):
    db_path = tmp_path / "world_settings.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE world_settings (
            setting_id TEXT PRIMARY KEY,
            availability TEXT NOT NULL,
            reserved_by TEXT
        )
        """
    )
    conn.commit()
    conn.close()

    try:
        WorldSettingStore(str(db_path))
    except RuntimeError as exc:
        assert "independent World Setting contract" in str(exc)
    else:
        raise AssertionError("reservation-era Setting schema must be rejected")


def test_setting_domain_does_not_import_director_npc_or_define_reservations():
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/domain/world_setting").glob("*.py")
    )
    assert "director_npc" not in source
    assert "reserved_by" not in source
    assert "availability" not in source
