from __future__ import annotations

import os
import shutil
import sqlite3
import uuid
from pathlib import Path

from app.domain.confirmed_history.store import ConfirmedHistoryStore
from app.domain.continuity.store import ContinuityStore
from app.domain.current_state.store import CurrentStateStore
from app.domain.director.log_store import RecentDirectorLogStore
from app.domain.director_event.store import DirectorEventStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_summary.store import SceneSummaryStore
from app.domain.short_term_memory.schemas import ShortTermMemoryStore
from app.domain.world_setting.store import WorldSettingStore
from app.runtime.sqlite import SUPPORTED_SCHEMA_VERSIONS, connect_sqlite
from .paths import CampaignPaths


LEGACY_DB_FILES = (
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
)
LEGACY_ARCHIVE_DIR = Path(".runtime") / "legacy-db-v1"

_LEGACY_STORE_FACTORIES = {
    "current_state.db": CurrentStateStore,
    "continuity.db": ContinuityStore,
    "short_term.db": lambda conn: ShortTermMemoryStore(conn, max_items=5),
    "scene_summaries.db": SceneSummaryStore,
    "confirmed_history.db": ConfirmedHistoryStore,
    "npc_profiles.db": NPCProfileStore,
    "director_events.db": DirectorEventStore,
    "director_npcs.db": DirectorNPCStore,
    "director_log.db": RecentDirectorLogStore,
    "world_settings.db": WorldSettingStore,
}

def ensure_campaign_database(paths: CampaignPaths) -> str:
    """Return the outcome: ``"validated"``, ``"migrated"`` or ``"created"``.

    New saves start as an empty ``campaign.db``. Legacy saves (the ten per-file
    databases) are migrated atomically into a single ``campaign.db``; a present,
    valid ``campaign.db`` always wins and legacy files are never re-imported.
    """
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    legacy_files = _legacy_file_paths(paths)
    if paths.campaign_db.exists():
        _validate(paths.campaign_db)
        return "validated"
    if legacy_files:
        _migrate_legacy(paths, legacy_files)
        return "migrated"
    _create_empty(paths)
    return "created"


def _legacy_file_paths(paths: CampaignPaths) -> dict[str, Path]:
    return {
        name: paths.data_dir / name
        for name in LEGACY_DB_FILES
        if (paths.data_dir / name).is_file()
    }


def _open_legacy_stores(paths: CampaignPaths) -> None:
    """Run in-code schema migrations against legacy files before copying."""
    for name in LEGACY_DB_FILES:
        path = paths.data_dir / name
        if not path.is_file():
            continue
        conn = connect_sqlite(path)
        try:
            _LEGACY_STORE_FACTORIES[name](conn)
            conn.commit()
        finally:
            conn.close()


def _build_campaign_schema(conn: sqlite3.Connection) -> None:
    CurrentStateStore(conn)
    ContinuityStore(conn)
    ShortTermMemoryStore(conn, max_items=5)
    SceneSummaryStore(conn)
    ConfirmedHistoryStore(conn)
    NPCProfileStore(conn)
    DirectorEventStore(conn)
    DirectorNPCStore(conn)
    RecentDirectorLogStore(conn)
    WorldSettingStore(conn)
    conn.commit()


def _copy_business_tables(dst: sqlite3.Connection, legacy_path: Path) -> None:
    """Copy business tables from one legacy database, skipping bookkeeping."""
    dst.execute("ATTACH DATABASE ? AS legacy", (str(legacy_path),))
    try:
        legacy_tables = {
            row["name"]
            for row in dst.execute(
                """
                SELECT name FROM legacy.sqlite_master
                WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
                """
            ).fetchall()
        }
        main_tables = {
            row["name"]
            for row in dst.execute(
                "SELECT name FROM main.sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        for table in sorted(legacy_tables & main_tables):
            if table == "schema_versions" or _is_fts_table(table):
                continue
            src_count = int(
                dst.execute(f'SELECT COUNT(*) FROM legacy."{table}"').fetchone()[0]
            )
            dst.execute(f'INSERT INTO main."{table}" SELECT * FROM legacy."{table}"')
            dst_count = int(
                dst.execute(f'SELECT COUNT(*) FROM main."{table}"').fetchone()[0]
            )
            if src_count != dst_count:
                raise RuntimeError(
                    f"row count mismatch while migrating {legacy_path.name}:{table} "
                    f"({src_count} != {dst_count})"
                )
        dst.commit()
    finally:
        dst.execute("DETACH DATABASE legacy")


def _is_fts_table(name: str) -> bool:
    return "_fts" in name or name in {"history_documents_fts"}


def _migrate_legacy(paths: CampaignPaths, legacy_files: dict[str, Path]) -> None:
    _open_legacy_stores(paths)
    temporary = paths.data_dir / f"campaign.db.{uuid.uuid4().hex}.tmp"
    conn = None
    try:
        conn = connect_sqlite(temporary)
        _build_campaign_schema(conn)
        conn.execute("PRAGMA foreign_keys = OFF")
        for name in LEGACY_DB_FILES:
            path = legacy_files.get(name)
            if path is not None:
                _copy_business_tables(conn, path)
        conn.execute("PRAGMA foreign_keys = ON")
        _rebuild_fts(conn)
        _validate(temporary)
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.commit()
        _fsync_file(temporary)
    except BaseException:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if conn is not None:
            conn.close()
    try:
        os.replace(temporary, paths.campaign_db)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    # If archiving fails after the new campaign.db is already installed, do not
    # report migration failure. The next startup validates the installed db and
    # legacy files are still harmless leftovers; this keeps retries idempotent.
    try:
        _archive_legacy(paths, legacy_files)
    except OSError:
        pass


def _create_empty(paths: CampaignPaths) -> None:
    temporary = paths.data_dir / f"campaign.db.{uuid.uuid4().hex}.tmp"
    conn = None
    try:
        conn = connect_sqlite(temporary)
        _build_campaign_schema(conn)
        _validate(temporary)
        conn.execute("PRAGMA journal_mode = DELETE")
        conn.commit()
        _fsync_file(temporary)
    except BaseException:
        if conn is not None:
            conn.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if conn is not None:
            conn.close()
    try:
        os.replace(temporary, paths.campaign_db)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    ContinuityStore(conn).rebuild_fts()
    ConfirmedHistoryStore(conn).rebuild_fts()
    conn.commit()


_REQUIRED_BUSINESS_TABLES = (
    "current_state",
    "continuity_items",
    "continuity_logs",
    "continuity_log_evidence",
    "continuity_processed_turns",
    "short_term_scenes",
    "scene_summaries",
    "scene_summary_meta",
    "history_turns",
    "history_documents",
    "npc_profiles",
    "npc_field_change_logs",
    "npc_field_change_evidence",
    "npc_field_change_processed_turns",
    "director_events",
    "director_npcs",
    "director_log",
    "world_settings",
)


def validate_campaign_database(path: Path, *, allow_minimal: bool = False) -> None:
    """Validate a concrete ``campaign.db`` file (reusable for restore staging).

    Phase 4 promotes the former private ``_validate`` so offline restore can
    validate a staged temporary copy with the same schema/foreign-key/quick_check
    contract as first-install migration.

    ``allow_minimal`` is test-only: when True, a DB that fails schema checks
    but passes ``quick_check`` is accepted (used only for synthetic game_state-
    only DBs in tests). Production restores must leave it False.
    """
    conn = connect_sqlite(path)
    try:
        for component, version in SUPPORTED_SCHEMA_VERSIONS.items():
            row = conn.execute(
                "SELECT version FROM schema_versions WHERE component = ?",
                (component,),
            ).fetchone()
            if row is None:
                raise RuntimeError(
                    f"campaign.db is missing schema version for {component!r}"
                )
            if int(row["version"]) != version:
                raise RuntimeError(
                    f"campaign.db schema version for {component!r} is "
                    f"{row['version']}, expected {version}"
                )
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = [name for name in _REQUIRED_BUSINESS_TABLES if name not in tables]
        if missing:
            raise RuntimeError(
                "campaign.db is missing required business tables: "
                + ", ".join(sorted(missing))
            )
        mismatches = conn.execute("PRAGMA foreign_key_check").fetchall()
        if mismatches:
            raise RuntimeError(
                f"campaign.db failed foreign key check ({len(mismatches)} rows)"
            )
        quick = conn.execute("PRAGMA quick_check").fetchone()
        if quick is None or str(quick[0]) != "ok":
            raise RuntimeError(
                f"campaign.db failed quick_check: {quick[0] if quick else 'no result'}"
            )
    except Exception as exc:
        # Test-only minimal DB fallback: allow a synthetic game_state-only DB
        # when explicitly requested. Production must not set allow_minimal.
        if allow_minimal and (
            "no such table" in str(exc)
            or "missing schema version" in str(exc)
            or "missing required business tables" in str(exc)
        ):
            try:
                q = conn.execute("PRAGMA quick_check").fetchone()
                if q is not None and str(q[0]) == "ok":
                    return
            except Exception:
                pass
        # Normalize to RuntimeError for callers expecting it
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(str(exc)) from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


# Backwards compatibility: private name retained for internal call sites migrated to public alias.
_validate = validate_campaign_database


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory after atomic replace."""
    try:
        fd = os.open(str(path), os.O_DIRECTORY)
    except (OSError, NotADirectoryError):
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _archive_legacy(paths: CampaignPaths, legacy_files: dict[str, Path]) -> None:
    archive = paths.data_dir / LEGACY_ARCHIVE_DIR
    archive.mkdir(parents=True, exist_ok=True)
    for path in legacy_files.values():
        for suffix in ("", "-wal", "-shm"):
            candidate = Path(str(path) + suffix)
            if candidate.exists():
                shutil.move(str(candidate), str(archive / candidate.name))


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())