from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.time import utc_now
from .sqlite import connect_sqlite, ensure_column


def _now() -> str:
    return utc_now()


class CampaignHistory:
    """Durable pending-turn journal plus a bounded undo/redo snapshot stack."""

    def __init__(self, data_dir: Path, *, max_undo: int = 10) -> None:
        self.data_dir = Path(data_dir)
        self.runtime_dir = self.data_dir / ".runtime"
        self.snapshots_dir = self.data_dir / ".snapshots"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runtime_dir / "turn_history.db"
        self.max_undo = max_undo
        self._lock = threading.RLock()
        self._init_schema()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = connect_sqlite(self.db_path)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _stored_snapshot_path(self, path: Path) -> str:
        return path.relative_to(self.data_dir).as_posix()

    def _snapshot_path(self, stored: str) -> Path:
        path = Path(stored)
        if path.is_absolute():
            raise RuntimeError("Turn history contains a non-portable snapshot path")
        resolved = (self.data_dir / path).resolve()
        root = self.data_dir.resolve()
        if resolved != root and root not in resolved.parents:
            raise RuntimeError("Turn history snapshot path escapes the campaign")
        return resolved

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS turn_transactions (
                    turn_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    group_id TEXT,
                    turn_index INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    write_db INTEGER,
                    scene_text TEXT,
                    pre_snapshot_id TEXT,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS turn_snapshots (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    snapshot_id TEXT NOT NULL UNIQUE,
                    turn_id TEXT NOT NULL,
                    turn_index INTEGER NOT NULL,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    redo_path TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            ensure_column(conn, "turn_transactions", "state_changes_json", "TEXT")
            ensure_column(conn, "turn_transactions", "state_before_json", "TEXT")
            ensure_column(conn, "turn_transactions", "state_after_json", "TEXT")
            ensure_column(conn, "turn_transactions", "state_warnings_json", "TEXT")
            conn.commit()

    def save_pending(
        self,
        *,
        turn_id: str,
        kind: str,
        group_id: str | None,
        turn_index: int,
        payload: dict[str, Any],
    ) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO turn_transactions (
                    turn_id, kind, group_id, turn_index, payload_json,
                    status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'awaiting_confirm', ?, ?)
                ON CONFLICT(turn_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    status='awaiting_confirm', updated_at=excluded.updated_at
                """,
                (
                    turn_id,
                    kind,
                    group_id,
                    turn_index,
                    json.dumps(payload, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            conn.commit()

    def pending_records(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM turn_transactions
                WHERE status IN ('awaiting_confirm', 'confirming', 'failed')
                ORDER BY created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def record_state_audit(
        self,
        turn_id: str,
        *,
        state_changes: dict[str, Any],
        state_warnings: list[dict[str, Any]],
        state_before: dict[str, Any],
        state_after: dict[str, Any],
    ) -> None:
        """Attach the accepted incremental state plan to its turn journal."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE turn_transactions
                SET state_changes_json=?, state_warnings_json=?, state_before_json=?,
                    state_after_json=?, updated_at=?
                WHERE turn_id=?
                """,
                (
                    json.dumps(state_changes, ensure_ascii=False),
                    json.dumps(state_warnings, ensure_ascii=False),
                    json.dumps(state_before, ensure_ascii=False),
                    json.dumps(state_after, ensure_ascii=False),
                    _now(),
                    turn_id,
                ),
            )
            conn.commit()

    def get_transaction(self, turn_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM turn_transactions WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def begin_confirmation(
        self,
        turn_id: str,
        *,
        write_db: bool,
        scene_text: str | None,
    ) -> str | None:
        if not write_db:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM turn_transactions WHERE turn_id = ?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Turn transaction is not journaled: {turn_id}")
            abandoned_redo_path: Path | None = None
            if row["pre_snapshot_id"]:
                snapshot_id = row["pre_snapshot_id"]
                snapshot = conn.execute(
                    "SELECT status, redo_path FROM turn_snapshots WHERE snapshot_id=?",
                    (snapshot_id,),
                ).fetchone()
                if snapshot is None:
                    raise RuntimeError(
                        f"Turn snapshot is missing: {row['pre_snapshot_id']}"
                    )
                if snapshot["status"] == "undone":
                    if snapshot["redo_path"]:
                        abandoned_redo_path = self._snapshot_path(snapshot["redo_path"])
                    conn.execute(
                        """
                        UPDATE turn_snapshots
                        SET status='provisional', redo_path=NULL
                        WHERE snapshot_id=?
                        """,
                        (snapshot_id,),
                    )
            else:
                self._discard_redo_locked(conn)
                snapshot_id = uuid.uuid4().hex
                path = self.snapshots_dir / f"undo-{snapshot_id}"
                self._capture_snapshot(path)
                conn.execute(
                    """
                    INSERT INTO turn_snapshots (
                        snapshot_id, turn_id, turn_index, path, status, created_at
                    ) VALUES (?, ?, ?, ?, 'provisional', ?)
                    """,
                    (
                        snapshot_id,
                        turn_id,
                        row["turn_index"],
                        self._stored_snapshot_path(path),
                        _now(),
                    ),
                )
            conn.execute(
                """
                UPDATE turn_transactions SET status='confirming', write_db=1,
                    scene_text=?, pre_snapshot_id=?, error=NULL, updated_at=?
                WHERE turn_id=?
                """,
                (scene_text, snapshot_id, _now(), turn_id),
            )
            conn.commit()
            if abandoned_redo_path is not None:
                shutil.rmtree(abandoned_redo_path, ignore_errors=True)
            return snapshot_id

    def fail_confirmation(self, turn_id: str, error: BaseException) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT s.path FROM turn_transactions t
                JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                WHERE t.turn_id=? AND s.status='provisional'
                """,
                (turn_id,),
            ).fetchone()
            if row is not None:
                self._restore_snapshot(self._snapshot_path(row["path"]))
            conn.execute(
                """
                UPDATE turn_transactions SET status='failed', error=?, updated_at=?
                WHERE turn_id=?
                """,
                (f"{type(error).__name__}: {error}", _now(), turn_id),
            )
            conn.commit()

    def complete_confirmation(self, turn_id: str, group_id: str | None) -> None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT pre_snapshot_id FROM turn_transactions WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            abandoned_snapshots = []
            if group_id:
                abandoned_snapshots = conn.execute(
                    """
                    SELECT s.snapshot_id, s.path FROM turn_transactions t
                    JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                    WHERE t.group_id=? AND t.turn_id<>? AND s.status='provisional'
                    """,
                    (group_id, turn_id),
                ).fetchall()
            conn.execute(
                "UPDATE turn_transactions SET status='completed', updated_at=? WHERE turn_id=?",
                (_now(), turn_id),
            )
            if group_id:
                conn.execute(
                    """
                    UPDATE turn_transactions SET status='discarded', updated_at=?
                    WHERE group_id=? AND turn_id<>? AND status<>'completed'
                    """,
                    (_now(), group_id, turn_id),
                )
                conn.executemany(
                    "DELETE FROM turn_snapshots WHERE snapshot_id=?",
                    [(snapshot["snapshot_id"],) for snapshot in abandoned_snapshots],
                )
            if row and row["pre_snapshot_id"]:
                conn.execute(
                    "UPDATE turn_snapshots SET status='applied' WHERE snapshot_id=?",
                    (row["pre_snapshot_id"],),
                )
            conn.commit()
            for snapshot in abandoned_snapshots:
                shutil.rmtree(self._snapshot_path(snapshot["path"]), ignore_errors=True)
            self._trim_locked(conn)

    def discard(self, turn_ids: list[str]) -> None:
        if not turn_ids:
            return
        placeholders = ",".join("?" for _ in turn_ids)
        with self._lock, self._connect() as conn:
            snapshots = conn.execute(
                f"""
                SELECT s.snapshot_id, s.path FROM turn_transactions t
                JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                WHERE t.turn_id IN ({placeholders})
                  AND t.status IN ('awaiting_confirm', 'failed')
                  AND s.status='provisional'
                """,
                turn_ids,
            ).fetchall()
            conn.executemany(
                """
                UPDATE turn_transactions SET status='discarded',
                    pre_snapshot_id=NULL, updated_at=?
                WHERE turn_id=? AND status IN ('awaiting_confirm', 'failed')
                """,
                [(_now(), turn_id) for turn_id in turn_ids],
            )
            for snapshot in snapshots:
                conn.execute(
                    "DELETE FROM turn_snapshots WHERE snapshot_id=?",
                    (snapshot["snapshot_id"],),
                )
            conn.commit()
            for snapshot in snapshots:
                shutil.rmtree(self._snapshot_path(snapshot["path"]), ignore_errors=True)

    def recover_interrupted(self) -> bool:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT t.*, s.path FROM turn_transactions t
                JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                WHERE t.status IN ('confirming', 'failed')
                ORDER BY t.updated_at DESC LIMIT 1
                """
            ).fetchone()
            if row is None:
                return False
            self._restore_snapshot(self._snapshot_path(row["path"]))
            conn.execute(
                """
                UPDATE turn_transactions SET status='awaiting_confirm',
                    error='Recovered interrupted confirmation from pre-turn snapshot',
                    updated_at=? WHERE turn_id=?
                """,
                (_now(), row["turn_id"]),
            )
            conn.execute(
                "UPDATE turn_snapshots SET status='provisional' WHERE snapshot_id=?",
                (row["pre_snapshot_id"],),
            )
            conn.commit()
            return True

    def undo(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM turn_snapshots WHERE status='applied'
                ORDER BY sequence DESC LIMIT 1
                """
            ).fetchone()
            if row is None:
                raise ValueError("No confirmed turn is available to undo")
            redo_path = self.snapshots_dir / f"redo-{row['snapshot_id']}"
            self._capture_snapshot(redo_path)
            self._restore_snapshot(self._snapshot_path(row["path"]))
            conn.execute(
                "UPDATE turn_snapshots SET status='undone', redo_path=? WHERE sequence=?",
                (self._stored_snapshot_path(redo_path), row["sequence"]),
            )
            transaction = conn.execute(
                "SELECT group_id FROM turn_transactions WHERE turn_id=?",
                (row["turn_id"],),
            ).fetchone()
            if transaction is None:
                raise RuntimeError(
                    f"Confirmed turn transaction is missing: {row['turn_id']}"
                )
            if transaction["group_id"]:
                conn.execute(
                    """
                    UPDATE turn_transactions
                    SET status='awaiting_confirm', error=NULL, updated_at=?
                    WHERE group_id=? AND status IN ('completed', 'discarded')
                    """,
                    (_now(), transaction["group_id"]),
                )
            else:
                conn.execute(
                    """
                    UPDATE turn_transactions
                    SET status='awaiting_confirm', error=NULL, updated_at=?
                    WHERE turn_id=?
                    """,
                    (_now(), row["turn_id"]),
                )
            conn.commit()
            result = self._snapshot_data(row, "undone")
            result["restored_pending_turn_ids"] = self._redo_pending_turn_ids_locked(
                conn, row["turn_id"]
            )
            return result

    def redo(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM turn_snapshots WHERE status='undone'
                ORDER BY sequence ASC LIMIT 1
                """
            ).fetchone()
            if row is None or not row["redo_path"]:
                raise ValueError("No undone turn is available to redo")
            redo_path = self._snapshot_path(row["redo_path"])
            self._restore_snapshot(redo_path)
            shutil.rmtree(redo_path, ignore_errors=True)
            conn.execute(
                "UPDATE turn_snapshots SET status='applied', redo_path=NULL WHERE sequence=?",
                (row["sequence"],),
            )
            transaction = conn.execute(
                "SELECT group_id FROM turn_transactions WHERE turn_id=?",
                (row["turn_id"],),
            ).fetchone()
            if transaction is None:
                raise RuntimeError(
                    f"Undone turn transaction is missing: {row['turn_id']}"
                )
            conn.execute(
                """
                UPDATE turn_transactions
                SET status='completed', error=NULL, updated_at=?
                WHERE turn_id=?
                """,
                (_now(), row["turn_id"]),
            )
            if transaction["group_id"]:
                conn.execute(
                    """
                    UPDATE turn_transactions
                    SET status='discarded', updated_at=?
                    WHERE group_id=? AND turn_id<>?
                      AND status='awaiting_confirm'
                    """,
                    (_now(), transaction["group_id"], row["turn_id"]),
                )
            conn.commit()
            return self._snapshot_data(row, "redone")

    def redo_pending_turn_ids(self) -> list[str]:
        """Pending drafts restored by the next available redo operation."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT turn_id FROM turn_snapshots WHERE status='undone'
                ORDER BY sequence ASC LIMIT 1
                """
            ).fetchone()
            if row is None:
                return []
            return self._redo_pending_turn_ids_locked(conn, row["turn_id"])

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            applied = conn.execute(
                "SELECT COUNT(*) FROM turn_snapshots WHERE status='applied'"
            ).fetchone()[0]
            undone = conn.execute(
                "SELECT COUNT(*) FROM turn_snapshots WHERE status='undone'"
            ).fetchone()[0]
            rows = conn.execute(
                """
                SELECT sequence, snapshot_id, turn_id, turn_index, status, created_at
                FROM turn_snapshots WHERE status IN ('applied', 'undone')
                ORDER BY sequence DESC LIMIT 10
                """
            ).fetchall()
        redo_pending_turn_ids = self.redo_pending_turn_ids() if undone else []
        return {
            "can_undo": applied > 0,
            "can_redo": undone > 0,
            "undo_count": applied,
            "redo_count": undone,
            "redo_pending_turn_ids": redo_pending_turn_ids,
            "snapshots": [dict(row) for row in rows],
        }

    @staticmethod
    def _redo_pending_turn_ids_locked(conn, turn_id: str) -> list[str]:
        transaction = conn.execute(
            "SELECT group_id FROM turn_transactions WHERE turn_id=?",
            (turn_id,),
        ).fetchone()
        if transaction is None:
            return []
        if transaction["group_id"]:
            rows = conn.execute(
                """
                SELECT turn_id FROM turn_transactions
                WHERE group_id=? AND status='awaiting_confirm'
                ORDER BY created_at
                """,
                (transaction["group_id"],),
            ).fetchall()
            return [item["turn_id"] for item in rows]
        row = conn.execute(
            """
            SELECT turn_id FROM turn_transactions
            WHERE turn_id=? AND status='awaiting_confirm'
            """,
            (turn_id,),
        ).fetchone()
        return [row["turn_id"]] if row is not None else []

    def _capture_snapshot(self, destination: Path) -> None:
        if destination.exists():
            shutil.rmtree(destination)
        destination.mkdir(parents=True)
        files = []
        for source in sorted(self.data_dir.glob("*.db")):
            target = destination / source.name
            src_conn = sqlite3.connect(str(source))
            dst_conn = sqlite3.connect(str(target))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            files.append(source.name)
        for name in ("core_memory.json", "settings.json", "initialization.json"):
            source = self.data_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
                files.append(name)
        (destination / "manifest.json").write_text(
            json.dumps({"created_at": _now(), "files": files}, indent=2),
            encoding="utf-8",
        )

    def _restore_snapshot(self, source: Path) -> None:
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        expected = set(manifest["files"])
        for current in self.data_dir.glob("*.db"):
            if current.name not in expected:
                for suffix in ("-wal", "-shm"):
                    Path(f"{current}{suffix}").unlink(missing_ok=True)
                current.unlink()
        for name in ("core_memory.json", "settings.json", "initialization.json"):
            current = self.data_dir / name
            if current.exists() and name not in expected:
                current.unlink()
        for name in expected:
            snapshot_file = source / name
            destination = self.data_dir / name
            if snapshot_file.suffix == ".db":
                self._restore_database(snapshot_file, destination)
            else:
                shutil.copy2(snapshot_file, destination)

    @staticmethod
    def _restore_database(source: Path, destination: Path) -> None:
        """Restore through SQLite so WAL readers cannot revive replaced pages."""
        source_uri = f"file:{source.resolve()}?mode=ro"
        source_conn = sqlite3.connect(source_uri, uri=True, timeout=30)
        destination_conn = sqlite3.connect(str(destination), timeout=30)
        try:
            destination_conn.execute("PRAGMA busy_timeout = 30000")
            source_conn.backup(destination_conn)
            destination_conn.commit()
            destination_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            destination_conn.close()
            source_conn.close()

    def _discard_redo_locked(self, conn) -> None:
        rows = conn.execute(
            "SELECT path, redo_path FROM turn_snapshots WHERE status='undone'"
        ).fetchall()
        for row in rows:
            shutil.rmtree(self._snapshot_path(row["path"]), ignore_errors=True)
            if row["redo_path"]:
                shutil.rmtree(self._snapshot_path(row["redo_path"]), ignore_errors=True)
        conn.execute("DELETE FROM turn_snapshots WHERE status='undone'")

    def _trim_locked(self, conn) -> None:
        rows = conn.execute(
            """
            SELECT sequence, path, redo_path FROM turn_snapshots
            WHERE status='applied' ORDER BY sequence DESC
            """
        ).fetchall()
        for row in rows[self.max_undo :]:
            shutil.rmtree(self._snapshot_path(row["path"]), ignore_errors=True)
            if row["redo_path"]:
                shutil.rmtree(self._snapshot_path(row["redo_path"]), ignore_errors=True)
            conn.execute("DELETE FROM turn_snapshots WHERE sequence=?", (row["sequence"],))
        conn.commit()

    @staticmethod
    def _snapshot_data(row, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "turn_id": row["turn_id"],
            "turn_index": row["turn_index"],
            "snapshot_id": row["snapshot_id"],
        }
