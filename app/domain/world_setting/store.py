from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from .schemas import (
    SettingAnchorPacket,
    SettingMaintenanceResult,
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
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS world_setting_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self._migrate_anchor_packets()
        ensure_schema_version(self._conn, "world_setting", 5)
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

    def list_maintenance_seeds(self) -> list[WorldSetting]:
        rows = self._conn.execute(
            """
            SELECT * FROM world_settings
            WHERE maturity = 'seed' AND availability = 'available'
            ORDER BY created_turn, created_at
            """
        ).fetchall()
        return [self._setting_from_row(row) for row in rows]

    def list_maintenance_targets(self) -> list[WorldSetting]:
        rows = self._conn.execute(
            """
            SELECT * FROM world_settings
            WHERE maturity = 'usable'
              AND availability IN ('available', 'reserved', 'consumed')
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
                document = source_documents.get(candidate.source_key)
                if document is None:
                    continue
                setting = WorldSetting(
                    label=candidate.label.strip(),
                    kind=candidate.kind,
                    basic_concept=candidate.basic_concept.strip(),
                    visibility=document.visibility,
                    created_turn=turn_index,
                    last_seen_turn=turn_index,
                )
                self._insert_setting(setting)
                self._insert_source(
                    setting.setting_id,
                    document=document,
                    source_span=candidate.source_span.strip(),
                    turn_index=turn_index,
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

    def last_maintained_turn(self) -> int | None:
        row = self._conn.execute(
            """
            SELECT value FROM world_setting_meta
            WHERE key = 'last_maintained_turn'
            """
        ).fetchone()
        return int(row["value"]) if row is not None else None

    def apply_maintenance(
        self,
        result: SettingMaintenanceResult,
        *,
        turn_index: int,
    ) -> list[str]:
        retained_ids: list[str] = []
        with transaction(self._conn):
            for group in result.groups:
                if group.resolution == "independent":
                    target_id = str(group.canonical_seed_id)
                    self._conn.execute(
                        """
                        UPDATE world_settings
                        SET normalized_label = ?,
                            label = ?,
                            kind = ?,
                            basic_concept = ?,
                            updated_at = ?
                        WHERE setting_id = ?
                        """,
                        (
                            _normalize(group.canonical_label or ""),
                            group.canonical_label,
                            group.canonical_kind,
                            group.canonical_basic_concept,
                            utc_now(),
                            target_id,
                        ),
                    )
                    absorbed_ids = [
                        setting_id
                        for setting_id in group.seed_ids
                        if setting_id != target_id
                    ]
                    self._absorb_settings(
                        target_id=target_id,
                        absorbed_ids=absorbed_ids,
                    )
                    retained_ids.append(target_id)
                    continue

                target_id = str(group.target_setting_id)
                fact_visibility = {
                    addition.fact: self._source_visibility_for_settings(
                        addition.source_seed_ids
                    )
                    for addition in group.fact_additions
                }
                self._absorb_settings(
                    target_id=target_id,
                    absorbed_ids=group.seed_ids,
                )
                if group.resolution == "supplement":
                    self._apply_supplement(
                        target_id=target_id,
                        basic_concept=str(group.supplemented_basic_concept),
                        definition=group.supplemented_definition,
                        fact_additions=[
                            (addition.fact, fact_visibility[addition.fact])
                            for addition in group.fact_additions
                        ],
                    )
                retained_ids.append(target_id)
            self._conn.execute(
                """
                INSERT INTO world_setting_meta(key, value)
                VALUES ('last_maintained_turn', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (str(turn_index),),
            )
        return list(dict.fromkeys(retained_ids))

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

    def _absorb_settings(
        self,
        *,
        target_id: str,
        absorbed_ids: list[str],
    ) -> None:
        unique_absorbed_ids = list(dict.fromkeys(absorbed_ids))
        absorbed_id_set = set(unique_absorbed_ids)
        for absorbed_id in unique_absorbed_ids:
            if absorbed_id == target_id:
                continue
            target_row = self._conn.execute(
                """
                SELECT visibility, related_setting_ids_json,
                       created_turn, last_seen_turn
                FROM world_settings
                WHERE setting_id = ?
                """,
                (target_id,),
            ).fetchone()
            absorbed_row = self._conn.execute(
                """
                SELECT visibility, related_setting_ids_json,
                       created_turn, last_seen_turn
                FROM world_settings
                WHERE setting_id = ?
                """,
                (absorbed_id,),
            ).fetchone()
            if target_row is None or absorbed_row is None:
                raise ValueError(
                    "Setting maintenance referenced a missing setting"
                )
            self._conn.execute(
                """
                INSERT OR IGNORE INTO world_setting_sources (
                    setting_id, source_key, source_type, source_ref,
                    source_turn, source_span, visibility
                )
                SELECT ?, source_key, source_type, source_ref,
                       source_turn, source_span, visibility
                FROM world_setting_sources
                WHERE setting_id = ?
                """,
                (target_id, absorbed_id),
            )
            target_related = json.loads(
                target_row["related_setting_ids_json"] or "[]"
            )
            absorbed_related = json.loads(
                absorbed_row["related_setting_ids_json"] or "[]"
            )
            merged_related = [
                setting_id
                for setting_id in dict.fromkeys(
                    [*target_related, *absorbed_related]
                )
                if setting_id != target_id
                and setting_id not in absorbed_id_set
            ]
            self._conn.execute(
                """
                UPDATE world_settings
                SET visibility = ?,
                    related_setting_ids_json = ?,
                    created_turn = ?,
                    last_seen_turn = ?,
                    updated_at = ?
                WHERE setting_id = ?
                """,
                (
                    _merge_visibility(
                        target_row["visibility"],
                        absorbed_row["visibility"],
                    ),
                    json.dumps(merged_related, ensure_ascii=False),
                    min(
                        int(target_row["created_turn"]),
                        int(absorbed_row["created_turn"]),
                    ),
                    max(
                        int(target_row["last_seen_turn"]),
                        int(absorbed_row["last_seen_turn"]),
                    ),
                    utc_now(),
                    target_id,
                ),
            )
            self._replace_related_setting_id(
                old_id=absorbed_id,
                new_id=target_id,
            )
            self._conn.execute(
                "DELETE FROM world_settings WHERE setting_id = ?",
                (absorbed_id,),
            )

    def _replace_related_setting_id(
        self,
        *,
        old_id: str,
        new_id: str,
    ) -> None:
        rows = self._conn.execute(
            """
            SELECT setting_id, related_setting_ids_json
            FROM world_settings
            """
        ).fetchall()
        for row in rows:
            related = json.loads(row["related_setting_ids_json"] or "[]")
            if old_id not in related:
                continue
            replaced = [
                new_id if setting_id == old_id else setting_id
                for setting_id in related
            ]
            replaced = [
                setting_id
                for setting_id in dict.fromkeys(replaced)
                if setting_id != row["setting_id"]
            ]
            self._conn.execute(
                """
                UPDATE world_settings
                SET related_setting_ids_json = ?, updated_at = ?
                WHERE setting_id = ?
                """,
                (
                    json.dumps(replaced, ensure_ascii=False),
                    utc_now(),
                    row["setting_id"],
                ),
            )

    def _source_visibility_for_settings(
        self,
        setting_ids: list[str],
    ) -> set[str]:
        if not setting_ids:
            return set()
        placeholders = ", ".join("?" for _ in setting_ids)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT visibility
            FROM world_setting_sources
            WHERE setting_id IN ({placeholders})
            """,
            tuple(setting_ids),
        ).fetchall()
        return {str(row["visibility"]) for row in rows}

    def _apply_supplement(
        self,
        *,
        target_id: str,
        basic_concept: str,
        definition: str | None,
        fact_additions: list[tuple[str, set[str]]],
    ) -> None:
        row = self._conn.execute(
            """
            SELECT prepared_anchor_json
            FROM world_settings
            WHERE setting_id = ?
            """,
            (target_id,),
        ).fetchone()
        if row is None:
            raise ValueError("Setting supplement target does not exist")
        anchor = (
            SettingAnchorPacket.model_validate_json(row["prepared_anchor_json"])
            if row["prepared_anchor_json"]
            else None
        )
        anchor_json = None
        if anchor is not None:
            facts = list(anchor.setting_facts)
            known_indexes = list(anchor.player_known_facts)
            private_indexes = list(anchor.director_private_facts)
            normalized = {_normalize(fact): index for index, fact in enumerate(facts)}
            for fact, visibility in fact_additions:
                clean_fact = fact.strip()
                key = _normalize(clean_fact)
                if not key:
                    continue
                index = normalized.get(key)
                if index is None:
                    index = len(facts)
                    facts.append(clean_fact)
                    normalized[key] = index
                if "player_known" in visibility and index not in known_indexes:
                    known_indexes.append(index)
                if (
                    "director_private" in visibility
                    and index not in private_indexes
                ):
                    private_indexes.append(index)
            updated_anchor = anchor.model_copy(
                update={
                    "definition": definition or anchor.definition,
                    "setting_facts": facts,
                    "player_known_facts": known_indexes,
                    "director_private_facts": private_indexes,
                }
            )
            anchor_json = updated_anchor.model_dump_json()
        self._conn.execute(
            """
            UPDATE world_settings
            SET basic_concept = ?,
                prepared_anchor_json = COALESCE(?, prepared_anchor_json),
                updated_at = ?
            WHERE setting_id = ?
            """,
            (basic_concept.strip(), anchor_json, utc_now(), target_id),
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
