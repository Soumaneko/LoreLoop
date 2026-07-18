from __future__ import annotations

import re
from collections.abc import Iterable

from app.domain.narrative_canon_query.schemas import CanonEntityReference
from app.domain.short_term_memory.schemas import ShortTermScene
from app.runtime.sqlite import connect_sqlite, ensure_schema_version, transaction

from .schemas import CanonEvidenceDocument


class CanonEvidenceStore:
    """Rebuildable FTS index over authoritative confirmed narrative records."""

    def __init__(self, db_path: str = ":memory:"):
        self._db_path = db_path
        self._conn = connect_sqlite(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS canon_documents (
                document_id TEXT PRIMARY KEY,
                source_type TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                scene_id TEXT NOT NULL,
                paragraph_index INTEGER,
                text TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_canon_documents_turn
            ON canon_documents(turn_index, source_type, paragraph_index);

            CREATE TABLE IF NOT EXISTS canon_mentions (
                document_id TEXT NOT NULL,
                canonical_name TEXT NOT NULL,
                surface TEXT NOT NULL,
                PRIMARY KEY(document_id, canonical_name, surface),
                FOREIGN KEY(document_id) REFERENCES canon_documents(document_id)
                    ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_canon_mentions_name
            ON canon_mentions(canonical_name, document_id);

            CREATE VIRTUAL TABLE IF NOT EXISTS canon_documents_fts
            USING fts5(document_id UNINDEXED, text, tokenize='trigram');
            """
        )
        ensure_schema_version(self._conn, "canon_evidence", 1)
        self._conn.commit()

    def rebuild(
        self,
        scenes: Iterable[ShortTermScene],
        entity_catalog: list[CanonEntityReference],
    ) -> None:
        documents: list[CanonEvidenceDocument] = []
        for scene in scenes:
            clean_input = scene.user_input.strip()
            if clean_input:
                documents.append(CanonEvidenceDocument(
                    document_id=f"turn_{scene.turn_index}_input",
                    source_type="player_input",
                    turn_index=scene.turn_index,
                    scene_id=scene.scene_id,
                    text=clean_input,
                ))
            for paragraph_index, paragraph in enumerate(
                _split_paragraphs(scene.scene_text),
                start=1,
            ):
                documents.append(CanonEvidenceDocument(
                    document_id=f"turn_{scene.turn_index}_p{paragraph_index:03d}",
                    source_type="scene_paragraph",
                    turn_index=scene.turn_index,
                    scene_id=scene.scene_id,
                    paragraph_index=paragraph_index,
                    text=paragraph,
                ))

        surfaces = _catalog_surfaces(entity_catalog)
        with transaction(self._conn):
            self._conn.execute("DELETE FROM canon_mentions")
            self._conn.execute("DELETE FROM canon_documents")
            self._conn.execute("DELETE FROM canon_documents_fts")
            for document in documents:
                self._conn.execute(
                    """
                    INSERT INTO canon_documents(
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
                    "INSERT INTO canon_documents_fts(document_id, text) VALUES (?, ?)",
                    (document.document_id, document.text),
                )
                folded_text = document.text.casefold()
                for canonical_name, surface in surfaces:
                    if surface.casefold() not in folded_text:
                        continue
                    self._conn.execute(
                        """
                        INSERT OR IGNORE INTO canon_mentions(
                            document_id, canonical_name, surface
                        ) VALUES (?, ?, ?)
                        """,
                        (document.document_id, canonical_name, surface),
                    )

    def list_documents(self) -> list[CanonEvidenceDocument]:
        rows = self._conn.execute(
            """
            SELECT * FROM canon_documents
            ORDER BY turn_index, source_type, paragraph_index
            """
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def documents_for_turn(self, turn_index: int) -> list[CanonEvidenceDocument]:
        rows = self._conn.execute(
            """
            SELECT * FROM canon_documents WHERE turn_index = ?
            ORDER BY CASE source_type WHEN 'player_input' THEN 0 ELSE 1 END,
                     paragraph_index
            """,
            (turn_index,),
        ).fetchall()
        return [self._row_to_document(row) for row in rows]

    def mentions_for_documents(
        self,
        document_ids: list[str],
    ) -> dict[str, set[str]]:
        if not document_ids:
            return {}
        placeholders = ",".join("?" for _ in document_ids)
        rows = self._conn.execute(
            f"""
            SELECT document_id, canonical_name FROM canon_mentions
            WHERE document_id IN ({placeholders})
            """,
            document_ids,
        ).fetchall()
        result: dict[str, set[str]] = {}
        for row in rows:
            result.setdefault(row["document_id"], set()).add(row["canonical_name"])
        return result

    def fts_search(self, term: str, *, limit: int = 100) -> dict[str, float]:
        clean = term.strip()
        if len(clean) < 3:
            return {}
        expression = '"' + clean.replace('"', '""') + '"'
        try:
            rows = self._conn.execute(
                """
                SELECT document_id, bm25(canon_documents_fts) AS rank
                FROM canon_documents_fts
                WHERE canon_documents_fts MATCH ?
                ORDER BY rank LIMIT ?
                """,
                (expression, limit),
            ).fetchall()
        except Exception:
            return {}
        return {
            row["document_id"]: max(0.0, -float(row["rank"]))
            for row in rows
        }

    def count(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) FROM canon_documents").fetchone()
        return int(row[0])

    def turn_count(self) -> int:
        row = self._conn.execute(
            "SELECT COUNT(DISTINCT turn_index) FROM canon_documents"
        ).fetchone()
        return int(row[0])

    @staticmethod
    def _row_to_document(row) -> CanonEvidenceDocument:
        return CanonEvidenceDocument(
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


def _split_paragraphs(scene_text: str) -> list[str]:
    return [
        paragraph
        for part in re.split(r"\n{2,}", scene_text.strip())
        if (paragraph := part.strip())
    ]


def _catalog_surfaces(
    catalog: list[CanonEntityReference],
) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for entity in catalog:
        for raw_surface in [entity.name, *entity.aliases]:
            surface = raw_surface.strip()
            key = (entity.name.casefold(), surface.casefold())
            if not surface or key in seen:
                continue
            seen.add(key)
            values.append((entity.name, surface))
    values.sort(key=lambda item: len(item[1]), reverse=True)
    return values
