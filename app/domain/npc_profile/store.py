import json
import sqlite3
from collections.abc import Sequence

from app.core.text import normalize_compact_key, normalize_key
from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.npc_profile.changes import AcceptedNPCFieldChange
from app.domain.npc_profile.field_history import (
    NPCFieldChangeEvidence,
    NPCFieldChangeLog,
    NPCFieldChangeRecordResult,
    PreparedNPCFieldBatch,
    prepare_accepted_field_changes,
)
from app.domain.npc_profile.schemas import (
    NPCProfile,
    utc_now,
)
from app.runtime.sqlite import (
    ensure_exact_schema_version,
    transaction,
)


SCHEMA_VERSION = 9


class NPCProfileStore:
    def __init__(self, conn: sqlite3.Connection, *, owns_connection: bool = False):
        self._db = conn
        self._owns_connection = owns_connection
        self._create_tables()

    def _create_tables(self) -> None:
        persisted_version = self._current_schema_version()
        if persisted_version not in {None, 6, 7, 8, SCHEMA_VERSION}:
            raise RuntimeError(
                "Unsupported npc_profile database schema: "
                f"expected {SCHEMA_VERSION}, got {persisted_version}"
            )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS npc_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                profile_json TEXT NOT NULL,
                source_scene_ids_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                name_hit_count INTEGER NOT NULL DEFAULT 0,
                alias_hit_counts_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        expected_columns = {
            "id",
            "name",
            "aliases_json",
            "profile_json",
            "source_scene_ids_json",
            "updated_at",
            "active",
            "name_hit_count",
            "alias_hit_counts_json",
        }
        actual_columns = {
            row["name"]
            for row in self._db.execute(
                "PRAGMA table_info(npc_profiles)"
            ).fetchall()
        }
        if actual_columns != expected_columns:
            raise RuntimeError(
                "npc_profiles.db does not match the current NPCProfile contract"
            )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_npc_profiles_name
            ON npc_profiles(active, name)
            """
        )
        version = persisted_version
        if version == 6:
            self._migrate_v6_to_v7()
            self._migrate_v7_to_v8()
            self._migrate_v8_to_v9()
        elif version == 7:
            self._migrate_v7_to_v8()
            self._migrate_v8_to_v9()
        elif version == 8:
            self._migrate_v8_to_v9()
        elif version is None:
            with transaction(self._db):
                self._create_field_history_tables()
                self._validate_field_history_schema()
                ensure_exact_schema_version(
                    self._db,
                    "npc_profile",
                    SCHEMA_VERSION,
                )
        elif version != SCHEMA_VERSION:
            raise RuntimeError(
                "Unsupported npc_profile database schema: "
                f"expected {SCHEMA_VERSION}, got {version}"
            )
        self._validate_field_history_schema()
        ensure_exact_schema_version(
            self._db,
            "npc_profile",
            SCHEMA_VERSION,
        )
        self._db.commit()

    def _current_schema_version(self) -> int | None:
        versions_table = self._db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("schema_versions",),
        ).fetchone()
        if versions_table is None:
            return None
        row = self._db.execute(
            "SELECT version FROM schema_versions WHERE component = ?",
            ("npc_profile",),
        ).fetchone()
        return int(row["version"]) if row is not None else None

    def _migrate_v6_to_v7(self) -> None:
        """Add evidence-backed field history without rewriting profile rows."""

        with transaction(self._db):
            self._create_field_history_tables()
            self._validate_field_history_schema()
            self._db.execute(
                """
                UPDATE schema_versions
                SET version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE component = 'npc_profile'
                """,
                (7,),
            )

    def _migrate_v7_to_v8(self) -> None:
        """Rewrite v7 profile rows onto the v8 contract.

        - ``role`` 为空且 legacy ``public_summary`` 非空时，以该摘要初始化 role，
          避免唯一公开身份文本丢失；role 已存在则保持 role。
        - 删除 legacy ``public_summary`` / ``current_intent``。
        - 新字段 ``last_scene_summary`` / ``last_scene_turn`` 初始化为 null，
          不伪造最近出场历史。
        - 重建 ``npc_field_change_logs`` 以允许写入 ``last_scene_summary``；
          legacy ``public_summary`` 日志保留为只读 audit row。
        """
        # SQLite 的 foreign_keys 只能在事务外切换；重建阶段临时关闭以避免
        # DROP/CREATE 顺序触发级联删除或引用校验，成功路径全程在事务内保持原子。
        self._db.execute("PRAGMA foreign_keys = OFF")
        try:
            with transaction(self._db):
                rows = self._db.execute(
                    "SELECT id, profile_json FROM npc_profiles"
                ).fetchall()
                for row in rows:
                    raw = json.loads(row["profile_json"])
                    role_value = raw.get("role")
                    legacy_summary = raw.get("public_summary")
                    role_is_empty = (
                        not isinstance(role_value, str)
                        or not role_value.strip()
                    )
                    if (
                        role_is_empty
                        and isinstance(legacy_summary, str)
                        and legacy_summary.strip()
                    ):
                        role_value = legacy_summary.strip()
                    raw.pop("public_summary", None)
                    raw.pop("current_intent", None)
                    if role_value is not None:
                        raw["role"] = role_value
                    raw["last_scene_summary"] = None
                    raw["last_scene_turn"] = None
                    validated = NPCProfile.model_validate(raw)
                    self._db.execute(
                        "UPDATE npc_profiles SET profile_json = ? WHERE id = ?",
                        (validated.model_dump_json(), row["id"]),
                    )
                self._rebuild_field_history_logs_table()
                self._validate_field_history_schema()
                violations = self._db.execute(
                    "PRAGMA foreign_key_check"
                ).fetchall()
                if violations:
                    raise RuntimeError(
                        "npc_profile v7->v8 migration left orphaned "
                        "field-history rows; refusing to finalize: "
                        f"{sorted(map(tuple, violations))[:3]}"
                    )
                self._db.execute(
                    """
                    UPDATE schema_versions
                    SET version = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE component = 'npc_profile'
                    """,
                    (SCHEMA_VERSION,),
                )
        finally:
            self._db.execute("PRAGMA foreign_keys = ON")

    def _rebuild_field_history_logs_table(self) -> None:
        """Rebuild ``npc_field_change_logs`` with the v8 field-name contract.

        SQLite cannot alter a CHECK constraint in place, so the table is rebuilt
        inside the migration transaction (caller keeps foreign_keys off).  Data
        is staged in plain backup tables so ``log_id`` values and the evidence
        mapping survive intact.  ``public_summary`` stays in the allowed set
        purely as a read-only legacy audit field: the Python write contract no
        longer produces it, and the History bridge stops emitting it.  Legacy
        rows (which carry ``summary_delta``) therefore copy unchanged while
        ``last_scene_summary`` becomes writable.
        """
        self._db.execute(
            "CREATE TABLE npc_field_change_logs_backup AS "
            "SELECT log_id, npc_id, field_name, turn_index, old_value, new_value, "
            "summary_delta, source FROM npc_field_change_logs"
        )
        self._db.execute(
            "CREATE TABLE npc_field_change_evidence_backup AS "
            "SELECT log_id, position, evidence_id, evidence_hash "
            "FROM npc_field_change_evidence"
        )
        self._db.execute("DROP TABLE npc_field_change_logs")
        self._db.execute("DROP TABLE npc_field_change_evidence")
        self._create_field_history_tables()
        self._db.execute(
            """
            INSERT INTO npc_field_change_logs(
                log_id, npc_id, field_name, turn_index, old_value, new_value,
                summary_delta, source
            )
            SELECT log_id, npc_id, field_name, turn_index, old_value, new_value,
                   summary_delta, source
            FROM npc_field_change_logs_backup
            ORDER BY log_id
            """
        )
        self._db.execute(
            """
            INSERT INTO npc_field_change_evidence(
                log_id, position, evidence_id, evidence_hash
            )
            SELECT log_id, position, evidence_id, evidence_hash
            FROM npc_field_change_evidence_backup
            ORDER BY log_id, position
            """
        )
        self._db.execute("DROP TABLE npc_field_change_logs_backup")
        self._db.execute("DROP TABLE npc_field_change_evidence_backup")

    def _migrate_v8_to_v9(self) -> None:
        """Add the trusted per-change ``change_basis`` column (v9).

        Additive nullable column: existing logs keep ``change_basis`` NULL, and
        the History bridge marks such nodes ``basis_complete=False`` (incomplete
        basis, degraded-rescue only) instead of pretending a complete delta.
        """

        with transaction(self._db):
            columns = {
                row["name"]
                for row in self._db.execute(
                    "PRAGMA table_info(npc_field_change_logs)"
                ).fetchall()
            }
            if "change_basis" not in columns:
                self._db.execute(
                    "ALTER TABLE npc_field_change_logs "
                    "ADD COLUMN change_basis TEXT"
                )
            self._validate_field_history_schema()
            self._db.execute(
                """
                UPDATE schema_versions
                SET version = ?, updated_at = CURRENT_TIMESTAMP
                WHERE component = 'npc_profile'
                """,
                (9,),
            )

    def _create_field_history_tables(self) -> None:
        statements = (
            """
            CREATE TABLE IF NOT EXISTS npc_field_change_logs (
                log_id INTEGER PRIMARY KEY AUTOINCREMENT,
                npc_id TEXT NOT NULL,
                field_name TEXT NOT NULL CHECK(field_name IN (
                    'public_summary', 'role', 'appearance', 'faction',
                    'last_scene_summary'
                )),
                turn_index INTEGER NOT NULL CHECK(turn_index >= 0),
                old_value TEXT,
                new_value TEXT NOT NULL,
                change_basis TEXT,
                summary_delta TEXT,
                source TEXT NOT NULL CHECK(source = 'proposal'),
                CHECK(
                    (field_name = 'public_summary'
                     AND summary_delta IS NOT NULL
                     AND length(trim(summary_delta)) > 0)
                    OR
                    (field_name != 'public_summary' AND summary_delta IS NULL)
                ),
                UNIQUE(npc_id, field_name, turn_index),
                FOREIGN KEY(npc_id) REFERENCES npc_profiles(id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_npc_field_change_bridge
            ON npc_field_change_logs(npc_id, field_name, turn_index, log_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS npc_field_change_evidence (
                log_id INTEGER NOT NULL,
                position INTEGER NOT NULL CHECK(position >= 0),
                evidence_id TEXT NOT NULL,
                evidence_hash TEXT NOT NULL CHECK(length(evidence_hash) = 64),
                PRIMARY KEY(log_id, position),
                UNIQUE(log_id, evidence_id),
                FOREIGN KEY(log_id) REFERENCES npc_field_change_logs(log_id)
                    ON DELETE CASCADE
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_npc_field_change_evidence_id
            ON npc_field_change_evidence(evidence_id)
            """,
            """
            CREATE TABLE IF NOT EXISTS npc_field_change_processed_turns (
                turn_index INTEGER PRIMARY KEY CHECK(turn_index >= 0),
                batch_hash TEXT NOT NULL CHECK(length(batch_hash) = 64)
            )
            """,
        )
        for statement in statements:
            self._db.execute(statement)

    def _validate_field_history_schema(self) -> None:
        expected_columns = {
            "npc_field_change_logs": {
                "log_id": ("INTEGER", 0, 1),
                "npc_id": ("TEXT", 1, 0),
                "field_name": ("TEXT", 1, 0),
                "turn_index": ("INTEGER", 1, 0),
                "old_value": ("TEXT", 0, 0),
                "new_value": ("TEXT", 1, 0),
                "change_basis": ("TEXT", 0, 0),
                "summary_delta": ("TEXT", 0, 0),
                "source": ("TEXT", 1, 0),
            },
            "npc_field_change_evidence": {
                "log_id": ("INTEGER", 1, 1),
                "position": ("INTEGER", 1, 2),
                "evidence_id": ("TEXT", 1, 0),
                "evidence_hash": ("TEXT", 1, 0),
            },
            "npc_field_change_processed_turns": {
                "turn_index": ("INTEGER", 0, 1),
                "batch_hash": ("TEXT", 1, 0),
            },
        }
        for table, expected in expected_columns.items():
            rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()
            actual = {
                row["name"]: (
                    str(row["type"]).upper(),
                    int(row["notnull"]),
                    int(row["pk"]),
                )
                for row in rows
            }
            if actual != expected:
                self._raise_field_history_schema_error(table)

        self._require_foreign_keys(
            "npc_field_change_logs",
            {("npc_id", "npc_profiles", "id", "CASCADE")},
        )
        self._require_foreign_keys(
            "npc_field_change_evidence",
            {("log_id", "npc_field_change_logs", "log_id", "CASCADE")},
        )
        self._require_foreign_keys("npc_field_change_processed_turns", set())

        required_indexes = (
            (
                "npc_field_change_logs",
                ("npc_id", "field_name", "turn_index"),
                True,
            ),
            (
                "npc_field_change_logs",
                ("npc_id", "field_name", "turn_index", "log_id"),
                False,
            ),
            (
                "npc_field_change_evidence",
                ("log_id", "evidence_id"),
                True,
            ),
            (
                "npc_field_change_evidence",
                ("evidence_id",),
                False,
            ),
        )
        for table, columns, unique in required_indexes:
            if not self._has_index(table, columns, unique=unique):
                self._raise_field_history_schema_error(table)

        required_sql_fragments = {
            "npc_field_change_logs": (
                "field_name text not null check(field_name in",
                "check(turn_index >= 0)",
                "check(source = 'proposal')",
                "length(trim(summary_delta)) > 0",
            ),
            "npc_field_change_evidence": (
                "check(position >= 0)",
                "check(length(evidence_hash) = 64)",
            ),
            "npc_field_change_processed_turns": (
                "check(turn_index >= 0)",
                "check(length(batch_hash) = 64)",
            ),
        }
        for table, fragments in required_sql_fragments.items():
            row = self._db.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name = ?",
                (table,),
            ).fetchone()
            normalized = " ".join(str(row["sql"] or "").lower().split()) if row else ""
            if any(fragment not in normalized for fragment in fragments):
                self._raise_field_history_schema_error(table)

    def _require_foreign_keys(
        self,
        table: str,
        expected: set[tuple[str, str, str, str]],
    ) -> None:
        actual = {
            (
                row["from"],
                row["table"],
                row["to"],
                str(row["on_delete"]).upper(),
            )
            for row in self._db.execute(
                f"PRAGMA foreign_key_list({table})"
            ).fetchall()
        }
        if actual != expected:
            self._raise_field_history_schema_error(table)

    def _has_index(
        self,
        table: str,
        columns: tuple[str, ...],
        *,
        unique: bool,
    ) -> bool:
        for row in self._db.execute(f"PRAGMA index_list({table})").fetchall():
            if bool(row["unique"]) != unique:
                continue
            indexed = tuple(
                item["name"]
                for item in self._db.execute(
                    f"PRAGMA index_info({row['name']})"
                ).fetchall()
            )
            if indexed == columns:
                return True
        return False

    @staticmethod
    def _raise_field_history_schema_error(table: str) -> None:
        raise RuntimeError(
            f"npc_profiles.db table {table!r} does not match "
            "the current NPC field-history contract"
        )

    def _row_to_profile(self, row: sqlite3.Row) -> NPCProfile:
        profile = NPCProfile.model_validate(json.loads(row["profile_json"]))
        profile.npc_id = row["id"]
        profile.source_scene_ids = json.loads(row["source_scene_ids_json"])
        profile.updated_at = row["updated_at"]
        profile.active = bool(row["active"])
        profile.name_hit_count = int(row["name_hit_count"])
        profile.alias_hit_counts = json.loads(
            row["alias_hit_counts_json"] or "{}"
        )
        return profile

    def upsert(self, profile: NPCProfile) -> str:
        # Pydantic 模型的字段可被直接赋值，不会自动重跑整体校验。
        # Store 是最后的持久化防线：任何未经重新校验的内存对象都不得
        # 序列化进 SQLite。
        NPCProfile.model_validate(profile.model_dump(mode="python"))
        self._ensure_given_name_alias(profile)
        NPCProfile.model_validate(profile.model_dump(mode="python"))
        now = profile.updated_at or utc_now()
        profile.updated_at = now
        with transaction(self._db):
            self._db.execute(
                """
                INSERT INTO npc_profiles (
                    id, name, aliases_json, profile_json, source_scene_ids_json,
                    updated_at, active, name_hit_count, alias_hit_counts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    aliases_json = excluded.aliases_json,
                    profile_json = excluded.profile_json,
                    source_scene_ids_json = excluded.source_scene_ids_json,
                    updated_at = excluded.updated_at,
                    active = excluded.active,
                    name_hit_count = excluded.name_hit_count,
                    alias_hit_counts_json = excluded.alias_hit_counts_json
                """,
                (
                    profile.npc_id,
                    profile.name,
                    json.dumps(profile.aliases, ensure_ascii=False),
                    profile.model_dump_json(),
                    json.dumps(profile.source_scene_ids, ensure_ascii=False),
                    now,
                    int(profile.active),
                    profile.name_hit_count,
                    json.dumps(profile.alias_hit_counts, ensure_ascii=False),
                ),
            )
        return profile.npc_id

    def get_by_id(self, npc_id: str) -> NPCProfile | None:
        row = self._db.execute(
            "SELECT * FROM npc_profiles WHERE id = ?",
            (npc_id,),
        ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def delete_by_id(self, npc_id: str) -> None:
        with transaction(self._db):
            affected_turns = [
                int(row["turn_index"])
                for row in self._db.execute(
                    """
                    SELECT DISTINCT turn_index
                    FROM npc_field_change_logs
                    WHERE npc_id = ?
                    """,
                    (npc_id,),
                ).fetchall()
            ]
            if affected_turns:
                # The processed hash describes the whole turn batch.  Keeping
                # other NPC rows or the marker after one member is cascaded
                # would leave a partial batch that an exact replay cannot heal.
                placeholders = ",".join("?" for _ in affected_turns)
                self._db.execute(
                    f"""
                    DELETE FROM npc_field_change_logs
                    WHERE turn_index IN ({placeholders})
                    """,
                    tuple(affected_turns),
                )
                self._db.execute(
                    f"""
                    DELETE FROM npc_field_change_processed_turns
                    WHERE turn_index IN ({placeholders})
                    """,
                    tuple(affected_turns),
                )
            self._db.execute(
                "DELETE FROM npc_profiles WHERE id = ?",
                (npc_id,),
            )

    def get_by_name(self, name: str) -> NPCProfile | None:
        term = normalize_key(name)
        if not term:
            return None
        for profile in self.list_all():
            if _name_matches(term, profile):
                return profile
        return None

    def get_by_source_director_npc_id(self, draft_id: str) -> NPCProfile | None:
        for profile in self.list_all():
            if profile.source_director_npc_id == draft_id:
                return profile
        return None

    def list_all(self, *, active_only: bool = True) -> list[NPCProfile]:
        sql = "SELECT * FROM npc_profiles"
        params: list[int] = []
        if active_only:
            sql += " WHERE active = ?"
            params.append(1)
        sql += " ORDER BY updated_at DESC, name ASC"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def append_alias(
        self,
        npc_id: str,
        alias: str,
        *,
        initial_hit_count: int = 0,
    ) -> bool:
        profile = self.get_by_id(npc_id)
        clean = str(alias).strip()
        if profile is None or not clean:
            return False
        alias_keys = {normalize_key(existing) for existing in profile.aliases}
        if (
            normalize_key(clean) in alias_keys
            or normalize_key(clean) == normalize_key(profile.name)
        ):
            return False
        profile.aliases.append(clean)
        if initial_hit_count > 0:
            profile.alias_hit_counts[clean] = max(
                profile.alias_hit_counts.get(clean, 0),
                initial_hit_count,
            )
        profile.updated_at = utc_now()
        self.upsert(profile)
        return True

    def record_mention(self, npc_id: str, surface: str) -> bool:
        profile = self.get_by_id(npc_id)
        clean = str(surface).strip()
        if profile is None or not clean:
            return False
        if normalize_compact_key(clean) == normalize_compact_key(profile.name):
            profile.name_hit_count += 1
        else:
            alias = _find_alias(profile, clean)
            if alias is None:
                return False
            profile.alias_hit_counts[alias] = profile.alias_hit_counts.get(alias, 0) + 1
        profile.updated_at = utc_now()
        self.upsert(profile)
        return True

    def record_accepted_field_changes(
        self,
        *,
        changes: Sequence[AcceptedNPCFieldChange],
        evidence_documents: Sequence[HistoryDocument],
        turn_index: int,
        scene_id: str,
        reset_before_record: bool = False,
    ) -> NPCFieldChangeRecordResult:
        """Atomically persist one confirmed turn's eligible field changes.

        A processed-turn hash closes the commit-marker gap: replaying the exact
        same batch is a no-op, while a different batch for the same turn fails
        closed unless the caller explicitly requests a history reset.
        """

        prepared = prepare_accepted_field_changes(
            changes=changes,
            evidence_documents=evidence_documents,
            turn_index=turn_index,
            scene_id=scene_id,
        )
        reset_performed = False
        replayed = False
        with transaction(self._db):
            self._require_field_history_profiles_match(prepared)
            existing = self._db.execute(
                """
                SELECT batch_hash
                FROM npc_field_change_processed_turns
                WHERE turn_index = ?
                """,
                (turn_index,),
            ).fetchone()
            if existing is not None and existing["batch_hash"] == prepared.batch_hash:
                if not reset_before_record or not self._has_other_field_history_turns(
                    turn_index
                ):
                    replayed = True
                else:
                    self._clear_field_history_rows()
                    reset_performed = True
            elif existing is not None and not reset_before_record:
                raise ValueError(
                    "NPC field history turn was already processed with a "
                    f"different batch: {turn_index}"
                )
            elif reset_before_record:
                self._clear_field_history_rows()
                reset_performed = True

            if not replayed:
                self._insert_prepared_field_batch(prepared)

        logs = self.list_field_change_logs(turn_index=turn_index)
        return NPCFieldChangeRecordResult(
            turn_index=turn_index,
            batch_hash=prepared.batch_hash,
            logs=logs,
            replayed=replayed,
            reset_performed=reset_performed,
        )

    def list_field_change_logs(
        self,
        *,
        npc_ids: Sequence[str] | None = None,
        turn_index: int | None = None,
    ) -> list[NPCFieldChangeLog]:
        clauses: list[str] = []
        params: list[object] = []
        if npc_ids is not None:
            ordered_ids = list(dict.fromkeys(npc_ids))
            if not ordered_ids:
                return []
            placeholders = ",".join("?" for _ in ordered_ids)
            clauses.append(f"npc_id IN ({placeholders})")
            params.extend(ordered_ids)
        if turn_index is not None:
            clauses.append("turn_index = ?")
            params.append(turn_index)
        sql = "SELECT * FROM npc_field_change_logs"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY turn_index, npc_id, field_name, log_id"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return self._field_logs_from_rows(rows)

    def field_change_logs_for_evidence(
        self,
        evidence_ids: Sequence[str],
    ) -> list[NPCFieldChangeLog]:
        ordered_ids = list(dict.fromkeys(evidence_ids))
        if not ordered_ids:
            return []
        placeholders = ",".join("?" for _ in ordered_ids)
        rows = self._db.execute(
            f"""
            SELECT DISTINCT logs.*
            FROM npc_field_change_logs AS logs
            JOIN npc_field_change_evidence AS evidence
              ON evidence.log_id = logs.log_id
            WHERE evidence.evidence_id IN ({placeholders})
            ORDER BY logs.turn_index, logs.npc_id, logs.field_name, logs.log_id
            """,
            tuple(ordered_ids),
        ).fetchall()
        return self._field_logs_from_rows(rows)

    def field_change_batch_hash(self, turn_index: int) -> str | None:
        row = self._db.execute(
            """
            SELECT batch_hash FROM npc_field_change_processed_turns
            WHERE turn_index = ?
            """,
            (turn_index,),
        ).fetchone()
        return str(row["batch_hash"]) if row is not None else None

    def clear_field_change_history(self) -> None:
        with transaction(self._db):
            self._clear_field_history_rows()

    def _require_field_history_profiles_match(
        self,
        prepared: PreparedNPCFieldBatch,
    ) -> None:
        npc_ids = list(dict.fromkeys(
            change.npc_id for change in prepared.changes
        ))
        if not npc_ids:
            return
        placeholders = ",".join("?" for _ in npc_ids)
        rows = self._db.execute(
            f"SELECT * FROM npc_profiles WHERE id IN ({placeholders})",
            tuple(npc_ids),
        ).fetchall()
        known = {row["id"] for row in rows}
        unknown = sorted(set(npc_ids) - known)
        if unknown:
            raise ValueError(
                f"NPC field changes reference unknown profiles: {unknown}"
            )
        profiles = {
            row["id"]: self._row_to_profile(row)
            for row in rows
        }
        mismatched = sorted(
            f"{change.npc_id}/{change.field_name}"
            for change in prepared.changes
            if getattr(profiles[change.npc_id], change.field_name)
            != change.new_value
        )
        mismatched.extend(
            f"{change.npc_id}/last_scene_turn"
            for change in prepared.changes
            if change.field_name == "last_scene_summary"
            and profiles[change.npc_id].last_scene_turn != change.turn_index
        )
        if mismatched:
            raise ValueError(
                "NPC field changes do not match the committed profile "
                f"snapshot: {mismatched}"
            )

    def _has_other_field_history_turns(self, turn_index: int) -> bool:
        return self._db.execute(
            """
            SELECT 1 FROM npc_field_change_processed_turns
            WHERE turn_index != ? LIMIT 1
            """,
            (turn_index,),
        ).fetchone() is not None

    def _clear_field_history_rows(self) -> None:
        self._db.execute("DELETE FROM npc_field_change_logs")
        self._db.execute("DELETE FROM npc_field_change_processed_turns")

    def _insert_prepared_field_batch(
        self,
        prepared: PreparedNPCFieldBatch,
    ) -> None:
        for change in prepared.changes:
            cursor = self._db.execute(
                """
                INSERT INTO npc_field_change_logs(
                    npc_id, field_name, turn_index, old_value, new_value,
                    change_basis, summary_delta, source
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    change.npc_id,
                    change.field_name,
                    change.turn_index,
                    change.old_value,
                    change.new_value,
                    change.change_basis,
                    change.summary_delta,
                    change.source,
                ),
            )
            log_id = int(cursor.lastrowid)
            for evidence in change.evidence:
                self._db.execute(
                    """
                    INSERT INTO npc_field_change_evidence(
                        log_id, position, evidence_id, evidence_hash
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (
                        log_id,
                        evidence.position,
                        evidence.evidence_id,
                        evidence.evidence_hash,
                    ),
                )
        self._db.execute(
            """
            INSERT INTO npc_field_change_processed_turns(turn_index, batch_hash)
            VALUES (?, ?)
            """,
            (prepared.turn_index, prepared.batch_hash),
        )

    def _field_logs_from_rows(
        self,
        rows: Sequence[sqlite3.Row],
    ) -> list[NPCFieldChangeLog]:
        if not rows:
            return []
        log_ids = [int(row["log_id"]) for row in rows]
        placeholders = ",".join("?" for _ in log_ids)
        evidence_rows = self._db.execute(
            f"""
            SELECT * FROM npc_field_change_evidence
            WHERE log_id IN ({placeholders})
            ORDER BY log_id, position
            """,
            tuple(log_ids),
        ).fetchall()
        evidence_by_log: dict[int, list[NPCFieldChangeEvidence]] = {}
        for evidence in evidence_rows:
            log_id = int(evidence["log_id"])
            evidence_by_log.setdefault(log_id, []).append(
                NPCFieldChangeEvidence(
                    log_id=log_id,
                    position=int(evidence["position"]),
                    evidence_id=evidence["evidence_id"],
                    evidence_hash=evidence["evidence_hash"],
                )
            )
        return [
            NPCFieldChangeLog(
                log_id=int(row["log_id"]),
                npc_id=row["npc_id"],
                field_name=row["field_name"],
                turn_index=int(row["turn_index"]),
                old_value=row["old_value"],
                new_value=row["new_value"],
                change_basis=row["change_basis"],
                summary_delta=row["summary_delta"],
                source=row["source"],
                evidence=evidence_by_log.get(int(row["log_id"]), []),
            )
            for row in rows
        ]

    def _ensure_given_name_alias(self, profile: NPCProfile) -> None:
        given_name = profile.name.split("·", 1)[0].strip()
        if given_name == profile.name or not given_name:
            return
        known_names = {
            normalize_key(profile.name),
            *(normalize_key(alias) for alias in profile.aliases),
            *(normalize_key(alias) for alias in profile.transient_aliases),
        }
        if normalize_key(given_name) in known_names or self._name_conflicts(profile, given_name):
            return
        profile.aliases.append(given_name)

    def _name_conflicts(self, profile: NPCProfile, name: str) -> bool:
        key = normalize_key(name)
        for other in self.list_all():
            if other.npc_id == profile.npc_id:
                continue
            if key in {normalize_key(other.name), *[normalize_key(alias) for alias in other.aliases]}:
                return True
        return False

    def close(self) -> None:
        if self._owns_connection:
            self._db.close()

    def __enter__(self) -> "NPCProfileStore":
        return self

    def __exit__(self, *args) -> None:
        self.close()


def _name_matches(term: str, profile: NPCProfile) -> bool:
    return (
        term == normalize_key(profile.name)
        or term in {normalize_key(alias) for alias in profile.aliases}
        or term in {normalize_key(alias) for alias in profile.transient_aliases}
    )


def _find_alias(profile: NPCProfile, surface: str) -> str | None:
    """按 identity 同一规范化（compact）匹配永久 alias，返回 verbatim alias。"""
    key = normalize_compact_key(surface)
    for alias in profile.aliases:
        if normalize_compact_key(alias) == key:
            return alias
    return None
