#!/usr/bin/env python3
"""Replay legacy NPC updates against confirmed prose, one turn at a time.

This is a migration/audit tool, not part of the production confirmation graph.
It deliberately writes to ``<save>/.npc-field-replay`` and never mutates the
save's live ``npc_profiles.db``.  The replay uses:

* the pre-confirmation NPC snapshot captured for each applied turn;
* that turn's persisted ``state_before`` and Core Memory;
* exact scene-paragraph documents from Confirmed History;
* the current Scene Facts extractor and ``npc_batch_update`` pipeline.

After every successful turn the accepted stable changes are committed to the
replay database through the production v8 field-history contract.  The replay
database is the progress marker, so a failed call cannot partially advance the
sequence.  Promotion into a real save is intentionally out of scope: review
all per-turn reports and the final profile diff first.

The full v8 replay workflow (independent workspace, per-turn ``session_type``
traces, and an explicit promote tool) is scheduled for Ticket 11 Phase 6; this
tool currently provides the minimal v8 schema compatibility so it can read
current archives.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, is_dataclass
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Iterable, cast
import uuid

from app.core.llm.config import SharedGatewayFactory
from app.core.time import utc_now
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.core_memory.store import CoreMemoryStore
from app.domain.current_state.schemas import CurrentState
from app.domain.npc_profile.batch_updater import apply_npc_batch_update
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_facts import SceneFactExtractor
from app.domain.scene_facts.schemas import SceneFactExtractInput, SceneParagraphInput
from app.runtime.model_config import ModelConfigStore


WORKSPACE_NAME = ".npc-field-replay"
REPLAY_DB_NAME = "npc_profiles.replay.db"
METADATA_NAME = "metadata.json"
REPORTS_DIR_NAME = "reports"
TRACE_NAME = "llm_traces.jsonl"
SUPPORTED_MAIN_SCHEMA = 8
REPLAY_FIELDS = (
    "role",
    "appearance",
    "faction",
    "last_scene_summary",
    "last_scene_turn",
    "last_known_location",
    "attitude_to_player",
)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _schema_version(path: Path, component: str) -> int | None:
    with _connect_readonly(path) as connection:
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_versions'"
        ).fetchone()
        if exists is None:
            return None
        row = connection.execute(
            "SELECT version FROM schema_versions WHERE component = ?",
            (component,),
        ).fetchone()
    return int(row["version"]) if row is not None else None


def _field_history_counts(path: Path) -> dict[str, int]:
    with _connect_readonly(path) as connection:
        tables = {
            row["name"]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        result: dict[str, int] = {}
        for table in (
            "npc_field_change_logs",
            "npc_field_change_evidence",
            "npc_field_change_processed_turns",
        ):
            result[table] = (
                int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                if table in tables
                else 0
            )
    return result


def _turn_specs(save_dir: Path) -> list[dict[str, Any]]:
    history_db = save_dir / ".runtime" / "turn_history.db"
    confirmed_db = save_dir / "confirmed_history.db"
    if not history_db.exists() or not confirmed_db.exists():
        raise FileNotFoundError("save is missing turn_history.db or confirmed_history.db")

    with _connect_readonly(confirmed_db) as connection:
        confirmed_turns = {
            int(row["turn_index"])
            for row in connection.execute(
                "SELECT turn_index FROM history_turns ORDER BY turn_index"
            )
        }
    with _connect_readonly(history_db) as connection:
        rows = connection.execute(
            """
            SELECT turn_index, turn_id, path
            FROM turn_snapshots
            WHERE status = 'applied'
            ORDER BY turn_index
            """
        ).fetchall()

    specs: list[dict[str, Any]] = []
    for row in rows:
        turn_index = int(row["turn_index"])
        if turn_index not in confirmed_turns:
            continue
        snapshot_dir = save_dir / str(row["path"])
        required = (
            snapshot_dir / "npc_profiles.db",
            snapshot_dir / "core_memory.json",
        )
        if not all(path.exists() for path in required):
            raise FileNotFoundError(
                f"turn {turn_index} snapshot is incomplete: {snapshot_dir}"
            )
        specs.append({
            "turn_index": turn_index,
            "turn_id": str(row["turn_id"]),
            "snapshot_dir": snapshot_dir,
        })
    if not specs:
        raise RuntimeError("save has no applied snapshot that overlaps Confirmed History")
    return specs


def _load_current_templates(save_dir: Path) -> dict[str, NPCProfile]:
    path = save_dir / "npc_profiles.db"
    if _schema_version(path, "npc_profile") != SUPPORTED_MAIN_SCHEMA:
        raise RuntimeError(
            "live copied npc_profiles.db must be upgraded to schema v8 before replay"
        )
    with _connect_readonly(path) as connection:
        rows = connection.execute(
            "SELECT id, profile_json FROM npc_profiles ORDER BY id"
        ).fetchall()
    templates: dict[str, NPCProfile] = {}
    for row in rows:
        profile = NPCProfile.model_validate(json.loads(row["profile_json"]))
        profile.npc_id = str(row["id"])
        templates[profile.npc_id] = profile
    return templates


def _legacy_rows(path: Path) -> list[dict[str, Any]]:
    with _connect_readonly(path) as connection:
        rows = connection.execute(
            "SELECT id, profile_json FROM npc_profiles ORDER BY id"
        ).fetchall()
    return [
        {"npc_id": str(row["id"]), "profile": json.loads(row["profile_json"])}
        for row in rows
    ]


def _adapt_legacy_profile(
    row: dict[str, Any],
    templates: dict[str, NPCProfile],
) -> NPCProfile:
    npc_id = row["npc_id"]
    legacy = row["profile"]
    template = templates.get(npc_id)
    if template is None:
        raise RuntimeError(
            f"legacy snapshot NPC {npc_id!r} has no current v8 profile template"
        )
    profile = template.model_copy(deep=True)

    for field_name in (
        "name",
        "aliases",
        "transient_aliases",
        "role",
        "appearance",
        "faction",
        "last_scene_summary",
        "last_scene_turn",
        "attitude_to_player",
        "active",
        "name_hit_count",
        "alias_hit_counts",
        "source_scene_ids",
        "source_setting_ids",
        "source_director_npc_id",
        "created_turn",
        "introduced_turn",
        "updated_at",
    ):
        if field_name in legacy:
            setattr(profile, field_name, legacy[field_name])
    historical_location = legacy.get(
        "last_known_location",
        legacy.get("current_location"),
    )
    profile.last_known_location = historical_location
    # Old ability/personality shapes are deliberately not projected.  Both are
    # frozen in the current per-turn updater and the v8 template is valid.
    return NPCProfile.model_validate(profile.model_dump(mode="python"))


def _seed_missing_profiles(
    store: NPCProfileStore,
    *,
    snapshot_db: Path,
    templates: dict[str, NPCProfile],
) -> list[str]:
    existing = {profile.npc_id for profile in store.list_all(active_only=False)}
    seeded: list[str] = []
    for row in _legacy_rows(snapshot_db):
        if row["npc_id"] in existing:
            continue
        profile = _adapt_legacy_profile(row, templates)
        store.upsert(profile)
        seeded.append(profile.npc_id)
    return seeded


def _history_documents(save_dir: Path, turn_index: int) -> list[HistoryDocument]:
    path = save_dir / "confirmed_history.db"
    with _connect_readonly(path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM history_documents
            WHERE turn_index = ?
            ORDER BY CASE source_type WHEN 'player_input' THEN 0 ELSE 1 END,
                     paragraph_index
            """,
            (turn_index,),
        ).fetchall()
    documents = [HistoryDocument.model_validate(dict(row)) for row in rows]
    paragraphs = [item for item in documents if item.source_type == "scene_paragraph"]
    if not paragraphs:
        raise RuntimeError(f"turn {turn_index} has no confirmed scene paragraphs")
    return documents


def _turn_states(save_dir: Path, turn_id: str) -> tuple[CurrentState, CurrentState]:
    path = save_dir / ".runtime" / "turn_history.db"
    with _connect_readonly(path) as connection:
        row = connection.execute(
            """
            SELECT state_before_json, state_after_json
            FROM turn_transactions
            WHERE turn_id = ?
            """,
            (turn_id,),
        ).fetchone()
    if row is None or not row["state_before_json"] or not row["state_after_json"]:
        raise RuntimeError(
            f"turn transaction {turn_id!r} has no complete before/after state"
        )
    return (
        CurrentState.model_validate_json(row["state_before_json"]),
        CurrentState.model_validate_json(row["state_after_json"]),
    )


def _profile_projection(profiles: Iterable[NPCProfile]) -> dict[str, dict[str, Any]]:
    return {
        profile.npc_id: {
            "name": profile.name,
            **{field: getattr(profile, field) for field in REPLAY_FIELDS},
        }
        for profile in sorted(profiles, key=lambda item: item.npc_id)
    }


def _legacy_snapshot_projection(
    path: Path,
    templates: dict[str, NPCProfile],
) -> dict[str, dict[str, Any]]:
    return _profile_projection(
        _adapt_legacy_profile(row, templates)
        for row in _legacy_rows(path)
    )


def _projection_diff(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    return {
        npc_id: {
            "before": before.get(npc_id),
            "after": after.get(npc_id),
        }
        for npc_id in sorted(set(before) | set(after))
        if before.get(npc_id) != after.get(npc_id)
    }


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(cast(Any, value))
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return value


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(_jsonable(payload), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _checkpoint_wal(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        connection.close()


def _processed_turns(path: Path) -> list[int]:
    if not path.exists():
        return []
    with _connect_readonly(path) as connection:
        rows = connection.execute(
            "SELECT turn_index FROM npc_field_change_processed_turns ORDER BY turn_index"
        ).fetchall()
    return [int(row["turn_index"]) for row in rows]


def inspect_save(save_dir: Path) -> dict[str, Any]:
    save_dir = save_dir.resolve()
    npc_db = save_dir / "npc_profiles.db"
    specs = _turn_specs(save_dir)
    return {
        "save_dir": str(save_dir),
        "npc_schema_version": _schema_version(npc_db, "npc_profile"),
        "live_field_history": _field_history_counts(npc_db),
        "replayable_turns": [item["turn_index"] for item in specs],
        "first_snapshot_schema": _schema_version(
            specs[0]["snapshot_dir"] / "npc_profiles.db",
            "npc_profile",
        ),
        "workspace": str(save_dir / WORKSPACE_NAME),
    }


def init_replay(save_dir: Path) -> dict[str, Any]:
    save_dir = save_dir.resolve()
    audit = inspect_save(save_dir)
    if audit["npc_schema_version"] != SUPPORTED_MAIN_SCHEMA:
        raise RuntimeError("copied save is not schema v8")
    workspace = save_dir / WORKSPACE_NAME
    replay_db = workspace / REPLAY_DB_NAME
    if replay_db.exists():
        raise FileExistsError(
            f"replay already initialized at {replay_db}; use status/run"
        )
    workspace.mkdir(parents=True, exist_ok=True)
    specs = _turn_specs(save_dir)
    templates = _load_current_templates(save_dir)
    first_snapshot_db = specs[0]["snapshot_dir"] / "npc_profiles.db"
    with NPCProfileStore(str(replay_db)) as store:
        seeded = _seed_missing_profiles(
            store,
            snapshot_db=first_snapshot_db,
            templates=templates,
        )
    _checkpoint_wal(replay_db)
    metadata = {
        "format_version": 1,
        "created_at": utc_now(),
        "save_dir": str(save_dir),
        "replayable_turns": [item["turn_index"] for item in specs],
        "initial_seeded_npc_ids": seeded,
        "live_database_was_modified": False,
    }
    _write_json_atomic(workspace / METADATA_NAME, metadata)
    return {**audit, "initialized": True, "seeded_npc_ids": seeded}


def replay_status(save_dir: Path) -> dict[str, Any]:
    save_dir = save_dir.resolve()
    workspace = save_dir / WORKSPACE_NAME
    replay_db = workspace / REPLAY_DB_NAME
    specs = _turn_specs(save_dir)
    available = [item["turn_index"] for item in specs]
    processed = _processed_turns(replay_db)
    result: dict[str, Any] = {
        "initialized": replay_db.exists(),
        "available_turns": available,
        "processed_turns": processed,
        "next_turn": next((turn for turn in available if turn not in processed), None),
        "remaining_turns": [turn for turn in available if turn not in processed],
        "live_database_was_modified": False,
    }
    if replay_db.exists():
        result["replay_field_history"] = _field_history_counts(replay_db)
        templates = _load_current_templates(save_dir)
        with NPCProfileStore(str(replay_db)) as store:
            replay_profiles = _profile_projection(store.list_all(active_only=False))
        live_profiles = _profile_projection(templates.values())
        result["profile_diff"] = {
            npc_id: {
                "replay": replay_profiles.get(npc_id),
                "live": live_profiles.get(npc_id),
            }
            for npc_id in sorted(set(replay_profiles) | set(live_profiles))
            if replay_profiles.get(npc_id) != live_profiles.get(npc_id)
        }
    return result


def run_turn(save_dir: Path, turn_index: int) -> dict[str, Any]:
    save_dir = save_dir.resolve()
    workspace = save_dir / WORKSPACE_NAME
    replay_db = workspace / REPLAY_DB_NAME
    if not replay_db.exists():
        raise RuntimeError("replay is not initialized; run the init command first")

    specs = _turn_specs(save_dir)
    by_turn = {item["turn_index"]: item for item in specs}
    if turn_index not in by_turn:
        raise ValueError(f"turn {turn_index} is not replayable")
    status = replay_status(save_dir)
    if turn_index in status["processed_turns"]:
        raise ValueError(f"turn {turn_index} was already replayed")
    if turn_index != status["next_turn"]:
        raise ValueError(
            f"turns must be replayed in order; next turn is {status['next_turn']}"
        )

    spec = by_turn[turn_index]
    documents = _history_documents(save_dir, turn_index)
    paragraph_documents = [
        item for item in documents if item.source_type == "scene_paragraph"
    ]
    scene_id = paragraph_documents[0].scene_id
    if any(item.scene_id != scene_id for item in paragraph_documents):
        raise RuntimeError(f"turn {turn_index} spans multiple scene ids")
    state_before, state_after = _turn_states(save_dir, spec["turn_id"])
    if state_after.scene.scene_id != scene_id:
        raise RuntimeError(
            f"turn {turn_index} confirmed History scene {scene_id!r} does not "
            f"match updated state scene {state_after.scene.scene_id!r}"
        )
    core_memory = CoreMemoryStore(
        spec["snapshot_dir"] / "core_memory.json"
    ).load_required()

    candidate_db = workspace / f".{REPLAY_DB_NAME}.turn-{turn_index}.tmp"
    templates = _load_current_templates(save_dir)
    historical_before = _legacy_snapshot_projection(
        spec["snapshot_dir"] / "npc_profiles.db",
        templates,
    )
    following_spec = next(
        (
            item
            for item in specs
            if item["turn_index"] == turn_index + 1
        ),
        None,
    )
    historical_after = (
        _legacy_snapshot_projection(
            following_spec["snapshot_dir"] / "npc_profiles.db",
            templates,
        )
        if following_spec is not None
        else _profile_projection(templates.values())
    )
    trace_id = f"npc-field-replay-{turn_index}-{uuid.uuid4()}"
    report_path = workspace / REPORTS_DIR_NAME / f"turn_{turn_index:04d}.json"
    config = ModelConfigStore().load_effective()
    if not config.configured:
        raise RuntimeError("model is not configured; configure it in the app or .env")
    gateway = SharedGatewayFactory(
        config=config.gateway_config(),
        log_path=str(workspace / TRACE_NAME),
    )()

    candidate_db.unlink(missing_ok=True)
    # ``NPCProfileStore`` uses WAL.  A plain file copy is only a complete
    # transaction snapshot after the source WAL has been checkpointed.  Delay
    # the copy until every non-LLM input has validated, avoiding stale temp
    # files when configuration or a historical snapshot is invalid.
    _checkpoint_wal(replay_db)
    shutil.copy2(replay_db, candidate_db)
    try:
        with NPCProfileStore(str(candidate_db)) as store:
            seeded = _seed_missing_profiles(
                store,
                snapshot_db=spec["snapshot_dir"] / "npc_profiles.db",
                templates=templates,
            )
            before = _profile_projection(store.list_all(active_only=False))
            scene_facts = SceneFactExtractor(gateway).extract(
                SceneFactExtractInput(
                    scene_text="\n\n".join(item.text for item in paragraph_documents),
                    # Production extracts Scene Facts against the state entering
                    # the scene, before confirmation applies state operations.
                    current_state=state_before,
                    core_memory=core_memory,
                    scene_paragraphs=[
                        SceneParagraphInput(
                            document_id=item.document_id,
                            text=item.text,
                        )
                        for item in paragraph_documents
                    ],
                ),
                trace_id=trace_id,
            )
            result = apply_npc_batch_update(
                npc_store=store,
                gateway=gateway,
                scene_facts=scene_facts,
                evidence_documents={item.document_id: item for item in documents},
                # Production NPC admission runs after state_update and consumes
                # updated_state for participant location/attitude synchronization.
                current_state=state_after,
                turn_index=turn_index,
                scene_id=scene_id,
                admitted_at=utc_now(),
                trace_id=trace_id,
            )
            record_result = store.record_accepted_field_changes(
                changes=result.accepted_changes,
                evidence_documents=documents,
                turn_index=turn_index,
                scene_id=scene_id,
            )
            after = _profile_projection(store.list_all(active_only=False))
            report = {
                "turn_index": turn_index,
                "turn_id": spec["turn_id"],
                "scene_id": scene_id,
                "trace_id": trace_id,
                "seeded_npc_ids": seeded,
                "scene_facts": scene_facts,
                "accepted_changes": result.accepted_changes,
                "accepted_alias_changes": result.accepted_alias_changes,
                "new_npc_candidates": result.new_npc_candidates,
                "warnings": result.warnings,
                "coverage": result.coverage,
                "field_history": record_result,
                "profiles_before": before,
                "profiles_after": after,
                "historical_snapshot_before": historical_before,
                "historical_observed_after": historical_after,
                "historical_observed_diff": _projection_diff(
                    historical_before,
                    historical_after,
                ),
                "replay_vs_historical_after": _projection_diff(
                    historical_after,
                    after,
                ),
            }
        _checkpoint_wal(candidate_db)
        _write_json_atomic(report_path, report)
        # Remove any empty sidecars belonging to the old replay database before
        # atomically replacing its main file with the fully checkpointed one.
        replay_db.with_name(replay_db.name + "-wal").unlink(missing_ok=True)
        replay_db.with_name(replay_db.name + "-shm").unlink(missing_ok=True)
        os.replace(candidate_db, replay_db)
    finally:
        candidate_db.unlink(missing_ok=True)
        candidate_db.with_name(candidate_db.name + "-wal").unlink(missing_ok=True)
        candidate_db.with_name(candidate_db.name + "-shm").unlink(missing_ok=True)

    return {
        "turn_index": turn_index,
        "trace_id": trace_id,
        "report": str(report_path),
        "accepted_change_count": len(result.accepted_changes),
        "field_log_count": len(record_result.logs),
        "warning_count": len(result.warnings),
        "next_turn": replay_status(save_dir)["next_turn"],
        "live_database_was_modified": False,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Replay NPC field updates from a copied legacy save.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "init", "status"):
        child = subparsers.add_parser(name)
        child.add_argument("save_dir", type=Path)
    run = subparsers.add_parser("run")
    run.add_argument("save_dir", type=Path)
    run.add_argument("--turn", type=int, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "inspect":
            result = inspect_save(args.save_dir)
        elif args.command == "init":
            result = init_replay(args.save_dir)
        elif args.command == "status":
            result = replay_status(args.save_dir)
        else:
            result = run_turn(args.save_dir, args.turn)
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(_jsonable(result), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
