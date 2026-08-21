from __future__ import annotations

import json
import os
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


# Whitelisted files that travel with Undo/Redo.
_SNAPSHOT_WHITELIST = {"campaign.db", "core_memory.json", "initialization.json"}
# Legacy snapshot contents that must be migrated.
_LEGACY_SNAPSHOT_DB_FILES = {
    "current_state.db",
    "continuity.db",
    "short_term.db",
    "scene_summaries.db",
    "confirmed_history.db",
    "npc_profiles.db",
    "director_events.db",
    "director_npcs.db",
    "director_log.db",
    "world_settings.db",
}
_SNAPSHOT_LEGACY_MIGRATION_FILES = _LEGACY_SNAPSHOT_DB_FILES | {"settings.json"}


def ensure_control_database(paths) -> str:
    """Ensure the control database exists at ``paths.control_db``.

    Cases:
    - only ``control.db``: validate and upgrade schema in place.
    - only ``turn_history.db``: checkpoint WAL, then atomically rename to
      ``control.db`` and upgrade schema.
    - neither: create a fresh ``control.db`` via schema bootstrap.
    - both: fail closed.

    An existing control database must already contain the two base control
    tables; an empty or unrelated file is rejected instead of being silently
    replaced by a fresh schema.
    """
    control = Path(paths.control_db)
    legacy = Path(paths.turn_history_db) if hasattr(paths, "turn_history_db") else paths.data_dir / ".runtime" / "turn_history.db"
    has_control = control.is_file()
    has_legacy = legacy.is_file()
    if has_control and has_legacy:
        raise RuntimeError(
            f"Both control databases exist: {control} and {legacy}; "
            "refusing to choose silently. Remove one."
        )
    if has_control:
        _validate_control_database(control)
        _ensure_control_schema(control)
        return "validated"
    if has_legacy:
        # A legacy control DB is still a control DB: it must already contain the
        # two base control tables.  An empty/illegal legacy file must fail closed
        # instead of being renamed and silently upgraded into a fresh schema.
        _validate_control_database(legacy)
        _checkpoint_legacy_control(legacy)
        # The issue's migration contract is an atomic rename.  The legacy file
        # becomes control.db and any legacy WAL/SHM sidecars are removed only
        # after the main file is in place.  If any cleanup fails we fail closed:
        # a successful return must never leave both files behind.
        control.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.replace(legacy, control)
        except OSError:
            raise
        try:
            _ensure_control_schema(control)
        except BaseException:
            # Do not claim success if schema upgrade fails; leave the renamed
            # control.db in place and report the failure so the user can act.
            raise
        for suffix in ("-wal", "-shm", "-journal"):
            legacy_side = Path(str(legacy) + suffix)
            if legacy_side.exists():
                try:
                    legacy_side.unlink(missing_ok=True)
                except OSError as exc:
                    raise RuntimeError(
                        f"control migration succeeded but failed to remove "
                        f"{legacy_side}: {exc}"
                    ) from exc
        return "migrated"
    # neither exists
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


def _checkpoint_legacy_control(legacy: Path) -> None:
    """Checkpoint and close a legacy WAL before atomic rename.

    ``os.replace`` moves the main database file only; uncheckpointed WAL
    content would be stranded under the legacy name.  Opening with
    ``connect_sqlite`` also persists ``journal_mode=WAL`` in the database
    header, which is fine after the rename.
    """
    conn = connect_sqlite(legacy)
    try:
        # A plain commit after opening performs the checkpoint for a WAL-mode
        # database.  Run a full explicit checkpoint and verify the WAL is gone
        # before renaming.
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        conn.commit()
    finally:
        conn.close()
    # After close SQLite may remove the WAL sidecar automatically.  If it still
    # exists after a committed TRUNCATE checkpoint, the checkpoint did not fully
    # absorb the WAL (e.g. another connection still holds it), so fail closed.
    if Path(str(legacy) + "-wal").exists():
        raise RuntimeError(
            f"failed to checkpoint legacy control database WAL: {legacy}"
        )


def ensure_control_database_by_dir(data_dir: Path) -> str:
    """Convenience for direct data_dir callers (tests/tools)."""
    from .paths import CampaignPaths

    return ensure_control_database(CampaignPaths.from_data_dir(data_dir))


def _ensure_control_schema(db_path: Path) -> None:
    """Create or upgrade the control database schema in place."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect_sqlite(db_path)
    try:
        # Base tables (create if not exists, without new columns initially)
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
        # Add audit columns
        ensure_column(conn, "turn_transactions", "state_changes_json", "TEXT")
        ensure_column(conn, "turn_transactions", "state_before_json", "TEXT")
        ensure_column(conn, "turn_transactions", "state_after_json", "TEXT")
        ensure_column(conn, "turn_transactions", "state_warnings_json", "TEXT")
        # Phase 3 columns
        ensure_column(conn, "turn_transactions", "commit_id", "TEXT")
        ensure_column(conn, "turn_transactions", "revision_id", "TEXT")
        ensure_column(conn, "turn_snapshots", "revision_id", "TEXT")

        # control_state singleton
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS control_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                current_revision_id TEXT NOT NULL,
                connection_epoch INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            """
        )
        # indexes for non-null commit/revision
        conn.executescript(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_transactions_commit_id
                ON turn_transactions(commit_id) WHERE commit_id IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_turn_transactions_revision_id
                ON turn_transactions(revision_id) WHERE revision_id IS NOT NULL;
            """
        )
        # Ensure singleton row exists
        row = conn.execute("SELECT current_revision_id, connection_epoch FROM control_state WHERE id=1").fetchone()
        if row is None:
            initial_rev = uuid.uuid4().hex
            conn.execute(
                "INSERT INTO control_state(id, current_revision_id, connection_epoch, updated_at) VALUES (1, ?, 0, ?)",
                (initial_rev, _now()),
            )
        conn.commit()

        # Backfill revision chain if needed
        _backfill_revisions(conn)

        conn.commit()
    finally:
        conn.close()


def _backfill_revisions(conn: sqlite3.Connection) -> None:
    """Backfill a linear revision chain for snapshots that lack revision_id.

    The chain is linear: snapshot i pre = revisions[i], txn i post = revisions[i+1].
    ``control_state.current_revision_id`` is set to revisions[applied_count]
    (post of last applied, or pre of first undone).
    """
    # Fetch chain snapshots (applied/undone) ordered
    chain = conn.execute(
        "SELECT sequence, snapshot_id, turn_id, status, revision_id FROM turn_snapshots WHERE status IN ('applied','undone') ORDER BY sequence"
    ).fetchall()
    # Also check provisional missing revision
    prov_missing = conn.execute(
        "SELECT COUNT(*) FROM turn_snapshots WHERE status='provisional' AND revision_id IS NULL"
    ).fetchone()[0] > 0

    if not chain:
        if prov_missing:
            cur = conn.execute("SELECT current_revision_id FROM control_state WHERE id=1").fetchone()
            cur_rev = cur["current_revision_id"] if cur and cur["current_revision_id"] else uuid.uuid4().hex
            for row in conn.execute(
                "SELECT snapshot_id FROM turn_snapshots WHERE status='provisional' AND revision_id IS NULL"
            ).fetchall():
                conn.execute(
                    "UPDATE turn_snapshots SET revision_id=? WHERE snapshot_id=?",
                    (cur_rev, row["snapshot_id"]),
                )
        return

    # Map txn by snapshot
    txn_by_snapshot: dict[str, Any] = {}
    for s in chain:
        txn = conn.execute(
            "SELECT turn_id, revision_id, commit_id FROM turn_transactions WHERE pre_snapshot_id=?",
            (s["snapshot_id"],),
        ).fetchone()
        txn_by_snapshot[s["snapshot_id"]] = txn

    needs_snapshot = any(s["revision_id"] is None for s in chain)
    needs_txn = any(
        txn is None or txn["revision_id"] is None or txn["commit_id"] is None
        for txn in txn_by_snapshot.values()
    )

    if not needs_snapshot and not needs_txn and not prov_missing:
        return

    # Full rebuild case: all chain snapshots missing revision (initial migration)
    all_missing = all(s["revision_id"] is None for s in chain)
    if all_missing:
        revisions = [uuid.uuid4().hex for _ in range(len(chain) + 1)]
        for idx, snap in enumerate(chain):
            pre = revisions[idx]
            post = revisions[idx + 1]
            conn.execute(
                "UPDATE turn_snapshots SET revision_id=? WHERE snapshot_id=?",
                (pre, snap["snapshot_id"]),
            )
            txn = txn_by_snapshot[snap["snapshot_id"]]
            if txn is not None:
                # txn may have been fetched before updates, but we update directly
                conn.execute(
                    "UPDATE turn_transactions SET revision_id=?, commit_id=COALESCE(commit_id, ?) WHERE turn_id=?",
                    (post, uuid.uuid4().hex, txn["turn_id"]),
                )
                # Ensure commit_id not null (COALESCE keeps existing)
                # If txn had no commit, the above sets one; if it had, keeps it
                # But we need to handle case where txn had revision None but commit existed: the above keeps commit
                # For safety, ensure commit_id is set
                cur = conn.execute(
                    "SELECT commit_id FROM turn_transactions WHERE turn_id=?", (txn["turn_id"],)
                ).fetchone()
                if cur and cur["commit_id"] is None:
                    conn.execute(
                        "UPDATE turn_transactions SET commit_id=? WHERE turn_id=?",
                        (uuid.uuid4().hex, txn["turn_id"]),
                    )
        applied_count = sum(1 for s in chain if s["status"] == "applied")
        current_rev = revisions[applied_count]
        conn.execute(
            "UPDATE control_state SET current_revision_id=?, updated_at=? WHERE id=1",
            (current_rev, _now()),
        )
    else:
        # Incremental fill for partial chain (e.g., txn missing, or some snapshots missing)
        # Walk chain in order, filling missing pieces while preserving existing pre values
        # Determine starting current_rev (pre of first snapshot)
        first = chain[0]
        if first["revision_id"] is not None:
            current_rev: str = first["revision_id"]
        else:
            # first missing, generate or use control_state's current before chain
            # For partial where first is missing but later has, we generate for first
            current_rev = uuid.uuid4().hex
            conn.execute(
                "UPDATE turn_snapshots SET revision_id=? WHERE snapshot_id=?",
                (current_rev, first["snapshot_id"]),
            )
        # Now walk
        for idx, snap in enumerate(chain):
            # Ensure snapshot has pre; if missing, assign current_rev (post of previous)
            if snap["revision_id"] is None:
                # snap is not first (first already handled), assign current_rev
                if idx != 0:
                    conn.execute(
                        "UPDATE turn_snapshots SET revision_id=? WHERE snapshot_id=?",
                        (current_rev, snap["snapshot_id"]),
                    )
                    pre = current_rev
                else:
                    pre = current_rev
            else:
                pre = snap["revision_id"]
                # keep current_rev aligned to this pre for next post calculation
                current_rev = pre

            txn = txn_by_snapshot[snap["snapshot_id"]]
            if txn is not None:
                # Refresh txn after potential snapshot update
                txn_row = conn.execute(
                    "SELECT revision_id, commit_id FROM turn_transactions WHERE turn_id=?",
                    (txn["turn_id"],),
                ).fetchone()
                txn_rev = txn_row["revision_id"]
                txn_commit = txn_row["commit_id"]
                # Determine post: if txn already has revision, keep it; else derive from next snapshot's pre or generate
                if txn_rev is None:
                    next_pre = None
                    if idx + 1 < len(chain):
                        nxt = chain[idx + 1]
                        # Need fresh read as chain list may have been updated
                        nxt_row = conn.execute(
                            "SELECT revision_id FROM turn_snapshots WHERE snapshot_id=?",
                            (nxt["snapshot_id"],),
                        ).fetchone()
                        next_pre = nxt_row["revision_id"] if nxt_row else None
                    post = next_pre if next_pre is not None else uuid.uuid4().hex
                    conn.execute(
                        "UPDATE turn_transactions SET revision_id=? WHERE turn_id=?",
                        (post, txn["turn_id"]),
                    )
                    txn_rev = post
                else:
                    post = txn_rev
                if txn_commit is None:
                    conn.execute(
                        "UPDATE turn_transactions SET commit_id=? WHERE turn_id=?",
                        (uuid.uuid4().hex, txn["turn_id"]),
                    )
                current_rev = post
            else:
                # No txn, keep current_rev as pre (no post)
                pass

        # After walk, set control_state to boundary (post of last applied)
        applied = [s for s in chain if s["status"] == "applied"]
        if applied:
            last = applied[-1]
            txn_row = conn.execute(
                "SELECT revision_id FROM turn_transactions WHERE pre_snapshot_id=?",
                (last["snapshot_id"],),
            ).fetchone()
            boundary = txn_row["revision_id"] if txn_row and txn_row["revision_id"] else current_rev
        else:
            # all undone: boundary is pre of first snapshot
            first_row = conn.execute(
                "SELECT revision_id FROM turn_snapshots WHERE snapshot_id=?",
                (chain[0]["snapshot_id"],),
            ).fetchone()
            boundary = first_row["revision_id"] if first_row else current_rev
        conn.execute(
            "UPDATE control_state SET current_revision_id=?, updated_at=? WHERE id=1",
            (boundary, _now()),
        )

    # Handle provisional missing
    prov_rows = conn.execute(
        "SELECT snapshot_id FROM turn_snapshots WHERE status='provisional' AND revision_id IS NULL"
    ).fetchall()
    if prov_rows:
        cur = conn.execute("SELECT current_revision_id FROM control_state WHERE id=1").fetchone()
        cur_rev = cur["current_revision_id"] if cur else uuid.uuid4().hex
        for r in prov_rows:
            conn.execute(
                "UPDATE turn_snapshots SET revision_id=? WHERE snapshot_id=?",
                (cur_rev, r["snapshot_id"]),
            )


class CampaignHistory:
    """Durable pending-turn journal plus a bounded undo/redo snapshot stack."""

    def __init__(self, data_dir: Path, *, max_undo: int = 10) -> None:
        self.data_dir = Path(data_dir)
        self.runtime_dir = self.data_dir / ".runtime"
        self.snapshots_dir = self.data_dir / ".snapshots"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        # Delegate to the shared migration entry so tests/tools and runtime share one path
        try:
            ensure_control_database_by_dir(self.data_dir)
        except RuntimeError:
            raise
        except Exception as exc:
            # Any migration failure should be surfaced as RuntimeError for fail-closed
            raise RuntimeError(str(exc)) from exc
        self.db_path = self.runtime_dir / "control.db"
        self.max_undo = max_undo
        self._lock = threading.RLock()

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

    # --- control-state helpers ---
    def _get_control_state(self, conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute("SELECT current_revision_id, connection_epoch FROM control_state WHERE id=1").fetchone()
        if row is None:
            raise RuntimeError("control_state missing")
        return {"current_revision_id": row["current_revision_id"], "connection_epoch": int(row["connection_epoch"])}

    def get_control_state(self) -> dict[str, Any]:
        with self._connect() as conn:
            return self._get_control_state(conn)

    @property
    def current_revision_id(self) -> str | None:
        try:
            return self.get_control_state()["current_revision_id"]
        except Exception:
            return None

    @property
    def connection_epoch(self) -> int:
        try:
            return int(self.get_control_state()["connection_epoch"])
        except Exception:
            return 0

    def _increment_epoch_and_set_revision(self, conn: sqlite3.Connection, revision_id: str) -> None:
        row = conn.execute("SELECT connection_epoch FROM control_state WHERE id=1").fetchone()
        epoch = int(row["connection_epoch"]) if row is not None else 0
        conn.execute(
            "UPDATE control_state SET current_revision_id=?, connection_epoch=?, updated_at=? WHERE id=1",
            (revision_id, epoch + 1, _now()),
        )

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
                    "SELECT status, redo_path, revision_id FROM turn_snapshots WHERE snapshot_id=?",
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
                # capture current revision for snapshot
                cur = conn.execute("SELECT current_revision_id FROM control_state WHERE id=1").fetchone()
                cur_rev = cur["current_revision_id"] if cur is not None else uuid.uuid4().hex
                self._capture_snapshot(path)
                conn.execute(
                    """
                    INSERT INTO turn_snapshots (
                        snapshot_id, turn_id, turn_index, path, status, revision_id, created_at
                    ) VALUES (?, ?, ?, ?, 'provisional', ?, ?)
                    """,
                    (
                        snapshot_id,
                        turn_id,
                        row["turn_index"],
                        self._stored_snapshot_path(path),
                        cur_rev,
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
        # Phase 4 durable offline: confirming -> restoring -> failed
        # Keep backwards compatible name; runtime uses fail_confirmation_offline wrapper
        return self.fail_confirmation_offline(turn_id, error)

    def fail_confirmation_offline(self, turn_id: str, error: BaseException) -> None:
        with self._lock:
            # 1. Fetch provisional snapshot + snapshot_id for durable intent
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT s.path, s.revision_id, s.snapshot_id, s.status as snap_status FROM turn_transactions t
                    JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                    WHERE t.turn_id=? AND s.status IN ('provisional', 'restoring')
                    """,
                    (turn_id,),
                ).fetchone()
                txn_row = conn.execute(
                    "SELECT status, pre_snapshot_id FROM turn_transactions WHERE turn_id=?", (turn_id,)
                ).fetchone()
                if txn_row is None:
                    raise RuntimeError(f"Turn transaction not found: {turn_id}")
                # Already failed is idempotent
                if txn_row["status"] == "failed":
                    return
                snapshot_path: Path | None = None
                snapshot_rev: str | None = None
                snapshot_id: str | None = None
                if row is not None:
                    snapshot_path = self._snapshot_path(row["path"])
                    snapshot_rev = row["revision_id"]
                    snapshot_id = row["snapshot_id"]
                # Durable intent: confirming -> restoring for both txn and snapshot
                if txn_row["status"] == "confirming":
                    conn.execute(
                        "UPDATE turn_transactions SET status='restoring', updated_at=? WHERE turn_id=?",
                        (_now(), turn_id),
                    )
                    if snapshot_id is not None:
                        conn.execute(
                            "UPDATE turn_snapshots SET status='restoring' WHERE snapshot_id=?",
                            (snapshot_id,),
                        )
                    conn.commit()
                elif txn_row["status"] == "restoring":
                    # Ensure snapshot also in restoring (idempotent)
                    if snapshot_id is not None and row is not None and row["snap_status"] == "provisional":
                        conn.execute(
                            "UPDATE turn_snapshots SET status='restoring' WHERE snapshot_id=?",
                            (snapshot_id,),
                        )
                        conn.commit()
                else:
                    # e.g. awaiting_confirm retry: no restore needed
                    pass
            # 2. Offline install (outside control transaction, session UoW must be closed by caller)
            if snapshot_path is not None:
                self._restore_snapshot(snapshot_path)
            # 3. Finalize: restoring -> failed + epoch bump.  The epoch must
            # increase exactly once per completed durable restore, regardless
            # of whether the current revision already equals the pre-revision.
            with self._connect() as conn:
                cur = conn.execute("SELECT status FROM turn_transactions WHERE turn_id=?", (turn_id,)).fetchone()
                if cur is None or cur["status"] == "failed":
                    return
                if snapshot_path is not None and snapshot_rev is not None:
                    self._increment_epoch_and_set_revision(conn, snapshot_rev)
                # Restore snapshot status to provisional before marking failed? Keep as restoring until finalize
                if snapshot_id is not None:
                    conn.execute(
                        "UPDATE turn_snapshots SET status='provisional' WHERE snapshot_id=? AND status='restoring'",
                        (snapshot_id,),
                    )
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
                "SELECT pre_snapshot_id, commit_id, revision_id FROM turn_transactions WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"Turn transaction not found: {turn_id}")
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
            # idempotent commit/revision generation
            commit_id = row["commit_id"]
            post_rev = row["revision_id"]
            if commit_id is None or post_rev is None:
                commit_id = commit_id or uuid.uuid4().hex
                post_rev = post_rev or uuid.uuid4().hex
                conn.execute(
                    "UPDATE turn_transactions SET commit_id=?, revision_id=?, status='completed', updated_at=? WHERE turn_id=?",
                    (commit_id, post_rev, _now(), turn_id),
                )
            else:
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
            # update current_revision to post_rev; this is ordinary commit, not a restore, so epoch stays
            conn.execute(
                "UPDATE control_state SET current_revision_id=?, updated_at=? WHERE id=1",
                (post_rev, _now()),
            )
            conn.commit()
            for snapshot in abandoned_snapshots:
                shutil.rmtree(self._snapshot_path(snapshot["path"]), ignore_errors=True)
            self._trim_locked(conn)
            self._trim_transactions_locked(conn)

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
        # Phase 4: recover durable intents idempotently (epoch only once in finalize)
        recovered = False
        with self._lock:
            # 1. Undoing
            undo_row = None
            undo_path: Path | None = None
            undo_rev: str | None = None
            undo_seq: int | None = None
            with self._connect() as conn:
                r = conn.execute("SELECT * FROM turn_snapshots WHERE status='undoing' ORDER BY sequence DESC LIMIT 1").fetchone()
                if r is not None:
                    undo_row = dict(r)
                    undo_path = self._snapshot_path(r["path"])
                    undo_rev = r["revision_id"]
                    undo_seq = int(r["sequence"])
            if undo_path is not None and undo_row is not None and undo_seq is not None:
                self._restore_snapshot(undo_path)
                with self._connect() as conn:
                    cur = conn.execute("SELECT status FROM turn_snapshots WHERE sequence=?", (undo_seq,)).fetchone()
                    if cur is not None and cur["status"] == "undoing":
                        conn.execute("UPDATE turn_snapshots SET status='undone' WHERE sequence=?", (undo_seq,))
                        if undo_rev is not None:
                            self._increment_epoch_and_set_revision(conn, undo_rev)
                        txn = conn.execute("SELECT group_id FROM turn_transactions WHERE turn_id=?", (undo_row["turn_id"],)).fetchone()
                        if txn is not None:
                            if txn["group_id"]:
                                conn.execute(
                                    "UPDATE turn_transactions SET status='awaiting_confirm', error=NULL, updated_at=? WHERE group_id=? AND status IN ('completed','discarded')",
                                    (_now(), txn["group_id"]),
                                )
                            else:
                                conn.execute(
                                    "UPDATE turn_transactions SET status='awaiting_confirm', error=NULL, updated_at=? WHERE turn_id=?",
                                    (_now(), undo_row["turn_id"]),
                                )
                        conn.commit()
                recovered = True

            # 2. Redoing
            redo_row = None
            redo_path: Path | None = None
            redo_seq: int | None = None
            redo_post: dict | None = None
            with self._connect() as conn:
                r = conn.execute("SELECT * FROM turn_snapshots WHERE status='redoing' ORDER BY sequence ASC LIMIT 1").fetchone()
                if r is not None and r["redo_path"]:
                    redo_row = dict(r)
                    redo_path = self._snapshot_path(r["redo_path"])
                    redo_seq = int(r["sequence"])
                    pr = conn.execute("SELECT revision_id, group_id FROM turn_transactions WHERE turn_id=?", (r["turn_id"],)).fetchone()
                    redo_post = dict(pr) if pr is not None else None
            if redo_path is not None and redo_row is not None and redo_seq is not None:
                self._restore_snapshot(redo_path)
                with self._connect() as conn:
                    cur = conn.execute("SELECT status FROM turn_snapshots WHERE sequence=?", (redo_seq,)).fetchone()
                    if cur is not None and cur["status"] == "redoing":
                        conn.execute("UPDATE turn_snapshots SET status='applied' WHERE sequence=?", (redo_seq,))
                        post_rev = redo_post["revision_id"] if redo_post and redo_post.get("revision_id") else None
                        if post_rev is not None:
                            self._increment_epoch_and_set_revision(conn, post_rev)
                        else:
                            post_rev = uuid.uuid4().hex
                            conn.execute("UPDATE turn_transactions SET revision_id=? WHERE turn_id=?", (post_rev, redo_row["turn_id"]))
                            self._increment_epoch_and_set_revision(conn, post_rev)
                        if redo_post and redo_post.get("group_id"):
                            conn.execute("UPDATE turn_transactions SET status='completed', error=NULL, updated_at=? WHERE turn_id=?", (_now(), redo_row["turn_id"]))
                            conn.execute(
                                "UPDATE turn_transactions SET status='discarded', updated_at=? WHERE group_id=? AND turn_id<>? AND status='awaiting_confirm'",
                                (_now(), redo_post["group_id"], redo_row["turn_id"]),
                            )
                        else:
                            conn.execute("UPDATE turn_transactions SET status='completed', error=NULL, updated_at=? WHERE turn_id=?", (_now(), redo_row["turn_id"]))
                        conn.commit()
                shutil.rmtree(redo_path, ignore_errors=True)
                with self._connect() as conn:
                    conn.execute("UPDATE turn_snapshots SET redo_path=NULL WHERE sequence=? AND status='applied'", (redo_seq,))
                    conn.commit()
                recovered = True

            # 3. Restoring / Confirming (legacy confirming also treated as restoring)
            crow = None
            cr_path: Path | None = None
            cr_rev: str | None = None
            cr_turn: str | None = None
            with self._connect() as conn:
                r = conn.execute(
                    """
                    SELECT t.turn_id, t.status as txn_status, s.path, s.revision_id as snapshot_revision FROM turn_transactions t
                    JOIN turn_snapshots s ON s.snapshot_id=t.pre_snapshot_id
                    WHERE t.status IN ('restoring','confirming')
                    ORDER BY t.updated_at DESC LIMIT 1
                    """
                ).fetchone()
                if r is not None:
                    crow = dict(r)
                    cr_path = self._snapshot_path(r["path"])
                    cr_rev = r["snapshot_revision"]
                    cr_turn = r["turn_id"]
            if cr_path is not None and cr_turn is not None:
                self._restore_snapshot(cr_path)
                with self._connect() as conn:
                    cur = conn.execute("SELECT status, pre_snapshot_id FROM turn_transactions WHERE turn_id=?", (cr_turn,)).fetchone()
                    if cur is not None and cur["status"] in ("restoring", "confirming"):
                        if cr_rev is not None:
                            self._increment_epoch_and_set_revision(conn, cr_rev)
                        # Reset snapshot restoring -> provisional
                        if cur["pre_snapshot_id"]:
                            conn.execute(
                                "UPDATE turn_snapshots SET status='provisional' WHERE snapshot_id=? AND status='restoring'",
                                (cur["pre_snapshot_id"],),
                            )
                        conn.execute(
                            "UPDATE turn_transactions SET status='failed', error='Recovered interrupted confirmation from pre-turn snapshot', updated_at=? WHERE turn_id=?",
                            (_now(), cr_turn),
                        )
                        conn.commit()
                recovered = True

            # 4. Crash after redo directory delete but before redo_path clear:
            # an applied snapshot with a non-null redo_path is a leftover that
            # should simply have its path cleared (the directory is already gone).
            with self._connect() as conn:
                stale_redo = conn.execute(
                    "SELECT sequence, redo_path FROM turn_snapshots WHERE status='applied' AND redo_path IS NOT NULL ORDER BY sequence"
                ).fetchall()
                for row in stale_redo:
                    path = self._snapshot_path(row["redo_path"])
                    if path.exists():
                        shutil.rmtree(path, ignore_errors=True)
                    conn.execute("UPDATE turn_snapshots SET redo_path=NULL WHERE sequence=?", (row["sequence"],))
                if stale_redo:
                    conn.commit()
                    recovered = True
            return recovered

    def undo(self) -> dict[str, Any]:
        # Phase 4 durable offline: applied -> undoing -> undone
        with self._lock:
            # 1. Select target (applied or already undoing for resume)
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM turn_snapshots WHERE status IN ('applied','undoing')
                    ORDER BY sequence DESC LIMIT 1
                    """
                ).fetchone()
                # Prefer applied; if latest is undoing resume that one, but if there is separate applied newer? Order ensures we handle correctly
                # If the latest status is undone, the above won't find applied post-undo, but callers expect next applied.
                # So fallback to applied only when no undoing found
                if row is not None and row["status"] == "undoing":
                    pass
                else:
                    # Re-query strictly applied if the undoing row was not the applied candidate or no row
                    row2 = conn.execute(
                        """
                        SELECT * FROM turn_snapshots WHERE status='applied'
                        ORDER BY sequence DESC LIMIT 1
                        """
                    ).fetchone()
                    if row2 is not None:
                        row = row2
                    elif row is None or row["status"] != "undoing":
                        raise ValueError("No confirmed turn is available to undo")
                # Capture redo snapshot before intent if not already undoing
                snapshot_seq = row["sequence"]
                snapshot_id = row["snapshot_id"]
                snapshot_path = self._snapshot_path(row["path"])
                snapshot_rev = row["revision_id"]
                redo_path: Path | None = None
                redo_stored: str | None = None
                if row["status"] == "applied":
                    redo_path = self.snapshots_dir / f"redo-{snapshot_id}"
                    self._capture_snapshot(redo_path)
                    redo_stored = self._stored_snapshot_path(redo_path)
                    conn.execute(
                        "UPDATE turn_snapshots SET status='undoing', redo_path=? WHERE sequence=?",
                        (redo_stored, snapshot_seq),
                    )
                    conn.commit()
                else:
                    # Resume: redo already captured; if missing, recapture (crash before capture)
                    redo_stored = row["redo_path"]
                    if redo_stored:
                        redo_path = self._snapshot_path(redo_stored)
                        if not redo_path.exists():
                            # Recapture missing redo (idempotent)
                            redo_path = self.snapshots_dir / f"redo-{snapshot_id}"
                            self._capture_snapshot(redo_path)
                            redo_stored = self._stored_snapshot_path(redo_path)
                            conn.execute(
                                "UPDATE turn_snapshots SET redo_path=? WHERE sequence=?",
                                (redo_stored, snapshot_seq),
                            )
                            conn.commit()
                    else:
                        redo_path = self.snapshots_dir / f"redo-{snapshot_id}"
                        self._capture_snapshot(redo_path)
                        redo_stored = self._stored_snapshot_path(redo_path)
                        conn.execute(
                            "UPDATE turn_snapshots SET redo_path=? WHERE sequence=?",
                            (redo_stored, snapshot_seq),
                        )
                        conn.commit()
                        redo_path = self._snapshot_path(redo_stored)
            # 2. Offline install pre snapshot (outside control txn)
            self._restore_snapshot(snapshot_path)
            # 3. Finalize in control txn
            with self._connect() as conn:
                cur = conn.execute("SELECT status, revision_id FROM turn_snapshots WHERE sequence=?", (snapshot_seq,)).fetchone()
                if cur is None:
                    raise RuntimeError("Undo snapshot disappeared")
                if cur["status"] == "undone":
                    # Already finalized (idempotent retry)
                    pass
                else:
                    conn.execute(
                        "UPDATE turn_snapshots SET status='undone' WHERE sequence=?",
                        (snapshot_seq,),
                    )
                    if snapshot_rev is not None:
                        self._increment_epoch_and_set_revision(conn, snapshot_rev)
                    transaction = conn.execute(
                        "SELECT group_id FROM turn_transactions WHERE turn_id=?",
                        (row["turn_id"],),
                    ).fetchone()
                    if transaction is None:
                        raise RuntimeError(f"Confirmed turn transaction is missing: {row['turn_id']}")
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
                result["restored_pending_turn_ids"] = self._redo_pending_turn_ids_locked(conn, row["turn_id"])
                return result

    def redo(self) -> dict[str, Any]:
        # Phase 4 durable offline: undone -> redoing -> applied
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT * FROM turn_snapshots WHERE status IN ('undone','redoing')
                    ORDER BY sequence ASC LIMIT 1
                    """
                ).fetchone()
                # Prefer redoing if exists (resume), else earliest undone
                if row is not None and row["status"] == "redoing":
                    pass
                else:
                    row2 = conn.execute(
                        """
                        SELECT * FROM turn_snapshots WHERE status='undone' AND redo_path IS NOT NULL
                        ORDER BY sequence ASC LIMIT 1
                        """
                    ).fetchone()
                    if row2 is not None:
                        row = row2
                    elif row is None or row["status"] != "redoing":
                        raise ValueError("No undone turn is available to redo")
                seq = row["sequence"]
                redo_stored = row["redo_path"]
                if not redo_stored:
                    raise ValueError("No undone turn is available to redo")
                redo_path = self._snapshot_path(redo_stored)
                if row["status"] == "undone":
                    conn.execute("UPDATE turn_snapshots SET status='redoing' WHERE sequence=?", (seq,))
                    conn.commit()
            # Offline install redo snapshot
            self._restore_snapshot(redo_path)
            # Finalize
            with self._connect() as conn:
                cur = conn.execute("SELECT status FROM turn_snapshots WHERE sequence=?", (seq,)).fetchone()
                if cur is not None and cur["status"] == "applied":
                    # Already applied (idempotent), ensure redo file cleaned
                    shutil.rmtree(redo_path, ignore_errors=True)
                    return self._snapshot_data(row, "redone")
                conn.execute("UPDATE turn_snapshots SET status='applied' WHERE sequence=?", (seq,))
                transaction = conn.execute(
                    "SELECT revision_id, group_id FROM turn_transactions WHERE turn_id=?",
                    (row["turn_id"],),
                ).fetchone()
                if transaction is None:
                    raise RuntimeError(f"Undone turn transaction is missing: {row['turn_id']}")
                post_rev = transaction["revision_id"]
                if post_rev is not None:
                    self._increment_epoch_and_set_revision(conn, post_rev)
                else:
                    post_rev = uuid.uuid4().hex
                    conn.execute("UPDATE turn_transactions SET revision_id=? WHERE turn_id=?", (post_rev, row["turn_id"]))
                    self._increment_epoch_and_set_revision(conn, post_rev)
                if transaction["group_id"]:
                    conn.execute(
                        "UPDATE turn_transactions SET status='completed', error=NULL, updated_at=? WHERE turn_id=?",
                        (_now(), row["turn_id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE turn_transactions SET status='completed', error=NULL, updated_at=? WHERE turn_id=?",
                        (_now(), row["turn_id"]),
                    )
                if transaction["group_id"]:
                    conn.execute(
                        """
                        UPDATE turn_transactions
                        SET status='discarded', updated_at=?
                        WHERE group_id=? AND turn_id<>? AND status='awaiting_confirm'
                        """,
                        (_now(), transaction["group_id"], row["turn_id"]),
                    )
                conn.commit()
            # Delete redo file only after control transaction commits, then clear
            # redo_path in a separate durable step.  If the process exits between
            # these steps, startup recovery still sees redo_path and can clean up.
            shutil.rmtree(redo_path, ignore_errors=True)
            with self._connect() as conn:
                conn.execute("UPDATE turn_snapshots SET redo_path=NULL WHERE sequence=? AND status='applied'", (seq,))
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
            # also fetch control state for debug
            try:
                cs = conn.execute("SELECT current_revision_id, connection_epoch FROM control_state WHERE id=1").fetchone()
                cur_rev = cs["current_revision_id"] if cs else None
                epoch = int(cs["connection_epoch"]) if cs else 0
            except Exception:
                cur_rev = None
                epoch = 0
        redo_pending_turn_ids = self.redo_pending_turn_ids() if undone else []
        result = {
            "can_undo": applied > 0,
            "can_redo": undone > 0,
            "undo_count": applied,
            "redo_count": undone,
            "redo_pending_turn_ids": redo_pending_turn_ids,
            "snapshots": [dict(row) for row in rows],
        }
        # expose for debug (player payload keeps existing fields; debug will use these)
        if cur_rev is not None:
            result["current_revision_id"] = cur_rev
            result["connection_epoch"] = epoch
        return result

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
        # Whitelisted snapshot files
        campaign = self.data_dir / "campaign.db"
        if campaign.is_file():
            target = destination / "campaign.db"
            src_conn = sqlite3.connect(str(campaign))
            dst_conn = sqlite3.connect(str(target))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
                src_conn.close()
            files.append("campaign.db")
        for name in ("core_memory.json", "initialization.json"):
            source = self.data_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
                files.append(name)
        (destination / "manifest.json").write_text(
            json.dumps({"created_at": _now(), "files": files}, indent=2),
            encoding="utf-8",
        )

    def _migrate_snapshot_manifest_if_needed(self, snapshot_dir: Path) -> None:
        """Migrate old snapshot manifests in place using the shared archive logic."""
        # If snapshot still contains legacy ten-db files, migrate them to campaign.db first
        has_legacy = any((snapshot_dir / name).is_file() for name in _LEGACY_SNAPSHOT_DB_FILES)
        has_campaign = (snapshot_dir / "campaign.db").is_file()
        if has_legacy:
            # If snapshot still uses legacy DBs, migrate them to campaign.db first
            # (has_campaign may be false or true if partially migrated)
            try:
                from .campaign_migration import ensure_campaign_database
                from .paths import CampaignPaths

                ensure_campaign_database(CampaignPaths.from_data_dir(snapshot_dir))
            except Exception as exc:
                raise RuntimeError(
                    f"snapshot campaign migration failed: {snapshot_dir}: {exc}"
                ) from exc
            # Clean up archive dir that ensure_campaign_database may have created inside snapshot
            legacy_archive = snapshot_dir / ".runtime" / "legacy-db-v1"
            if legacy_archive.exists():
                shutil.rmtree(legacy_archive, ignore_errors=True)
                try:
                    (snapshot_dir / ".runtime").rmdir()
                except OSError:
                    pass
        # Delegate manifest rewriting to the single source of truth in save_archive
        # to keep settings.json/legacy handling consistent. Lazy import avoids cycle.
        try:
            from .save_archive import _rewrite_snapshot_manifest
        except ImportError:
            return
        # _rewrite_snapshot_manifest expects a legacy snapshot; it will raise SaveArchiveError
        # on malformed manifests, which we surface as RuntimeError for fail-closed restore.
        try:
            _rewrite_snapshot_manifest(snapshot_dir)
        except Exception as exc:
            # If manifest is missing or already current, _rewrite_snapshot_manifest raises;
            # for runtime restores we treat missing manifest as explicit failure, but
            # ignore the case where manifest is already clean (no legacy/settings to rewrite).
            # _rewrite_snapshot_manifest raises only on truly invalid manifests, so propagate.
            # To keep idempotence, check if manifest exists and is already clean.
            manifest_path = snapshot_dir / "manifest.json"
            if not manifest_path.is_file():
                raise RuntimeError(f"Snapshot manifest missing: {snapshot_dir}") from exc
            # If manifest exists but _rewrite complained about unexpected file, propagate
            # Otherwise, if it was a no-op (no legacy/settings), it would not have raised
            raise

    def _restore_snapshot(self, source: Path, *, _allow_minimal: bool = False) -> None:
        # First migrate old manifest if needed
        self._migrate_snapshot_manifest_if_needed(source)
        manifest = json.loads((source / "manifest.json").read_text(encoding="utf-8"))
        expected = set(manifest["files"])
        # Validate whitelist; unknown file fails closed
        for name in expected:
            if name not in _SNAPSHOT_WHITELIST:
                raise RuntimeError(f"Snapshot manifest contains unexpected file: {name}")
        # Stage all expected files first and validate every one before touching
        # any live file.  A bad snapshot must never delete current JSON/db state.
        staged: list[tuple[Path, Path, Path]] = []  # (source, dest, tmp)
        try:
            for name in expected:
                snapshot_file = source / name
                destination = self.data_dir / name
                if not snapshot_file.exists():
                    raise RuntimeError(f"Snapshot file missing: {snapshot_file}")
                tmp = self.data_dir / f".{name}.{uuid.uuid4().hex}.tmp"
                if snapshot_file.suffix == ".db":
                    # Copy snapshot db to tmp, validate, fsync
                    shutil.copy2(snapshot_file, tmp)
                    try:
                        from .campaign_migration import validate_campaign_database

                        validate_campaign_database(tmp, allow_minimal=_allow_minimal)
                    except Exception:
                        tmp.unlink(missing_ok=True)
                        raise
                    # fsync tmp file
                    try:
                        with open(tmp, "rb") as h:
                            os.fsync(h.fileno())
                    except Exception:
                        pass
                else:
                    # JSON: copy and validate JSON parse
                    shutil.copy2(snapshot_file, tmp)
                    try:
                        json.loads(tmp.read_text(encoding="utf-8"))
                    except Exception:
                        tmp.unlink(missing_ok=True)
                        raise RuntimeError(f"Snapshot JSON invalid: {snapshot_file}")
                    try:
                        with open(tmp, "rb") as h:
                            os.fsync(h.fileno())
                    except Exception:
                        pass
                staged.append((snapshot_file, destination, tmp))

            # All staged files are valid.  Now it is safe to delete current
            # files that are not in the restored snapshot.
            for current in self.data_dir.glob("*.db"):
                if current.name not in expected:
                    for suffix in ("-wal", "-shm", "-journal"):
                        Path(f"{current}{suffix}").unlink(missing_ok=True)
                    current.unlink(missing_ok=True)
            # For json files, only restore whitelisted; do not touch settings.json or drafts
            for name in ("core_memory.json", "initialization.json"):
                current = self.data_dir / name
                if current.exists() and name not in expected:
                    current.unlink()
            # settings.json is deliberately NOT removed/restored

            # Install all staged files atomically (campaign connections must be closed by caller)
            for _src, dest, tmp in staged:
                if dest.suffix == ".db":
                    for suffix in ("-wal", "-shm", "-journal"):
                        Path(f"{dest}{suffix}").unlink(missing_ok=True)
                os.replace(tmp, dest)
            # Best-effort fsync data_dir (reuse campaign_migration helper)
            try:
                from .campaign_migration import _fsync_dir

                _fsync_dir(self.data_dir)
            except Exception:
                try:
                    fd = os.open(str(self.data_dir), os.O_DIRECTORY)
                    try:
                        os.fsync(fd)
                    finally:
                        os.close(fd)
                except Exception:
                    pass
        finally:
            for _src, _dest, tmp in staged:
                if tmp.exists():
                    tmp.unlink(missing_ok=True)

    @staticmethod
    def _restore_database(source: Path, destination: Path) -> None:
        """Legacy online restore — deprecated, production uses offline staging.

        Phase 4 requires offline ``os.replace`` via ``_restore_snapshot``.
        This method remains only for historical direct calls; new code must
        not use it (marked deprecated).
        """
        import warnings

        warnings.warn(
            "CampaignHistory._restore_database is deprecated; use offline _restore_snapshot",
            DeprecationWarning,
            stacklevel=2,
        )
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

    def _trim_transactions_locked(self, conn) -> None:
        # Keep transactions for the same window as the undo snapshot stack
        # (``max_undo`` turns, anchor on the newest row's turn_index so pending
        # drafts targeting a future turn stay covered). Older completed,
        # discarded, failed and abandoned drafts are unreachable via undo/redo
        # and only accumulate payload bytes.
        row = conn.execute(
            "SELECT MAX(turn_index) FROM turn_transactions"
        ).fetchone()
        if row is None or row[0] is None:
            return
        threshold = int(row[0]) - self.max_undo
        conn.execute(
            "DELETE FROM turn_transactions WHERE turn_index <= ?",
            (threshold,),
        )
        conn.commit()

    @staticmethod
    def _snapshot_data(row, action: str) -> dict[str, Any]:
        return {
            "action": action,
            "turn_id": row["turn_id"],
            "turn_index": row["turn_index"],
            "snapshot_id": row["snapshot_id"],
        }
