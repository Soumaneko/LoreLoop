from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


DEFAULT_BUSY_TIMEOUT_MS = 5_000
SUPPORTED_SCHEMA_VERSIONS = {
    "confirmed_history": 1,
    "continuity": 3,
    "current_state": 4,
    "director_event": 11,
    "director_log": 1,
    "director_npc": 5,
    "npc_profile": 8,
    "scene_summary": 1,
    "short_term_memory": 5,
    "world_setting": 12,
}
_savepoint_counter = 0
_savepoint_lock = threading.Lock()


def connect_sqlite(
    db_path: str | Path,
    *,
    busy_timeout_ms: int = DEFAULT_BUSY_TIMEOUT_MS,
) -> sqlite3.Connection:
    """Open a consistently configured application SQLite connection."""
    path = str(db_path)
    connection = sqlite3.connect(
        path,
        check_same_thread=False,
        timeout=busy_timeout_ms / 1_000,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {int(busy_timeout_ms)}")
    if path != ":memory:":
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Commit atomically; use a savepoint when called inside a transaction."""
    if not connection.in_transaction:
        connection.execute("BEGIN")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        return

    savepoint = _next_savepoint()
    connection.execute(f"SAVEPOINT {savepoint}")
    try:
        yield connection
    except BaseException:
        connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        connection.execute(f"RELEASE SAVEPOINT {savepoint}")


def ensure_schema_version(
    connection: sqlite3.Connection,
    component: str,
    version: int,
) -> None:
    """Record the schema owned by a store in its database file."""
    if version < 1:
        raise ValueError("schema version must be >= 1")
    supported = SUPPORTED_SCHEMA_VERSIONS.get(component)
    if supported is not None and version != supported:
        raise RuntimeError(
            f"Code schema version for {component!r} is not registered "
            f"({version} != {supported})"
        )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_versions (
            component TEXT PRIMARY KEY,
            version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    current = connection.execute(
        "SELECT version FROM schema_versions WHERE component = ?",
        (component,),
    ).fetchone()
    if current is not None and int(current["version"]) > version:
        raise RuntimeError(
            f"Database schema for {component!r} is newer than this application "
            f"({current['version']} > {version})"
        )
    connection.execute(
        """
        INSERT INTO schema_versions(component, version)
        VALUES (?, ?)
        ON CONFLICT(component) DO UPDATE SET
            version = excluded.version,
            updated_at = CURRENT_TIMESTAMP
        """,
        (component, version),
    )


def ensure_exact_schema_version(
    connection: sqlite3.Connection,
    component: str,
    version: int,
) -> None:
    """Initialize a fresh store or reject any different persisted contract."""
    versions_table_exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        ("schema_versions",),
    ).fetchone()
    if versions_table_exists is not None:
        current = connection.execute(
            "SELECT version FROM schema_versions WHERE component = ?",
            (component,),
        ).fetchone()
        if current is not None and int(current["version"]) != version:
            raise RuntimeError(
                f"Unsupported {component} database schema: "
                f"expected {version}, got {current['version']}"
            )
    ensure_schema_version(connection, component, version)


def ensure_column(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    """Idempotently add a column while applying a schema migration."""
    columns = {
        row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
    }
    if column not in columns:
        connection.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {declaration}"
        )


def _next_savepoint() -> str:
    global _savepoint_counter
    with _savepoint_lock:
        _savepoint_counter += 1
        return f"app_tx_{_savepoint_counter}"
