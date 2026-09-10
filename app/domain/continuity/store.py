from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.runtime.sqlite import ensure_schema_version, transaction

from .schemas import (
    ContinuityItem,
    ContinuityItemDraft,
    ContinuityLifecycleLog,
    ContinuityLogEvidence,
    ContinuityUpdateDraft,
)


SCHEMA_VERSION = 3


class ContinuityStore:
    def __init__(self, conn: sqlite3.Connection, *, owns_connection: bool = False):
        self._conn = conn
        self._owns_connection = owns_connection
        self._init_schema()

    def _init_schema(self) -> None:
        if self._is_legacy_schema():
            self._migrate_v1_to_v3()
        elif self._has_incremental_state_schema():
            self._migrate_incremental_state_to_v3_snapshot()
        else:
            self._create_schema_v3()
        ensure_schema_version(self._conn, "continuity", SCHEMA_VERSION)
        self._conn.commit()

    def _is_legacy_schema(self) -> bool:
        exists = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("continuity_items",),
        ).fetchone()
        if exists is None:
            return False
        columns = {
            row["name"]
            for row in self._conn.execute("PRAGMA table_info(continuity_items)")
        }
        return "statement" in columns and "premise" not in columns

    def _has_incremental_state_schema(self) -> bool:
        columns = self._table_columns("continuity_items")
        return "current_state_json" in columns and "current_state" not in columns

    def _table_columns(self, table: str) -> set[str]:
        return {
            row["name"]
            for row in self._conn.execute(f"PRAGMA table_info({table})")
        }

    def _create_schema_v3(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS continuity_items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL,
                premise TEXT NOT NULL,
                current_state TEXT,
                entities_json TEXT NOT NULL,
                location TEXT,
                deadline TEXT,
                created_turn INTEGER NOT NULL,
                updated_turn INTEGER NOT NULL,
                closed_turn INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_continuity_active
            ON continuity_items(status, updated_turn);

            CREATE VIRTUAL TABLE IF NOT EXISTS continuity_items_fts
            USING fts5(
                item_id UNINDEXED,
                subject,
                premise,
                current_state,
                entities,
                location,
                deadline,
                tokenize='trigram'
            );

            CREATE TABLE IF NOT EXISTS continuity_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                state_delta TEXT,
                reason TEXT NOT NULL,
                migration_created INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(item_id) REFERENCES continuity_items(id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_continuity_logs_item_turn
            ON continuity_logs(item_id, turn_index, log_id);
            CREATE INDEX IF NOT EXISTS idx_continuity_logs_bridge
            ON continuity_logs(item_id, operation, turn_index, log_id);

            CREATE TABLE IF NOT EXISTS continuity_log_evidence (
                log_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_hash TEXT,
                PRIMARY KEY(log_id, position),
                UNIQUE(log_id, evidence_id),
                FOREIGN KEY(log_id) REFERENCES continuity_logs(log_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_continuity_log_evidence_id
            ON continuity_log_evidence(evidence_id);

            CREATE TABLE IF NOT EXISTS continuity_processed_turns (
                turn_index INTEGER PRIMARY KEY
            );
            """
        )

    def _migrate_v1_to_v3(self) -> None:
        try:
            self._conn.executescript(
                """
            BEGIN IMMEDIATE;
            DROP TABLE continuity_items_fts;
            DROP INDEX IF EXISTS idx_continuity_active;
            ALTER TABLE continuity_items RENAME TO continuity_items_v1;

            CREATE TABLE continuity_items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL,
                premise TEXT NOT NULL,
                current_state TEXT,
                entities_json TEXT NOT NULL,
                location TEXT,
                deadline TEXT,
                created_turn INTEGER NOT NULL,
                updated_turn INTEGER NOT NULL,
                closed_turn INTEGER
            );
            CREATE INDEX idx_continuity_active
            ON continuity_items(status, updated_turn);
            INSERT INTO continuity_items(
                id, kind, status, subject, premise, current_state,
                entities_json, location, deadline, created_turn, updated_turn,
                closed_turn
            )
            SELECT id, kind, status, subject, statement, NULL, entities_json,
                   location, deadline, created_turn, updated_turn, closed_turn
            FROM continuity_items_v1;

            CREATE VIRTUAL TABLE continuity_items_fts
            USING fts5(
                item_id UNINDEXED, subject, premise, current_state, entities,
                location, deadline, tokenize='trigram'
            );
            INSERT INTO continuity_items_fts(
                item_id, subject, premise, current_state, entities,
                location, deadline
            )
            SELECT id, subject, premise, '',
                   COALESCE((
                       SELECT group_concat(CAST(value AS TEXT), ' ')
                       FROM json_each(continuity_items.entities_json)
                   ), ''),
                   COALESCE(location, ''), COALESCE(deadline, '')
            FROM continuity_items WHERE status = 'active';

            CREATE TABLE continuity_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                item_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                state_delta TEXT,
                reason TEXT NOT NULL,
                migration_created INTEGER NOT NULL DEFAULT 0,
                FOREIGN KEY(item_id) REFERENCES continuity_items(id)
                    ON DELETE CASCADE
            );
            CREATE INDEX idx_continuity_logs_item_turn
            ON continuity_logs(item_id, turn_index, log_id);
            CREATE INDEX idx_continuity_logs_bridge
            ON continuity_logs(item_id, operation, turn_index, log_id);
            INSERT INTO continuity_logs(
                item_id, operation, turn_index, state_delta, reason,
                migration_created
            )
            SELECT DISTINCT item_id, operation, turn_index, NULL,
                   '由旧版 Continuity evidence 迁移。', 1
            FROM continuity_evidence;

            CREATE TABLE continuity_log_evidence (
                log_id INTEGER NOT NULL,
                position INTEGER NOT NULL,
                evidence_id TEXT NOT NULL,
                evidence_hash TEXT,
                PRIMARY KEY(log_id, position),
                UNIQUE(log_id, evidence_id),
                FOREIGN KEY(log_id) REFERENCES continuity_logs(log_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX idx_continuity_log_evidence_id
            ON continuity_log_evidence(evidence_id);
            INSERT INTO continuity_log_evidence(
                log_id, position, evidence_id, evidence_hash
            )
            SELECT logs.log_id,
                   ROW_NUMBER() OVER (
                       PARTITION BY logs.log_id
                       ORDER BY evidence.evidence_id
                   ) - 1,
                   evidence.evidence_id, NULL
            FROM continuity_evidence AS evidence
            JOIN continuity_logs AS logs
              ON logs.item_id = evidence.item_id
             AND logs.operation = evidence.operation
             AND logs.turn_index = evidence.turn_index;

            DROP TABLE continuity_evidence;
            DROP TABLE continuity_items_v1;
            COMMIT;
                """
            )
        except BaseException:
            self._conn.rollback()
            raise

    def _migrate_incremental_state_to_v3_snapshot(self) -> None:
        """Upgrade unreleased list-based schemas without changing version 3."""
        rows = self._conn.execute(
            "SELECT id, current_state_json FROM continuity_items"
        ).fetchall()
        evidence_has_position = "position" in self._table_columns(
            "continuity_log_evidence"
        )
        logs_have_marker = "migration_created" in self._table_columns(
            "continuity_logs"
        )
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            self._conn.execute(
                "ALTER TABLE continuity_items ADD COLUMN current_state TEXT"
            )
            for row in rows:
                snapshot = _legacy_incremental_snapshot(
                    row["current_state_json"]
                )
                self._conn.execute(
                    "UPDATE continuity_items SET current_state = ? WHERE id = ?",
                    (snapshot, row["id"]),
                )
            if not logs_have_marker:
                self._conn.execute(
                    """
                    ALTER TABLE continuity_logs
                    ADD COLUMN migration_created INTEGER NOT NULL DEFAULT 0
                    """
                )
            if not evidence_has_position:
                self._conn.execute(
                    "DROP INDEX IF EXISTS idx_continuity_log_evidence_id"
                )
                self._conn.execute(
                    """
                    ALTER TABLE continuity_log_evidence
                    RENAME TO continuity_log_evidence_incremental
                    """
                )
                self._conn.execute(
                    """
                    CREATE TABLE continuity_log_evidence (
                        log_id INTEGER NOT NULL,
                        position INTEGER NOT NULL,
                        evidence_id TEXT NOT NULL,
                        evidence_hash TEXT,
                        PRIMARY KEY(log_id, position),
                        UNIQUE(log_id, evidence_id),
                        FOREIGN KEY(log_id) REFERENCES continuity_logs(log_id)
                            ON DELETE CASCADE
                    )
                    """
                )
                self._conn.execute(
                    """
                    INSERT INTO continuity_log_evidence(
                        log_id, position, evidence_id, evidence_hash
                    )
                    SELECT log_id,
                           ROW_NUMBER() OVER (
                               PARTITION BY log_id ORDER BY evidence_id
                           ) - 1,
                           evidence_id, evidence_hash
                    FROM continuity_log_evidence_incremental
                    """
                )
                self._conn.execute(
                    "DROP TABLE continuity_log_evidence_incremental"
                )
                self._conn.execute(
                    """
                    CREATE INDEX idx_continuity_log_evidence_id
                    ON continuity_log_evidence(evidence_id)
                    """
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_continuity_logs_bridge
                ON continuity_logs(item_id, operation, turn_index, log_id)
                """
            )
            self._rebuild_fts()
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    def list_active(self) -> list[ContinuityItem]:
        rows = self._conn.execute(
            """
            SELECT * FROM continuity_items
            WHERE status = 'active'
            ORDER BY created_turn, id
            """
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def list_all(self) -> list[ContinuityItem]:
        rows = self._conn.execute(
            "SELECT * FROM continuity_items ORDER BY created_turn, id"
        ).fetchall()
        return [self._row_to_item(row) for row in rows]

    def get(self, item_id: str) -> ContinuityItem | None:
        row = self._conn.execute(
            "SELECT * FROM continuity_items WHERE id = ?",
            (item_id,),
        ).fetchone()
        return self._row_to_item(row) if row is not None else None

    def turn_processed(self, turn_index: int) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM continuity_processed_turns WHERE turn_index = ?",
            (turn_index,),
        ).fetchone() is not None

    def mark_turn_processed(self, turn_index: int) -> None:
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO continuity_processed_turns(turn_index) VALUES (?)",
                (turn_index,),
            )

    def insert(self, item: ContinuityItem) -> None:
        with transaction(self._conn):
            self._insert_item(item)

    def _insert_item(self, item: ContinuityItem) -> None:
        values = self._item_values(item)
        if "current_state_json" in self._table_columns("continuity_items"):
            self._conn.execute(
                """
                INSERT INTO continuity_items(
                    id, kind, status, subject, premise, current_state,
                    current_state_json, entities_json, location, deadline,
                    created_turn, updated_turn, closed_turn
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (*values[:6], "[]", *values[6:]),
            )
            self._replace_fts(item)
            return
        self._conn.execute(
            """
            INSERT INTO continuity_items(
                id, kind, status, subject, premise, current_state,
                entities_json, location, deadline, created_turn, updated_turn,
                closed_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        self._replace_fts(item)

    def update_active(
        self,
        item_id: str,
        draft: ContinuityUpdateDraft,
        *,
        turn_index: int,
    ) -> ContinuityItem:
        current = self.get(item_id)
        if current is None or current.status != "active":
            raise ValueError(f"continuity item is not active: {item_id}")
        updated = ContinuityItem.model_validate({
            **current.model_dump(),
            "subject": draft.subject,
            "current_state": draft.new_current_state,
            "entities": _merge_entities(current.entities, draft.entities),
            "location": (
                draft.location
                if draft.location is not None
                else current.location
            ),
            "deadline": (
                draft.deadline
                if draft.deadline is not None
                else current.deadline
            ),
            "updated_turn": turn_index,
        })
        self._write_active_item(updated)
        return updated

    def edit_active_metadata(
        self,
        item_id: str,
        *,
        subject: str,
        entities: list[str],
        location: str | None,
        deadline: str | None,
        turn_index: int,
    ) -> ContinuityItem:
        current = self.get(item_id)
        if current is None or current.status != "active":
            raise ValueError(f"continuity item is not active: {item_id}")
        updated = ContinuityItem.model_validate({
            **current.model_dump(),
            "subject": subject,
            "entities": entities,
            "location": location,
            "deadline": deadline,
            "updated_turn": turn_index,
        })
        self._write_active_item(updated)
        return updated

    def _write_active_item(self, item: ContinuityItem) -> None:
        with transaction(self._conn):
            self._conn.execute(
                """
                UPDATE continuity_items SET
                    subject = ?, current_state = ?, entities_json = ?,
                    location = ?, deadline = ?, updated_turn = ?
                WHERE id = ?
                """,
                (
                    item.subject,
                    item.current_state,
                    json.dumps(item.entities, ensure_ascii=False),
                    item.location,
                    item.deadline,
                    item.updated_turn,
                    item.id,
                ),
            )
            self._replace_fts(item)

    def close_item(
        self,
        item_id: str,
        *,
        status: str,
        turn_index: int,
    ) -> None:
        current = self.get(item_id)
        if current is None or current.status != "active":
            raise ValueError(f"continuity item is not active: {item_id}")
        if status not in {"resolved", "cancelled"}:
            raise ValueError(f"invalid continuity close status: {status}")
        with transaction(self._conn):
            self._conn.execute(
                """
                UPDATE continuity_items
                SET status = ?, updated_turn = ?, closed_turn = ?
                WHERE id = ?
                """,
                (status, turn_index, turn_index, item_id),
            )
            self._conn.execute(
                "DELETE FROM continuity_items_fts WHERE item_id = ?",
                (item_id,),
            )

    def add_lifecycle_log(
        self,
        *,
        item_id: str,
        operation: str,
        turn_index: int,
        state_delta: str | None,
        reason: str,
        evidence: Iterable[tuple[str, str]],
        migration_created: bool = False,
    ) -> int:
        with transaction(self._conn):
            log_id = self._insert_log_row(
                item_id=item_id,
                operation=operation,
                turn_index=turn_index,
                state_delta=state_delta,
                reason=reason,
                migration_created=migration_created,
            )
            for position, (evidence_id, evidence_hash) in enumerate(evidence):
                self._insert_log_evidence(
                    log_id=log_id,
                    position=position,
                    evidence_id=evidence_id,
                    evidence_hash=evidence_hash,
                )
        return log_id

    def _insert_log_row(
        self,
        *,
        item_id: str,
        operation: str,
        turn_index: int,
        state_delta: str | None,
        reason: str,
        migration_created: bool,
    ) -> int:
        cursor = self._conn.execute(
            """
            INSERT INTO continuity_logs(
                item_id, operation, turn_index, state_delta, reason,
                migration_created
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item_id,
                operation,
                turn_index,
                state_delta,
                reason,
                int(migration_created),
            ),
        )
        return int(cursor.lastrowid)

    def _insert_log_evidence(
        self,
        *,
        log_id: int,
        position: int,
        evidence_id: str,
        evidence_hash: str | None,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO continuity_log_evidence(
                log_id, position, evidence_id, evidence_hash
            ) VALUES (?, ?, ?, ?)
            """,
            (log_id, position, evidence_id, evidence_hash),
        )

    def logs_for_item(self, item_id: str) -> list[ContinuityLifecycleLog]:
        rows = self._conn.execute(
            """
            SELECT log_id, item_id, operation, turn_index, state_delta, reason,
                   migration_created
            FROM continuity_logs
            WHERE item_id = ?
            ORDER BY turn_index, log_id
            """,
            (item_id,),
        ).fetchall()
        return self._logs_from_rows(rows)

    def logs_for_evidence_ids(
        self,
        evidence_ids: Iterable[str],
    ) -> list[ContinuityLifecycleLog]:
        unique_ids = sorted(set(evidence_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._conn.execute(
            f"""
            SELECT DISTINCT logs.log_id, logs.item_id, logs.operation,
                            logs.turn_index, logs.state_delta, logs.reason,
                            logs.migration_created
            FROM continuity_logs AS logs
            JOIN continuity_log_evidence AS evidence
              ON evidence.log_id = logs.log_id
            WHERE evidence.evidence_id IN ({placeholders})
            ORDER BY logs.turn_index, logs.log_id
            """,
            unique_ids,
        ).fetchall()
        return self._logs_from_rows(rows)

    def latest_update_logs(
        self,
        item_ids: Iterable[str],
    ) -> list[ContinuityLifecycleLog]:
        """Latest ``update`` lifecycle node per item, with its evidence.

        Deliberately excludes ``create``: Context Facts replaces an item's
        accumulated ``current_state`` with the raw prose bound to the most
        recent ``update`` that formed the current state, rather than borrowing
        the bridge's create + latest skeleton (which would widen the paragraph
        selection).
        """
        unique_ids = sorted(set(item_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._conn.execute(
            f"""
            SELECT logs.log_id, logs.item_id, logs.operation, logs.turn_index,
                   logs.state_delta, logs.reason, logs.migration_created
            FROM continuity_logs AS logs
            WHERE logs.operation = 'update'
              AND logs.item_id IN ({placeholders})
              AND logs.log_id = (
                  SELECT latest.log_id FROM continuity_logs AS latest
                  WHERE latest.item_id = logs.item_id
                    AND latest.operation = 'update'
                  ORDER BY latest.turn_index DESC, latest.log_id DESC
                  LIMIT 1
              )
            ORDER BY logs.turn_index, logs.log_id
            """,
            unique_ids,
        ).fetchall()
        return self._logs_from_rows(rows)

    def bridge_lifecycle_logs(
        self,
        item_ids: Iterable[str],
    ) -> list[ContinuityLifecycleLog]:
        """Fetch a fixed lifecycle skeleton in two queries, regardless of log age."""
        unique_ids = sorted(set(item_ids))
        if not unique_ids:
            return []
        placeholders = ",".join("?" for _ in unique_ids)
        rows = self._conn.execute(
            f"""
            WITH selected_items AS (
                SELECT id, status
                FROM continuity_items
                WHERE id IN ({placeholders})
            ), node_ids AS (
                SELECT (
                    SELECT log_id FROM continuity_logs
                    WHERE item_id = selected_items.id
                      AND operation = 'create'
                    ORDER BY turn_index, log_id LIMIT 1
                ) AS log_id
                FROM selected_items
                UNION ALL
                SELECT CASE status
                    WHEN 'active' THEN (
                        SELECT log_id FROM continuity_logs
                        WHERE item_id = selected_items.id
                          AND operation = 'update'
                        ORDER BY turn_index DESC, log_id DESC LIMIT 1
                    )
                    WHEN 'resolved' THEN (
                        SELECT log_id FROM continuity_logs
                        WHERE item_id = selected_items.id
                          AND operation = 'resolve'
                        ORDER BY turn_index DESC, log_id DESC LIMIT 1
                    )
                    WHEN 'cancelled' THEN (
                        SELECT log_id FROM continuity_logs
                        WHERE item_id = selected_items.id
                          AND operation = 'cancel'
                        ORDER BY turn_index DESC, log_id DESC LIMIT 1
                    )
                END AS log_id
                FROM selected_items
            )
            SELECT logs.log_id, logs.item_id, logs.operation,
                   logs.turn_index, logs.state_delta, logs.reason,
                   logs.migration_created
            FROM continuity_logs AS logs
            JOIN node_ids ON node_ids.log_id = logs.log_id
            ORDER BY logs.item_id,
                     CASE logs.operation WHEN 'create' THEN 1 ELSE 0 END,
                     logs.turn_index DESC, logs.log_id DESC
            """,
            unique_ids,
        ).fetchall()
        return self._logs_from_rows(rows)

    def _logs_from_rows(self, rows) -> list[ContinuityLifecycleLog]:
        rows = list(rows)
        log_ids = [int(row["log_id"]) for row in rows]
        evidence_by_log: dict[int, list[ContinuityLogEvidence]] = {
            log_id: [] for log_id in log_ids
        }
        if log_ids:
            placeholders = ",".join("?" for _ in log_ids)
            evidence_rows = self._conn.execute(
                f"""
                SELECT log_id, position, evidence_id, evidence_hash
                FROM continuity_log_evidence
                WHERE log_id IN ({placeholders})
                ORDER BY log_id, position
                """,
                log_ids,
            ).fetchall()
            for evidence_row in evidence_rows:
                evidence_by_log[int(evidence_row["log_id"])].append(
                    ContinuityLogEvidence(
                        position=evidence_row["position"],
                        evidence_id=evidence_row["evidence_id"],
                        evidence_hash=evidence_row["evidence_hash"],
                    )
                )
        result: list[ContinuityLifecycleLog] = []
        for row in rows:
            result.append(ContinuityLifecycleLog(
                log_id=row["log_id"],
                item_id=row["item_id"],
                operation=row["operation"],
                turn_index=row["turn_index"],
                state_delta=row["state_delta"],
                reason=row["reason"],
                migration_created=bool(row["migration_created"]),
                evidence=evidence_by_log[int(row["log_id"])],
            ))
        return result

    def find_duplicate(self, draft: ContinuityItemDraft) -> ContinuityItem | None:
        fingerprint = _fingerprint(draft)
        return next(
            (
                item
                for item in self.list_active()
                if _fingerprint(item) == fingerprint
            ),
            None,
        )

    def fts_search(self, terms: Iterable[str]) -> set[str]:
        result: set[str] = set()
        for term in terms:
            clean = term.strip()
            if len(clean) < 3:
                continue
            expression = '"' + clean.replace('"', '""') + '"'
            try:
                rows = self._conn.execute(
                    """
                    SELECT item_id FROM continuity_items_fts
                    WHERE continuity_items_fts MATCH ?
                    """,
                    (expression,),
                ).fetchall()
            except sqlite3.OperationalError:
                continue
            result.update(row["item_id"] for row in rows)
        return result

    def transaction(self):
        return transaction(self._conn)

    def count(self) -> int:
        return int(self._conn.execute(
            "SELECT COUNT(*) FROM continuity_items"
        ).fetchone()[0])

    def clear(self) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM continuity_items_fts")
            self._conn.execute("DELETE FROM continuity_log_evidence")
            self._conn.execute("DELETE FROM continuity_logs")
            self._conn.execute("DELETE FROM continuity_items")
            self._conn.execute("DELETE FROM continuity_processed_turns")

    def _replace_fts(self, item: ContinuityItem) -> None:
        self._conn.execute(
            "DELETE FROM continuity_items_fts WHERE item_id = ?",
            (item.id,),
        )
        if item.status != "active":
            return
        self._conn.execute(
            """
            INSERT INTO continuity_items_fts(
                item_id, subject, premise, current_state, entities,
                location, deadline
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.subject,
                item.premise,
                item.current_state or "",
                " ".join(item.entities),
                item.location or "",
                item.deadline or "",
            ),
        )

    def _rebuild_fts(self) -> None:
        self._conn.execute("DELETE FROM continuity_items_fts")
        rows = self._conn.execute(
            "SELECT * FROM continuity_items WHERE status = 'active'"
        ).fetchall()
        for row in rows:
            self._replace_fts(self._row_to_item(row))

    def rebuild_fts(self) -> None:
        with transaction(self._conn):
            self._rebuild_fts()

    @staticmethod
    def _item_values(item: ContinuityItem) -> tuple:
        return (
            item.id,
            item.kind,
            item.status,
            item.subject,
            item.premise,
            item.current_state,
            json.dumps(item.entities, ensure_ascii=False),
            item.location,
            item.deadline,
            item.created_turn,
            item.updated_turn,
            item.closed_turn,
        )

    @staticmethod
    def _row_to_item(row) -> ContinuityItem:
        return ContinuityItem(
            id=row["id"],
            kind=row["kind"],
            status=row["status"],
            subject=row["subject"],
            premise=row["premise"],
            current_state=row["current_state"],
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            deadline=row["deadline"],
            created_turn=row["created_turn"],
            updated_turn=row["updated_turn"],
            closed_turn=row["closed_turn"],
        )

    def close(self) -> None:
        if self._owns_connection:
            self._conn.close()

    def __enter__(self) -> "ContinuityStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _fingerprint(item: ContinuityItemDraft | ContinuityItem) -> tuple:
    return (
        item.kind,
        item.subject.strip().casefold(),
        item.premise.strip().casefold(),
        tuple(sorted(entity.strip().casefold() for entity in item.entities)),
    )


def _merge_entities(existing: list[str], additions: list[str]) -> list[str]:
    """Keep entity metadata monotonic while preserving first-seen order."""
    merged = list(existing)
    seen = {entity.strip().casefold() for entity in existing}
    for entity in additions:
        key = entity.strip().casefold()
        if key not in seen:
            merged.append(entity)
            seen.add(key)
    return merged


def _legacy_incremental_snapshot(raw_json: str) -> str | None:
    """Preserve unreleased delta-list information until the next replacement."""
    entries = json.loads(raw_json)
    texts = [
        str(entry.get("text", "")).strip()
        for entry in entries
        if isinstance(entry, dict) and str(entry.get("text", "")).strip()
    ]
    if not texts:
        return None
    return "\n".join(texts)[-800:]
