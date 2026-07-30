from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterable

from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from .schemas import ConfirmedScene, HistoryDocument


class ConfirmedHistoryStore:
    """Persistent FTS5 index over raw confirmed player inputs and scene prose."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = connect_sqlite(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS history_turns (
                turn_index INTEGER PRIMARY KEY,
                scene_id TEXT NOT NULL,
                content_hash TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS history_documents (
                document_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                scene_id TEXT NOT NULL,
                paragraph_index INTEGER,
                text TEXT NOT NULL,
                FOREIGN KEY(turn_index) REFERENCES history_turns(turn_index)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_history_documents_turn
            ON history_documents(turn_index, source_type, paragraph_index);

            CREATE VIRTUAL TABLE IF NOT EXISTS history_documents_fts
            USING fts5(document_id UNINDEXED, text, tokenize='trigram');
            """
        )
        ensure_schema_version(self._conn, "confirmed_history", 1)
        self._conn.commit()

    def upsert_turn(
        self,
        *,
        scene_id: str,
        turn_index: int,
        user_input: str,
        scene_text: str,
    ) -> bool:
        content_hash = _content_hash(
            scene_id=scene_id,
            user_input=user_input,
            scene_text=scene_text,
        )
        existing = self._conn.execute(
            "SELECT content_hash FROM history_turns WHERE turn_index = ?",
            (turn_index,),
        ).fetchone()
        if existing is not None and existing["content_hash"] == content_hash:
            return False

        documents = _documents_for_turn(
            scene_id=scene_id,
            turn_index=turn_index,
            user_input=user_input,
            scene_text=scene_text,
        )
        with transaction(self._conn):
            self._delete_turn(turn_index)
            self._conn.execute(
                """
                INSERT INTO history_turns(turn_index, scene_id, content_hash)
                VALUES (?, ?, ?)
                """,
                (turn_index, scene_id, content_hash),
            )
            for document in documents:
                self._conn.execute(
                    """
                    INSERT INTO history_documents(
                        document_id, source_type, turn_index, scene_id,
                        paragraph_index, text
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        document.document_id,
                        document.source_type,
                        document.turn_index,
                        document.scene_id,
                        document.paragraph_index,
                        document.text,
                    ),
                )
                self._conn.execute(
                    """
                    INSERT INTO history_documents_fts(document_id, text)
                    VALUES (?, ?)
                    """,
                    (document.document_id, document.text),
                )
        return True

    def sync(self, scenes: Iterable[ConfirmedScene]) -> int:
        """Make the derived index match authoritative confirmed history."""
        expected = {scene.turn_index: scene for scene in scenes}
        stored_turns = {
            int(row["turn_index"])
            for row in self._conn.execute(
                "SELECT turn_index FROM history_turns"
            ).fetchall()
        }
        changed = 0
        extra_turns = stored_turns - set(expected)
        if extra_turns:
            with transaction(self._conn):
                for turn_index in sorted(extra_turns):
                    self._delete_turn(turn_index)
                    changed += 1
        for turn_index in sorted(expected):
            scene = expected[turn_index]
            if self.upsert_turn(
                scene_id=scene.scene_id,
                turn_index=scene.turn_index,
                user_input=scene.user_input,
                scene_text=scene.scene_text,
            ):
                changed += 1
        return changed

    def rebuild(self, scenes: Iterable[ConfirmedScene]) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM history_documents_fts")
            self._conn.execute("DELETE FROM history_documents")
            self._conn.execute("DELETE FROM history_turns")
        self.sync(scenes)

    def clear(self) -> None:
        with transaction(self._conn):
            self._conn.execute("DELETE FROM history_documents_fts")
            self._conn.execute("DELETE FROM history_documents")
            self._conn.execute("DELETE FROM history_turns")

    def _delete_turn(self, turn_index: int) -> None:
        rows = self._conn.execute(
            "SELECT document_id FROM history_documents WHERE turn_index = ?",
            (turn_index,),
        ).fetchall()
        document_ids = [row["document_id"] for row in rows]
        if document_ids:
            placeholders = ",".join("?" for _ in document_ids)
            self._conn.execute(
                f"""
                DELETE FROM history_documents_fts
                WHERE document_id IN ({placeholders})
                """,
                document_ids,
            )
        self._conn.execute(
            "DELETE FROM history_turns WHERE turn_index = ?",
            (turn_index,),
        )

    def list_documents(self) -> list[HistoryDocument]:
        rows = self._conn.execute(
            """
            SELECT * FROM history_documents
            ORDER BY turn_index,
                     CASE source_type WHEN 'player_input' THEN 0 ELSE 1 END,
                     paragraph_index
            """
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def documents_for_turn(self, turn_index: int) -> list[HistoryDocument]:
        rows = self._conn.execute(
            """
            SELECT * FROM history_documents WHERE turn_index = ?
            ORDER BY CASE source_type WHEN 'player_input' THEN 0 ELSE 1 END,
                     paragraph_index
            """,
            (turn_index,),
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def fts_search(self, term: str, *, limit: int = 100) -> dict[str, float]:
        clean = term.strip()
        if len(clean) < 3:
            return {}
        expression = '"' + clean.replace('"', '""') + '"'
        try:
            rows = self._conn.execute(
                """
                SELECT document_id, bm25(history_documents_fts) AS rank
                FROM history_documents_fts
                WHERE history_documents_fts MATCH ?
                ORDER BY rank LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            return {}
        return {
            row["document_id"]: max(0.0, -float(row["rank"]))
            for row in rows
        }

    def count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM history_documents"
        ).fetchone()
        return int(row[0])

    def turn_count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM history_turns").fetchone()
        return int(row[0])

    @staticmethod
    def _row_to_document(row) -> HistoryDocument:
        return HistoryDocument(
            document_id=row["document_id"],
            source_type=row["source_type"],
            turn_index=row["turn_index"],
            scene_id=row["scene_id"],
            paragraph_index=row["paragraph_index"],
            text=row["text"],
        )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _documents_for_turn(
    *,
    scene_id: str,
    turn_index: int,
    user_input: str,
    scene_text: str,
) -> list[HistoryDocument]:
    documents: list[HistoryDocument] = []
    clean_input = user_input.strip()
    if clean_input:
        documents.append(HistoryDocument(
            document_id=f"turn_{turn_index}_input",
            source_type="player_input",
            turn_index=turn_index,
            scene_id=scene_id,
            text=clean_input,
        ))
    for paragraph_index, paragraph in enumerate(
        _split_paragraphs(scene_text),
        start=1,
    ):
        documents.append(HistoryDocument(
            document_id=f"turn_{turn_index}_p{paragraph_index:03d}",
            source_type="scene_paragraph",
            turn_index=turn_index,
            scene_id=scene_id,
            paragraph_index=paragraph_index,
            text=paragraph,
        ))
    return documents


def _split_paragraphs(scene_text: str) -> list[str]:
    return [
        paragraph
        for part in re.split(r"\n{2,}", scene_text.strip())
        if (paragraph := part.strip())
    ]


def _content_hash(*, scene_id: str, user_input: str, scene_text: str) -> str:
    payload = "\0".join((scene_id, user_input.strip(), scene_text.strip()))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
