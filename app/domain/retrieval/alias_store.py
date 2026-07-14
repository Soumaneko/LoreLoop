import sqlite3
from app.runtime.sqlite import connect_sqlite, ensure_schema_version
from collections import defaultdict


class AliasStore:
    def __init__(self, db_path: str = ":memory:"):
        self._conn = connect_sqlite(db_path)
        self._init_table()

    def _init_table(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS aliases (
                alias TEXT PRIMARY KEY,
                canonical TEXT NOT NULL,
                kind TEXT NOT NULL,
                confidence REAL NOT NULL DEFAULT 1.0
            )
        """)
        ensure_schema_version(self._conn, "retrieval_alias", 1)
        self._conn.commit()

    def add_alias(
        self,
        alias: str,
        canonical: str,
        kind: str,
        confidence: float = 1.0,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO aliases VALUES (?, ?, ?, ?)",
            (alias, canonical, kind, confidence),
        )
        self._conn.commit()

    def resolve(self, text: str) -> str:
        row = self._conn.execute(
            "SELECT canonical FROM aliases WHERE alias = ?", (text,)
        ).fetchone()
        return row["canonical"] if row else text

    def expand(self, text: str) -> list[tuple[str, float]]:
        canonical = self.resolve(text)
        rows = self._conn.execute(
            "SELECT alias, confidence FROM aliases WHERE canonical = ?",
            (canonical,),
        ).fetchall()

        if not rows:
            return [(text, 1.0)]

        dedup: dict[str, float] = {}
        for row in rows:
            a = row["alias"]
            c = row["confidence"]
            if a not in dedup or c > dedup[a]:
                dedup[a] = c

        return sorted(dedup.items(), key=lambda x: x[1], reverse=True)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
