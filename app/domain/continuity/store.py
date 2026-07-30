from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from .schemas import ContinuityItem, ContinuityItemDraft


class ContinuityStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS continuity_items (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                subject TEXT NOT NULL,
                statement TEXT NOT NULL,
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
                statement,
                entities,
                location,
                deadline,
                tokenize='trigram'
            );

            CREATE TABLE IF NOT EXISTS continuity_evidence (
                item_id TEXT NOT NULL,
                evidence_id TEXT NOT NULL,
                operation TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                PRIMARY KEY(item_id, evidence_id, operation),
                FOREIGN KEY(item_id) REFERENCES continuity_items(id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS continuity_processed_turns (
                turn_index INTEGER PRIMARY KEY
            );
            """
        )
        ensure_schema_version(self._conn, "continuity", 1)
        self._conn.commit()

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
        self._conn.execute(
            "INSERT INTO continuity_processed_turns(turn_index) VALUES (?)",
            (turn_index,),
        )

    def insert(self, item: ContinuityItem) -> None:
        self._conn.execute(
            """
            INSERT INTO continuity_items(
                id, kind, status, subject, statement, entities_json,
                location, deadline, created_turn, updated_turn, closed_turn
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            self._item_values(item),
        )
        self._replace_fts(item)

    def update_active(
        self,
        item_id: str,
        draft: ContinuityItemDraft,
        *,
        turn_index: int,
    ) -> ContinuityItem:
        current = self.get(item_id)
        if current is None or current.status != "active":
            raise ValueError(f"continuity item is not active: {item_id}")
        if current.kind != draft.kind:
            raise ValueError("continuity update cannot change kind")
        updated = current.model_copy(update={
            **draft.model_dump(),
            "updated_turn": turn_index,
        })
        self._conn.execute(
            """
            UPDATE continuity_items SET
                subject = ?, statement = ?, entities_json = ?,
                location = ?, deadline = ?, updated_turn = ?
            WHERE id = ?
            """,
            (
                updated.subject,
                updated.statement,
                json.dumps(updated.entities, ensure_ascii=False),
                updated.location,
                updated.deadline,
                turn_index,
                item_id,
            ),
        )
        self._replace_fts(updated)
        return updated

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

    def add_evidence(
        self,
        *,
        item_id: str,
        evidence_ids: Iterable[str],
        operation: str,
        turn_index: int,
    ) -> None:
        for evidence_id in evidence_ids:
            self._conn.execute(
                """
                INSERT INTO continuity_evidence(
                    item_id, evidence_id, operation, turn_index
                ) VALUES (?, ?, ?, ?)
                """,
                (item_id, evidence_id, operation, turn_index),
            )

    def evidence_for_item(self, item_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT evidence_id, operation, turn_index
            FROM continuity_evidence
            WHERE item_id = ?
            ORDER BY turn_index, evidence_id, operation
            """,
            (item_id,),
        ).fetchall()
        return [dict(row) for row in rows]

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
            self._conn.execute("DELETE FROM continuity_evidence")
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
                item_id, subject, statement, entities, location, deadline
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                item.id,
                item.subject,
                item.statement,
                " ".join(item.entities),
                item.location or "",
                item.deadline or "",
            ),
        )

    @staticmethod
    def _item_values(item: ContinuityItem) -> tuple:
        return (
            item.id,
            item.kind,
            item.status,
            item.subject,
            item.statement,
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
            statement=row["statement"],
            entities=json.loads(row["entities_json"]),
            location=row["location"],
            deadline=row["deadline"],
            created_turn=row["created_turn"],
            updated_turn=row["updated_turn"],
            closed_turn=row["closed_turn"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _fingerprint(item: ContinuityItemDraft | ContinuityItem) -> tuple:
    return (
        item.kind,
        item.subject.strip().casefold(),
        item.statement.strip().casefold(),
        tuple(sorted(entity.strip().casefold() for entity in item.entities)),
    )
