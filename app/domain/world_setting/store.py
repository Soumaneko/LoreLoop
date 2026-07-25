from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from .schemas import (
    SettingAnchorPacket,
    SettingSeedCandidate,
    SettingSourceDocument,
    WorldSetting,
    WorldSettingSource,
    utc_now,
)


class WorldSettingStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
        self._init_tables()

    def _init_tables(self) -> None:
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_settings (
                setting_id TEXT PRIMARY KEY,
                normalized_label TEXT NOT NULL,
                label TEXT NOT NULL,
                kind TEXT NOT NULL,
                basic_concept TEXT NOT NULL,
                visibility TEXT NOT NULL,
                maturity TEXT NOT NULL,
                availability TEXT NOT NULL,
                prepared_anchor_json TEXT,
                reserved_by TEXT,
                consumed_by_director_npc_id TEXT,
                rejection_reason TEXT,
                related_setting_ids_json TEXT NOT NULL,
                created_turn INTEGER NOT NULL,
                last_seen_turn INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_world_settings_available
            ON world_settings(availability, created_turn, setting_id)
            """
        )
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_setting_sources (
                source_id INTEGER PRIMARY KEY AUTOINCREMENT,
                setting_id TEXT NOT NULL,
                source_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                source_turn INTEGER NOT NULL,
                source_span TEXT NOT NULL,
                visibility TEXT NOT NULL,
                UNIQUE(setting_id, source_key, source_span),
                FOREIGN KEY(setting_id) REFERENCES world_settings(setting_id)
                    ON DELETE CASCADE
            )
            """
        )
        self._migrate_anchor_packets()
        ensure_schema_version(self._conn, "world_setting", 4)
        self._conn.commit()

    def _migrate_anchor_packets(self) -> None:
        rows = self._conn.execute(
            """
            SELECT setting_id, prepared_anchor_json
            FROM world_settings
            WHERE prepared_anchor_json IS NOT NULL
              AND prepared_anchor_json != ''
            """
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["prepared_anchor_json"])
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            changed = False
            for removed in ("authored_constraints", "open_questions"):
                if removed in payload:
                    payload.pop(removed, None)
                    changed = True
            if "everyday_effects" in payload and "world_effects" not in payload:
                payload["world_effects"] = payload.pop("everyday_effects")
                changed = True
            elif "everyday_effects" in payload:
                payload.pop("everyday_effects", None)
                changed = True
            setting_facts = [
                str(fact).strip()
                for fact in payload.get("setting_facts", [])
                if str(fact).strip()
            ]
            player_known_indexes, player_known_added = _fact_indexes(
                payload.get("player_known_facts", []),
                setting_facts,
            )
            director_private_indexes, director_private_added = _fact_indexes(
                payload.get("director_private_facts", []),
                setting_facts,
            )
            if setting_facts != payload.get("setting_facts", []):
                payload["setting_facts"] = setting_facts
                changed = True
            if player_known_added or payload.get("player_known_facts") != player_known_indexes:
                payload["player_known_facts"] = player_known_indexes
                changed = True
            if (
                director_private_added
                or payload.get("director_private_facts") != director_private_indexes
            ):
                payload["director_private_facts"] = director_private_indexes
                changed = True
            if changed:
                self._conn.execute(
                    """
                    UPDATE world_settings
                    SET prepared_anchor_json = ?, updated_at = ?
                    WHERE setting_id = ?
                    """,
                    (
                        json.dumps(payload, ensure_ascii=False),
                        utc_now(),
                        row["setting_id"],
                    ),
                )

    def list_all(self) -> list[WorldSetting]:
        rows = self._conn.execute(
            "SELECT * FROM world_settings ORDER BY created_turn, created_at"
        ).fetchall()
        return [self._setting_from_row(row) for row in rows]

    def list_available(self) -> list[WorldSetting]:
        rows = self._conn.execute(
            """
            SELECT * FROM world_settings
            WHERE availability = 'available'
            ORDER BY created_turn, created_at
            """
        ).fetchall()
        return [self._setting_from_row(row) for row in rows]

    def list_reserved_by(self, owner: str) -> list[WorldSetting]:
        rows = self._conn.execute(
            """
            SELECT * FROM world_settings
            WHERE availability = 'reserved' AND reserved_by = ?
            ORDER BY created_turn, created_at
            """,
            (owner,),
        ).fetchall()
        return [self._setting_from_row(row) for row in rows]

    def get_by_id(self, setting_id: str) -> WorldSetting | None:
        row = self._conn.execute(
            "SELECT * FROM world_settings WHERE setting_id = ?",
            (setting_id,),
        ).fetchone()
        return self._setting_from_row(row) if row is not None else None

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) AS count FROM world_settings"
        ).fetchone()
        return int(row["count"])

    def apply_extraction(
        self,
        candidates: Iterable[SettingSeedCandidate],
        *,
        source_documents: dict[str, SettingSourceDocument],
        turn_index: int,
    ) -> list[str]:
        saved_ids: list[str] = []
        with transaction(self._conn):
            for candidate in candidates:
                if candidate.resolution == "ignore":
                    continue
                document = source_documents.get(candidate.source_key)
                if document is None:
                    continue
                if candidate.resolution == "existing":
                    setting = self.get_by_id(candidate.matched_setting_id or "")
                    if setting is None:
                        continue
                else:
                    setting = self._get_by_normalized_label(candidate.label)
                    if setting is None:
                        setting = WorldSetting(
                            label=candidate.label.strip(),
                            kind=candidate.kind,
                            basic_concept=candidate.basic_concept.strip(),
                            visibility=document.visibility,
                            related_setting_ids=list(
                                dict.fromkeys(candidate.related_setting_ids)
                            ),
                            created_turn=turn_index,
                            last_seen_turn=turn_index,
                        )
                        self._insert_setting(setting)
                    else:
                        self._touch_setting(
                            setting.setting_id,
                            turn_index=turn_index,
                            visibility=document.visibility,
                            related_setting_ids=candidate.related_setting_ids,
                        )
                self._insert_source(
                    setting.setting_id,
                    document=document,
                    source_span=candidate.source_span.strip(),
                    turn_index=turn_index,
                )
                self._touch_setting(
                    setting.setting_id,
                    turn_index=turn_index,
                    visibility=document.visibility,
                    related_setting_ids=candidate.related_setting_ids,
                )
                saved_ids.append(setting.setting_id)
        return list(dict.fromkeys(saved_ids))

    def reserve(self, setting_ids: list[str], *, owner: str) -> list[WorldSetting]:
        if not setting_ids:
            return []
        reserved: list[WorldSetting] = []
        with transaction(self._conn):
            for setting_id in setting_ids:
                row = self._conn.execute(
                    """
                    SELECT availability, reserved_by
                    FROM world_settings
                    WHERE setting_id = ?
                    """,
                    (setting_id,),
                ).fetchone()
                if row is None:
                    continue
                if row["availability"] == "reserved" and row["reserved_by"] == owner:
                    pass
                elif row["availability"] == "available":
                    self._conn.execute(
                        """
                        UPDATE world_settings
                        SET availability = 'reserved', reserved_by = ?, updated_at = ?
                        WHERE setting_id = ?
                        """,
                        (owner, utc_now(), setting_id),
                    )
                else:
                    continue
                setting = self.get_by_id(setting_id)
                if setting is not None:
                    reserved.append(setting)
        return reserved

    def save_preparation(
        self,
        setting_id: str,
        anchor: SettingAnchorPacket,
    ) -> None:
        self._conn.execute(
            """
            UPDATE world_settings
            SET maturity = 'usable', prepared_anchor_json = ?, updated_at = ?
            WHERE setting_id = ?
            """,
            (anchor.model_dump_json(), utc_now(), setting_id),
        )
        self._conn.commit()

    def mark_consumed(self, setting_id: str, *, director_npc_id: str) -> None:
        self._conn.execute(
            """
            UPDATE world_settings
            SET availability = 'consumed',
                reserved_by = NULL,
                consumed_by_director_npc_id = ?,
                updated_at = ?
            WHERE setting_id = ?
            """,
            (director_npc_id, utc_now(), setting_id),
        )
        self._conn.commit()

    def mark_rejected(self, setting_id: str, *, reason: str) -> None:
        self._conn.execute(
            """
            UPDATE world_settings
            SET availability = 'rejected',
                reserved_by = NULL,
                rejection_reason = ?,
                updated_at = ?
            WHERE setting_id = ?
            """,
            (reason, utc_now(), setting_id),
        )
        self._conn.commit()

    def release_reservations(
        self,
        owner: str,
        *,
        except_setting_ids: set[str] | None = None,
    ) -> None:
        exceptions = except_setting_ids or set()
        rows = self._conn.execute(
            """
            SELECT setting_id FROM world_settings
            WHERE availability = 'reserved' AND reserved_by = ?
            """,
            (owner,),
        ).fetchall()
        with transaction(self._conn):
            for row in rows:
                setting_id = str(row["setting_id"])
                if setting_id in exceptions:
                    continue
                self._conn.execute(
                    """
                    UPDATE world_settings
                    SET availability = 'available',
                        reserved_by = NULL,
                        updated_at = ?
                    WHERE setting_id = ?
                    """,
                    (utc_now(), setting_id),
                )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _get_by_normalized_label(self, label: str) -> WorldSetting | None:
        row = self._conn.execute(
            """
            SELECT * FROM world_settings
            WHERE normalized_label = ?
            ORDER BY created_at
            LIMIT 1
            """,
            (_normalize(label),),
        ).fetchone()
        return self._setting_from_row(row) if row is not None else None

    def _insert_setting(self, setting: WorldSetting) -> None:
        self._conn.execute(
            """
            INSERT INTO world_settings (
                setting_id, normalized_label, label, kind, basic_concept,
                visibility, maturity, availability, prepared_anchor_json,
                reserved_by, consumed_by_director_npc_id, rejection_reason,
                related_setting_ids_json, created_turn, last_seen_turn,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                setting.setting_id,
                _normalize(setting.label),
                setting.label,
                setting.kind,
                setting.basic_concept,
                setting.visibility,
                setting.maturity,
                setting.availability,
                (
                    setting.prepared_anchor.model_dump_json()
                    if setting.prepared_anchor is not None
                    else None
                ),
                setting.reserved_by,
                setting.consumed_by_director_npc_id,
                setting.rejection_reason,
                json.dumps(setting.related_setting_ids, ensure_ascii=False),
                setting.created_turn,
                setting.last_seen_turn,
                setting.created_at,
                setting.updated_at,
            ),
        )

    def _insert_source(
        self,
        setting_id: str,
        *,
        document: SettingSourceDocument,
        source_span: str,
        turn_index: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR IGNORE INTO world_setting_sources (
                setting_id, source_key, source_type, source_ref,
                source_turn, source_span, visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                setting_id,
                document.source_key,
                document.source_type,
                document.source_ref,
                turn_index,
                source_span,
                document.visibility,
            ),
        )

    def _touch_setting(
        self,
        setting_id: str,
        *,
        turn_index: int,
        visibility: str,
        related_setting_ids: list[str],
    ) -> None:
        row = self._conn.execute(
            """
            SELECT visibility, related_setting_ids_json
            FROM world_settings
            WHERE setting_id = ?
            """,
            (setting_id,),
        ).fetchone()
        if row is None:
            return
        merged_visibility = _merge_visibility(row["visibility"], visibility)
        existing_related = json.loads(row["related_setting_ids_json"] or "[]")
        merged_related = list(
            dict.fromkeys([*existing_related, *related_setting_ids])
        )
        merged_related = [
            value for value in merged_related if value and value != setting_id
        ]
        self._conn.execute(
            """
            UPDATE world_settings
            SET visibility = ?,
                related_setting_ids_json = ?,
                last_seen_turn = ?,
                updated_at = ?
            WHERE setting_id = ?
            """,
            (
                merged_visibility,
                json.dumps(merged_related, ensure_ascii=False),
                turn_index,
                utc_now(),
                setting_id,
            ),
        )

    def _setting_from_row(self, row: sqlite3.Row) -> WorldSetting:
        source_rows = self._conn.execute(
            """
            SELECT source_key, source_type, source_ref, source_turn,
                   source_span, visibility
            FROM world_setting_sources
            WHERE setting_id = ?
            ORDER BY source_id
            """,
            (row["setting_id"],),
        ).fetchall()
        prepared = (
            SettingAnchorPacket.model_validate_json(row["prepared_anchor_json"])
            if row["prepared_anchor_json"]
            else None
        )
        return WorldSetting(
            setting_id=row["setting_id"],
            label=row["label"],
            kind=row["kind"],
            basic_concept=row["basic_concept"],
            visibility=row["visibility"],
            maturity=row["maturity"],
            availability=row["availability"],
            prepared_anchor=prepared,
            reserved_by=row["reserved_by"],
            consumed_by_director_npc_id=row["consumed_by_director_npc_id"],
            rejection_reason=row["rejection_reason"],
            related_setting_ids=json.loads(
                row["related_setting_ids_json"] or "[]"
            ),
            sources=[WorldSettingSource.model_validate(dict(item)) for item in source_rows],
            created_turn=row["created_turn"],
            last_seen_turn=row["last_seen_turn"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _normalize(value: str) -> str:
    return "".join(str(value).strip().casefold().split())


def _fact_indexes(values, setting_facts: list[str]) -> tuple[list[int], bool]:
    indexes: list[int] = []
    added = False
    normalized_to_index = {
        _normalize(fact): index
        for index, fact in enumerate(setting_facts)
        if _normalize(fact)
    }
    for value in values or []:
        if isinstance(value, int):
            indexes.append(value)
            continue
        fact = str(value).strip()
        key = _normalize(fact)
        if not key:
            continue
        index = normalized_to_index.get(key)
        if index is None:
            index = len(setting_facts)
            setting_facts.append(fact)
            normalized_to_index[key] = index
            added = True
        indexes.append(index)
    return list(dict.fromkeys(indexes)), added


def _merge_visibility(left: str, right: str) -> str:
    if left == right:
        return left
    return "mixed"
