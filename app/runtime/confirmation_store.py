"""The durable confirmation fact lives in the campaign write transaction."""

import sqlite3

from .sqlite import ensure_exact_schema_version


def ensure_confirmation_schema(connection: sqlite3.Connection) -> None:
    ensure_exact_schema_version(connection, "confirmation", 1)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS turn_confirmations (
            turn_id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            group_id TEXT UNIQUE,
            turn_index INTEGER NOT NULL,
            scene_text TEXT NOT NULL,
            initialization_fingerprint TEXT,
            state_changes_json TEXT,
            state_warnings_json TEXT,
            state_before_json TEXT,
            state_after_json TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
