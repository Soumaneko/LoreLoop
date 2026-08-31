from app.tests.factories.stores import open_npc_profile_store
import sqlite3

import pytest

from app.runtime.services import RuntimeServices
from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction


def test_transaction_rolls_back_all_statements():
    connection = connect_sqlite(":memory:")
    connection.execute("CREATE TABLE items(value TEXT)")
    connection.commit()

    with pytest.raises(RuntimeError):
        with transaction(connection):
            connection.execute("INSERT INTO items VALUES ('first')")
            connection.execute("INSERT INTO items VALUES ('second')")
            raise RuntimeError("stop")

    assert connection.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    connection.close()


def test_schema_version_rejects_newer_database():
    connection = connect_sqlite(":memory:")
    ensure_schema_version(connection, "test", 2)
    connection.commit()

    with pytest.raises(RuntimeError, match="newer"):
        ensure_schema_version(connection, "test", 1)
    connection.close()


def test_npc_profile_store_rejects_pre_versioned_columns(tmp_path):
    db_path = tmp_path / "pre_versioned.db"
    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE npc_profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            profile_json TEXT NOT NULL,
            source_scene_ids_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.5,
            updated_at TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    connection.commit()
    connection.close()

    with pytest.raises(
        RuntimeError,
        match="current NPCProfile contract",
    ):
        open_npc_profile_store(str(db_path))


def test_runtime_services_creates_trace_isolated_gateways():
    class Gateway:
        def __init__(self):
            self.context = {}

        def set_trace_context(self, **context):
            self.context.update(context)

    services = RuntimeServices(
        stores=object(),
        gateway_factory=Gateway,
        jobs=object(),
    )

    first = services.gateway(session_type="generation", turn_index=3)
    second = services.gateway(session_type="maintenance", turn_index=4)

    assert first is not second
    assert first.context == {"session_type": "generation", "turn_index": 3}
    assert second.context == {"session_type": "maintenance", "turn_index": 4}