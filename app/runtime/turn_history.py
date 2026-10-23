from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from typing import Any, Iterator

from app.core.time import utc_now
from .snapshots import SnapshotError, capture_snapshot, install_snapshot
from .sqlite import connect_sqlite


def _now() -> str:
    return utc_now()


def ensure_control_database(paths) -> str:
    """Open or create the current control database."""
    control = Path(paths.control_db)
    if control.is_file():
        _validate_control_database(control)
        _ensure_control_schema(control)
        return "validated"
    control.parent.mkdir(parents=True, exist_ok=True)
    _ensure_control_schema(control)
    return "created"


def _validate_control_database(db_path: Path) -> None:
    """Fail closed when an existing control database is not a valid control DB.

    Only the two base control tables are checked here; the rest of the schema is
    created/upgraded by :func:`_ensure_control_schema`.
    """
    conn = connect_sqlite(db_path)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise RuntimeError(
                f"control.db failed quick_check: {quick[0] if quick else 'no result'}"
            )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [
            name for name in ("turn_transactions", "turn_snapshots")
            if name not in tables
        ]
        if missing:
            raise RuntimeError(
                "control.db is missing required control tables: "
                + ", ".join(sorted(missing))
            )
    finally:
        conn.close()


def ensure_control_database_by_dir(data_dir: Path) -> str:
    """Convenience for direct data_dir callers (tests/tools)."""
    from .paths import CampaignPaths

    return ensure_control_database(CampaignPaths.from_data_dir(data_dir))


def _ensure_control_schema(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_sqlite(db_path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_notices (
                notice_id TEXT PRIMARY KEY, code TEXT NOT NULL,
                message TEXT NOT NULL, operation_type TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turn_transactions (
                turn_id TEXT PRIMARY KEY, kind TEXT NOT NULL, group_id TEXT,
                turn_index INTEGER NOT NULL, payload_json TEXT NOT NULL,
                status TEXT NOT NULL, write_db INTEGER, scene_text TEXT,
                pre_snapshot_id TEXT, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turn_snapshots (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_id TEXT NOT NULL UNIQUE, turn_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL, path TEXT NOT NULL,
                status TEXT NOT NULL, redo_path TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS control_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                restore_target TEXT,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT OR IGNORE INTO control_state(id, updated_at) VALUES (1, ?)",
            (_now(),),
        )


class CampaignHistory:
    """Pending drafts and bounded snapshots; campaign.db owns saved results."""

    def __init__(self, data_dir: Path, *, max_undo: int = 10) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.runtime_dir = self.data_dir / ".runtime"
        self.snapshots_dir = self.data_dir / ".snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        ensure_control_database_by_dir(self.data_dir)
        self.db_path = self.runtime_dir / "control.db"
        self.campaign_db = self.data_dir / "campaign.db"
        self.max_undo = max_undo
        self._lock = threading.RLock()
        from .campaign_migration import ensure_campaign_database
        from .paths import CampaignPaths

        ensure_campaign_database(CampaignPaths.from_data_dir(self.data_dir))

    @contextmanager
    def _campaign_connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock, closing(connect_sqlite(self.campaign_db)) as conn, conn:
            yield conn

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

    def get_control_state(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT restore_target FROM control_state WHERE id=1").fetchone()
        return {"restore_target": row["restore_target"]}

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
                "SELECT * FROM turn_transactions WHERE status IN ('awaiting_confirm','confirming','failed') ORDER BY created_at"
            ).fetchall()
        with self._campaign_connect() as conn:
            saved = conn.execute("SELECT turn_id, group_id FROM turn_confirmations").fetchall()
        saved_ids = {r["turn_id"] for r in saved}
        saved_groups = {r["group_id"] for r in saved if r["group_id"]}
        return [dict(row) for row in rows
                if row["turn_id"] not in saved_ids and row["group_id"] not in saved_groups]

    def record_confirmation(
        self,
        connection: sqlite3.Connection,
        turn_id: str,
        *,
        scene_text: str,
        initialization_fingerprint: str | None = None,
        state_changes: dict | None = None,
        state_warnings: list | None = None,
        state_before: dict | None = None,
        state_after: dict | None = None,
    ) -> None:
        """Write the selected version alongside its domain writes, without committing."""
        if not connection.in_transaction:
            raise RuntimeError("Confirmation must be recorded inside its campaign transaction")
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM turn_transactions WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            raise RuntimeError(f"Pending turn not found: {turn_id}")
        connection.execute(
            """
            INSERT INTO turn_confirmations (
                turn_id, kind, group_id, turn_index, scene_text, initialization_fingerprint,
                state_changes_json, state_warnings_json, state_before_json, state_after_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (turn_id, row["kind"], row["group_id"], row["turn_index"], scene_text,
             initialization_fingerprint,
             *[json.dumps(value, ensure_ascii=False) if value is not None else None
               for value in (state_changes, state_warnings, state_before, state_after)], _now()),
        )

        connection.execute(
            """UPDATE turn_confirmations SET state_changes_json=NULL,
                state_warnings_json=NULL, state_before_json=NULL, state_after_json=NULL
                WHERE turn_index <= ?""",
            (row["turn_index"] - self.max_undo,),
        )

    def record_runtime_notice(self, operation_id: str, operation_type: str, code: str, message: str) -> dict[str, str]:
        notice = {"notice_id": f"{operation_id}:{code}", "code": code, "message": message,
                  "operation_type": operation_type, "created_at": _now()}
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_notices (notice_id, code, message, operation_type, created_at) VALUES (:notice_id, :code, :message, :operation_type, :created_at)", notice,
            )
            conn.execute("DELETE FROM runtime_notices WHERE notice_id NOT IN (SELECT notice_id FROM runtime_notices ORDER BY created_at DESC, rowid DESC LIMIT 40)")
            conn.commit()
        return notice

    def runtime_notices(self) -> list[dict[str, str]]:
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT notice_id, code, message, operation_type, created_at FROM runtime_notices ORDER BY created_at DESC, rowid DESC LIMIT 20"
            )]

    def get_transaction(self, turn_id: str) -> dict[str, Any] | None:
        with self._campaign_connect() as conn:
            saved = conn.execute("SELECT * FROM turn_confirmations WHERE turn_id=?", (turn_id,)).fetchone()
        if saved is not None:
            return {**dict(saved), "status": "completed"}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM turn_transactions WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result["status"] == "completed":
            result["status"] = "awaiting_confirm"
        return result

    def begin_confirmation(self, turn_id: str, *, write_db: bool, scene_text: str | None) -> str | None:
        if not write_db:
            return None
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM turn_transactions WHERE turn_id=?", (turn_id,)).fetchone()
            if row is None:
                raise RuntimeError(f"Pending turn not found: {turn_id}")
            if self.get_transaction(turn_id)["status"] == "completed":
                raise ValueError("This version is already confirmed")
            snapshot_id = uuid.uuid4().hex
            path = self.snapshots_dir / f"undo-{snapshot_id}"
            self._capture_snapshot(path)
            # A retry captures the current base; abandoned preparations have no undo role.
            previous = conn.execute(
                "SELECT * FROM turn_snapshots WHERE turn_id=? AND status='provisional'", (turn_id,)
            ).fetchall()
            conn.execute("DELETE FROM turn_snapshots WHERE turn_id=? AND status='provisional'", (turn_id,))
            conn.execute(
                "INSERT INTO turn_snapshots(snapshot_id,turn_id,turn_index,path,status,created_at) VALUES (?,?,?,?,'provisional',?)",
                (snapshot_id, turn_id, row["turn_index"], self._stored_snapshot_path(path), _now()),
            )
            conn.execute(
                "UPDATE turn_transactions SET status='confirming', write_db=1, scene_text=?, pre_snapshot_id=?, error=NULL, updated_at=? WHERE turn_id=?",
                (scene_text, snapshot_id, _now(), turn_id),
            )
        for snapshot in previous:
            shutil.rmtree(self._snapshot_path(snapshot["path"]), ignore_errors=True)
        return snapshot_id

    def fail_confirmation(self, turn_id: str, error: BaseException) -> None:
        """The campaign transaction has rolled back; keep the draft for retry."""
        record = self.get_transaction(turn_id)
        if record is None:
            raise RuntimeError(f"Pending turn not found: {turn_id}")
        if record["status"] == "completed":
            return
        with self._connect() as conn:
            conn.execute(
                "UPDATE turn_transactions SET status='failed', error=?, updated_at=? WHERE turn_id=?",
                (f"{type(error).__name__}: {error}", _now(), turn_id),
            )

    def complete_confirmation(self, turn_id: str, group_id: str | None) -> None:
        """Publish undo availability and retire drafts after the campaign commit."""
        record = self.get_transaction(turn_id)
        if record is None or record["status"] != "completed":
            raise RuntimeError(f"Confirmation is not saved: {turn_id}")
        with self._lock, self._connect() as conn:
            pending = conn.execute(
                "SELECT t.status, t.pre_snapshot_id, s.status AS snapshot_status "
                "FROM turn_transactions t LEFT JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id "
                "WHERE t.turn_id=?", (turn_id,)
            ).fetchone()
            if pending is None or (pending["status"] == "completed" and pending["snapshot_status"] != "provisional"):
                return
            abandoned = conn.execute(
                "SELECT s.path, s.redo_path FROM turn_snapshots s JOIN turn_transactions t ON t.turn_id=s.turn_id WHERE t.group_id=? AND (s.status='provisional' OR s.turn_id=?) AND s.snapshot_id<>?",
                (group_id, turn_id, pending["pre_snapshot_id"]),
            ).fetchall()
            conn.execute(
                "DELETE FROM turn_snapshots WHERE (status='provisional' OR turn_id=?) AND snapshot_id<>? AND turn_id IN (SELECT turn_id FROM turn_transactions WHERE group_id=?)",
                (turn_id, pending["pre_snapshot_id"], group_id),
            )
            discarded = self._discard_redo_locked(conn)
            conn.execute("UPDATE turn_transactions SET status='completed', error=NULL, updated_at=? WHERE turn_id=?", (_now(), turn_id))
            if group_id:
                conn.execute("UPDATE turn_transactions SET status='discarded', updated_at=? WHERE group_id=? AND turn_id<>?", (_now(), group_id, turn_id))
            conn.execute("UPDATE turn_snapshots SET status='applied' WHERE snapshot_id=?", (pending["pre_snapshot_id"],))
            trimmed = self._trim_locked(conn)
            self._trim_transactions_locked(conn)
        for snapshot in [*abandoned, *discarded, *trimmed]:
            for stored in (snapshot["path"], snapshot["redo_path"]):
                if stored:
                    shutil.rmtree(self._snapshot_path(stored), ignore_errors=True)

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
        """Read the saved fact to settle unfinished confirmation bookkeeping."""
        changed = False
        with self._lock:
            with self._connect() as conn:
                target = conn.execute("SELECT restore_target FROM control_state WHERE id=1").fetchone()[0]
            if target:
                self._restore_snapshot(self._snapshot_path(target))
                changed = True
            with self._connect() as conn:
                snapshots = conn.execute("SELECT * FROM turn_snapshots ORDER BY sequence").fetchall()
            with self._campaign_connect() as campaign:
                saved = {r["turn_id"] for r in campaign.execute("SELECT turn_id FROM turn_confirmations")}
            for row in snapshots:
                if row["status"] == "provisional" and row["turn_id"] in saved:
                    self.complete_confirmation(row["turn_id"], self.get_transaction(row["turn_id"])["group_id"])
                    changed = True
            # Publishing a saved confirmation retires its old redo branch.
            with self._connect() as conn:
                snapshots = conn.execute("SELECT * FROM turn_snapshots ORDER BY sequence").fetchall()
            for row in snapshots:
                with self._connect() as conn:
                    if row["status"] == "provisional":
                        cursor = conn.execute(
                            "UPDATE turn_transactions SET status='failed', error='Confirmation was interrupted; retry the draft', updated_at=? WHERE turn_id=? AND status='confirming'",
                            (_now(), row["turn_id"]),
                        )
                        changed = bool(cursor.rowcount) or changed
                    else:
                        desired = "applied" if row["turn_id"] in saved else "undone"
                        if desired != row["status"]:
                            conn.execute("UPDATE turn_snapshots SET status=? WHERE sequence=?", (desired, row["sequence"]))
                            self._set_group_status(conn, row["turn_id"], desired == "applied")
                            changed = True
                        if desired == "applied" and row["redo_path"]:
                            conn.execute("UPDATE turn_snapshots SET redo_path=NULL WHERE sequence=?", (row["sequence"],))
                            shutil.rmtree(self._snapshot_path(row["redo_path"]), ignore_errors=True)
                            changed = True
        return changed

    @staticmethod
    def _set_group_status(conn: sqlite3.Connection, turn_id: str, applied: bool) -> None:
        row = conn.execute("SELECT group_id FROM turn_transactions WHERE turn_id=?", (turn_id,)).fetchone()
        if row is None:
            return
        if row["group_id"]:
            conn.execute("UPDATE turn_transactions SET status=?, error=NULL, updated_at=? WHERE group_id=?",
                         ("discarded" if applied else "awaiting_confirm", _now(), row["group_id"]))
        conn.execute("UPDATE turn_transactions SET status=?, error=NULL, updated_at=? WHERE turn_id=?",
                     ("completed" if applied else "awaiting_confirm", _now(), turn_id))

    def _recover_history_operation(self, action: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            target = conn.execute("SELECT restore_target FROM control_state WHERE id=1").fetchone()[0]
            column = "path" if action == "undone" else "redo_path"
            row = conn.execute(
                f"SELECT * FROM turn_snapshots WHERE {column}=?", (target,)
            ).fetchone()
        self.recover_interrupted()
        if row is None:
            return None
        # Recovery already completed this request; do not select another turn.
        result = self._snapshot_data(row, action)
        if action == "undone":
            with self._connect() as conn:
                result["restored_pending_turn_ids"] = self._redo_pending_turn_ids_locked(conn, row["turn_id"])
        return result

    def undo(self) -> dict[str, Any]:
        with self._lock:
            recovered = self._recover_history_operation("undone")
            if recovered is not None:
                return recovered
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM turn_snapshots WHERE status='applied' ORDER BY sequence DESC LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No confirmed turn is available to undo")
            redo = self.snapshots_dir / f"redo-{row['snapshot_id']}"
            if not row["redo_path"] or not redo.is_dir():
                self._capture_snapshot(redo)
            # Keep the redo location before installing so a failed install can be retried.
            with self._connect() as conn:
                conn.execute("UPDATE turn_snapshots SET redo_path=? WHERE sequence=?", (self._stored_snapshot_path(redo), row["sequence"]))
            self._restore_snapshot(self._snapshot_path(row["path"]))
            with self._connect() as conn:
                conn.execute("UPDATE turn_snapshots SET status='undone' WHERE sequence=?", (row["sequence"],))
                self._set_group_status(conn, row["turn_id"], False)
                result = self._snapshot_data(row, "undone")
                result["restored_pending_turn_ids"] = self._redo_pending_turn_ids_locked(conn, row["turn_id"])
            return result

    def redo(self) -> dict[str, Any]:
        with self._lock:
            recovered = self._recover_history_operation("redone")
            if recovered is not None:
                return recovered
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM turn_snapshots WHERE status='undone' AND redo_path IS NOT NULL ORDER BY sequence LIMIT 1").fetchone()
            if row is None:
                raise ValueError("No undone turn is available to redo")
            self._restore_snapshot(self._snapshot_path(row["redo_path"]))
            with self._connect() as conn:
                conn.execute("UPDATE turn_snapshots SET status='applied', redo_path=NULL WHERE sequence=?", (row["sequence"],))
                self._set_group_status(conn, row["turn_id"], True)
            shutil.rmtree(self._snapshot_path(row["redo_path"]), ignore_errors=True)
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
            rows = conn.execute("SELECT sequence,snapshot_id,turn_id,turn_index,status,created_at FROM turn_snapshots WHERE status IN ('applied','undone') ORDER BY sequence DESC").fetchall()
        applied = sum(r["status"] == "applied" for r in rows)
        undone = len(rows) - applied
        return {
            "can_undo": bool(applied), "can_redo": bool(undone),
            "undo_count": applied, "redo_count": undone,
            "redo_pending_turn_ids": self.redo_pending_turn_ids() if undone else [],
            "snapshots": [dict(r) for r in rows],
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
        capture_snapshot(self.data_dir, destination)

    def _restore_snapshot(self, source: Path) -> None:
        # One target covers the database and JSON files until installation completes.
        with self._connect() as conn:
            conn.execute("UPDATE control_state SET restore_target=? WHERE id=1", (self._stored_snapshot_path(source),))
        try:
            install_snapshot(self.data_dir, source)
        except (SnapshotError, RuntimeError):
            # Validation fails before any live file is replaced.
            with self._connect() as conn:
                conn.execute("UPDATE control_state SET restore_target=NULL WHERE id=1")
            raise
        with self._connect() as conn:
            conn.execute("UPDATE control_state SET restore_target=NULL, updated_at=? WHERE id=1", (_now(),))

    def _discard_redo_locked(self, conn) -> list[sqlite3.Row]:
        rows = conn.execute(
            "SELECT path, redo_path FROM turn_snapshots WHERE status='undone'"
        ).fetchall()
        conn.execute("DELETE FROM turn_snapshots WHERE status='undone'")
        return rows

    def _trim_locked(self, conn) -> list[sqlite3.Row]:
        rows = conn.execute(
            """
            SELECT sequence, path, redo_path FROM turn_snapshots
            WHERE status='applied' ORDER BY sequence DESC
            """
        ).fetchall()
        trimmed = rows[self.max_undo :]
        conn.executemany("DELETE FROM turn_snapshots WHERE sequence=?", [(row["sequence"],) for row in trimmed])
        return trimmed

    def _trim_transactions_locked(self, conn) -> None:
        # Keep transactions for the same window as the undo snapshot stack
        # (``max_undo`` turns, anchor on the newest row's turn_index so pending
        # drafts targeting a future turn stay covered). Snapshot-referenced groups
        # remain available even after a discarded future draft advanced the index.
        row = conn.execute(
            "SELECT MAX(turn_index) FROM turn_transactions"
        ).fetchone()
        if row is None or row[0] is None:
            return
        threshold = int(row[0]) - self.max_undo
        conn.execute(
            """DELETE FROM turn_transactions WHERE turn_index <= ?
                AND COALESCE(group_id, turn_id) NOT IN (
                    SELECT COALESCE(t.group_id, t.turn_id)
                    FROM turn_transactions t
                    JOIN turn_snapshots s ON s.turn_id=t.turn_id
                )""",
            (threshold,),
        )

    @staticmethod
    def _snapshot_data(row, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "turn_id": row["turn_id"],
            "turn_index": row["turn_index"],
            "snapshot_id": row["snapshot_id"],
        }
