from __future__ import annotations

import os
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

UNSUPPORTED_LEGACY_LAYOUT_MESSAGE = (
    "unsupported legacy layout: save contains legacy ten database files, "
    "turn_history.db or director_mutation.json from a pre-1.0 layout. This build only supports "
    "campaign.db + .runtime/control.db. Migrate the save with a pre-Phase4 build first."
)


def has_legacy_layout(data_dir: Path) -> list[str]:
    """Return sorted list of legacy layout markers found at the save root.

    Only checks the save root (ten DBs, turn_history.db, director_mutation.json).
    Snapshot-internal legacy files are handled by snapshots/save_archive directly.
    """
    data_dir = Path(data_dir)
    found: list[str] = []
    for name in LEGACY_DB_FILES:
        if (data_dir / name).is_file():
            found.append(name)
    if (data_dir / ".runtime" / "turn_history.db").is_file():
        found.append(".runtime/turn_history.db")
    if (data_dir / ".runtime" / "director_mutation.json").is_file():
        found.append(".runtime/director_mutation.json")
    return sorted(found)


def ensure_campaign_database(paths: CampaignPaths) -> str:
    """Return the outcome: ``"validated"`` or ``"created"``.

    Only the current format (``campaign.db``) is supported. Any legacy ten
    databases or ``.runtime/director_mutation.json`` without a valid current
    database cause a fail-closed ``RuntimeError``; no empty database is created
    over an old layout.
    """
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    journal = paths.data_dir / ".runtime" / "director_mutation.json"
    if journal.is_file():
        raise RuntimeError(
            f"{UNSUPPORTED_LEGACY_LAYOUT_MESSAGE} Found: .runtime/director_mutation.json"
        )
    if paths.campaign_db.exists():
        validate_campaign_database(paths.campaign_db)
        return "validated"
    legacy_markers = has_legacy_layout(paths.data_dir)
    if legacy_markers:
        raise RuntimeError(
            f"{UNSUPPORTED_LEGACY_LAYOUT_MESSAGE} Found: {', '.join(legacy_markers)}"
        )
    _create_empty(paths)
    return "created"


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


def _install_campaign_db(paths: CampaignPaths) -> None:
    """Build ``campaign.db`` in a temporary file, validate, atomically install.

    Shared by first-install creation of an empty database. Any failure before
    ``os.replace`` removes the temporary file; a failure during replace cleans
    up and re-raises.
    """
    temporary = paths.data_dir / f"campaign.db.{uuid.uuid4().hex}.tmp"
    conn = None
    try:
        conn = connect_sqlite(temporary)
        _build_campaign_schema(conn)
        validate_campaign_database(temporary)
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


def _create_empty(paths: CampaignPaths) -> None:
    _install_campaign_db(paths)


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


def validate_campaign_database(path: Path) -> None:
    """Validate a concrete ``campaign.db`` file (reusable for restore staging).

    Phase 4 promotes the former private ``_validate`` so offline restore can
    validate a staged temporary copy with the same schema/foreign-key/quick_check
    contract as first-install migration.
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
        # Normalize to RuntimeError for callers expecting it
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError(str(exc)) from exc
    finally:
        try:
            conn.close()
        except Exception:
            pass


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


def _fsync_file(path: Path) -> None:
    with open(path, "rb") as handle:
        os.fsync(handle.fileno())
