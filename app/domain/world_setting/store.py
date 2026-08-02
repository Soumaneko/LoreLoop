from __future__ import annotations

import json
import sqlite3

from app.runtime.sqlite import connect_sqlite, ensure_exact_schema_version

from .schemas import WorldSetting, utc_now


class WorldSettingStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_settings (
                setting_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL UNIQUE,
                summary TEXT NOT NULL,
                public_facts_json TEXT NOT NULL,
                hidden_facts_json TEXT NOT NULL,
                related_setting_ids_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        expected_columns = {
            "setting_id",
            "kind",
            "name",
            "normalized_name",
            "summary",
            "public_facts_json",
            "hidden_facts_json",
            "related_setting_ids_json",
            "created_turn",
            "created_at",
            "updated_at",
        }
        actual_columns = {
            row["name"]
            for row in self._conn.execute(
                "PRAGMA table_info(world_settings)"
            ).fetchall()
        }
        if actual_columns != expected_columns:
            raise RuntimeError(
                "world_settings.db does not match the current static Setting contract"
            )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_world_settings_kind
            ON world_settings(kind, created_turn, setting_id)
            """
        )
        ensure_exact_schema_version(self._conn, "world_setting", 8)
        self._conn.commit()

    def save(self, setting: WorldSetting) -> str:
        self._require_related_settings(setting.related_setting_ids)
        try:
            self._conn.execute(
                """
                INSERT INTO world_settings (
                    setting_id, kind, name, normalized_name, summary,
                    public_facts_json, hidden_facts_json,
                    related_setting_ids_json, created_turn, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    setting.setting_id,
                    setting.kind,
                    setting.name,
                    _normalize(setting.name),
                    setting.summary,
                    json.dumps(setting.public_facts, ensure_ascii=False),
                    json.dumps(setting.hidden_facts, ensure_ascii=False),
                    json.dumps(setting.related_setting_ids, ensure_ascii=False),
                    setting.created_turn,
                    setting.created_at,
                    setting.updated_at,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"setting name already exists: {setting.name}") from exc
        self._conn.commit()
        return setting.setting_id

    def update(self, setting: WorldSetting) -> None:
        if self.get_by_id(setting.setting_id) is None:
            raise ValueError(f"setting not found: {setting.setting_id}")
        self._require_related_settings(setting.related_setting_ids)
        updated = setting.model_copy(update={"updated_at": utc_now()})
        try:
            self._conn.execute(
                """
                UPDATE world_settings
                SET kind = ?, name = ?, normalized_name = ?, summary = ?,
                    public_facts_json = ?, hidden_facts_json = ?,
                    related_setting_ids_json = ?, updated_at = ?
                WHERE setting_id = ?
                """,
                (
                    updated.kind,
                    updated.name,
                    _normalize(updated.name),
                    updated.summary,
                    json.dumps(updated.public_facts, ensure_ascii=False),
                    json.dumps(updated.hidden_facts, ensure_ascii=False),
                    json.dumps(updated.related_setting_ids, ensure_ascii=False),
                    updated.updated_at,
                    updated.setting_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"setting name already exists: {setting.name}") from exc
        self._conn.commit()

    def list_all(self) -> list[WorldSetting]:
        rows = self._conn.execute(
            "SELECT * FROM world_settings ORDER BY created_turn, created_at"
        ).fetchall()
        return [self._from_row(row) for row in rows]

    def get_by_id(self, setting_id: str) -> WorldSetting | None:
        row = self._conn.execute(
            "SELECT * FROM world_settings WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        return self._from_row(row) if row is not None else None

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM world_settings"
        ).fetchone()
        return int(row["count"])

    def _require_related_settings(self, setting_ids: list[str]) -> None:
        if not setting_ids:
            return
        placeholders = ", ".join("?" for _ in setting_ids)
        rows = self._conn.execute(
            f"SELECT setting_id FROM world_settings WHERE setting_id IN ({placeholders})",
            tuple(setting_ids),
        ).fetchall()
        found = {str(row["setting_id"]) for row in rows}
        missing = sorted(set(setting_ids) - found)
        if missing:
            raise ValueError(f"unknown related setting IDs: {', '.join(missing)}")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> WorldSetting:
        return WorldSetting(
            setting_id=row["setting_id"],
            kind=row["kind"],
            name=row["name"],
            summary=row["summary"],
            public_facts=json.loads(row["public_facts_json"] or "[]"),
            hidden_facts=json.loads(row["hidden_facts_json"] or "[]"),
            related_setting_ids=json.loads(
                row["related_setting_ids_json"] or "[]"
            ),
            created_turn=row["created_turn"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


def _normalize(value: str) -> str:
    return "".join(str(value).strip().casefold().split())
