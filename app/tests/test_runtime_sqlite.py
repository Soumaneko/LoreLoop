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


@pytest.mark.parametrize("existing_data", [False, True], ids=["empty", "unrelated-database"])
def test_unreadable_control_database_is_preserved(tmp_path, existing_data):
    from app.runtime.paths import CampaignPaths
    from app.runtime.turn_history import ensure_control_database

    paths = CampaignPaths.from_data_dir(tmp_path)
    control = tmp_path / ".runtime" / "control.db"
    control.parent.mkdir()
    with sqlite3.connect(control) as conn:
        if existing_data:
            conn.execute("CREATE TABLE owner_notes (note TEXT)")
            conn.execute("INSERT INTO owner_notes VALUES ('keep me')")
    with pytest.raises(RuntimeError):
        ensure_control_database(paths)
    assert control.is_file()
    if existing_data:
        with sqlite3.connect(control) as conn:
            assert conn.execute("SELECT note FROM owner_notes").fetchone()[0] == "keep me"
