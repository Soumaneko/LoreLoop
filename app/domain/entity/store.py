import json
import sqlite3

from app.domain.entity.schemas import (
    EntityProfile,
    EntityProfileUpdateInput,
    EntityType,
    profile_from_semantic,
    utc_now,
)


class EntityStore:
    def __init__(self, db_path: str = "data/entities.db"):
        self._db_path = db_path
        self._db = sqlite3.connect(db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_profiles (
                id TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                profile_json TEXT NOT NULL,
                source_scene_ids_json TEXT NOT NULL DEFAULT '[]',
                source_memory_ids_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.5,
                updated_at TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                name_hit_count INTEGER NOT NULL DEFAULT 1,
                alias_hit_counts_json TEXT NOT NULL DEFAULT '{}'
            )
            """
        )
        self._ensure_column(
            "entity_profiles",
            "name_hit_count",
            "INTEGER NOT NULL DEFAULT 1",
        )
        self._ensure_column(
            "entity_profiles",
            "alias_hit_counts_json",
            "TEXT NOT NULL DEFAULT '{}'",
        )
        self._db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_entity_profiles_type_name
            ON entity_profiles(active, entity_type, name)
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_memory_links (
                entity_id TEXT NOT NULL,
                memory_id TEXT NOT NULL,
                PRIMARY KEY (entity_id, memory_id)
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS entity_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        self._db.commit()

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        rows = self._db.execute(f"PRAGMA table_info({table})").fetchall()
        if column in {str(row["name"]) for row in rows}:
            return
        self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def _row_to_profile(self, row: sqlite3.Row) -> EntityProfile:
        semantic = EntityProfileUpdateInput.model_validate(json.loads(row["profile_json"]))
        return profile_from_semantic(
            semantic,
            entity_id=row["id"],
            source_scene_ids=json.loads(row["source_scene_ids_json"]),
            source_memory_ids=json.loads(row["source_memory_ids_json"]),
            confidence=row["confidence"],
            updated_at=row["updated_at"],
            active=bool(row["active"]),
            name_hit_count=int(row["name_hit_count"]),
            alias_hit_counts=json.loads(row["alias_hit_counts_json"] or "{}"),
        )

    def upsert(self, profile: EntityProfile) -> str:
        self._maybe_promote_alias(profile)
        now = profile.updated_at or utc_now()
        semantic = profile.semantic_view()
        self._db.execute(
            """
            INSERT INTO entity_profiles (
                id, entity_type, name, aliases_json, profile_json,
                source_scene_ids_json, source_memory_ids_json,
                confidence, updated_at, active, name_hit_count, alias_hit_counts_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                entity_type = excluded.entity_type,
                name = excluded.name,
                aliases_json = excluded.aliases_json,
                profile_json = excluded.profile_json,
                source_scene_ids_json = excluded.source_scene_ids_json,
                source_memory_ids_json = excluded.source_memory_ids_json,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at,
                active = excluded.active,
                name_hit_count = excluded.name_hit_count,
                alias_hit_counts_json = excluded.alias_hit_counts_json
            """,
            (
                profile.entity_id,
                profile.entity_type,
                profile.name,
                json.dumps(profile.aliases, ensure_ascii=False),
                semantic.model_dump_json(),
                json.dumps(profile.source_scene_ids, ensure_ascii=False),
                json.dumps(profile.source_memory_ids, ensure_ascii=False),
                profile.confidence,
                now,
                int(profile.active),
                profile.name_hit_count,
                json.dumps(profile.alias_hit_counts, ensure_ascii=False),
            ),
        )
        self._db.commit()
        return profile.entity_id

    def get_by_id(self, entity_id: str) -> EntityProfile | None:
        row = self._db.execute(
            "SELECT * FROM entity_profiles WHERE id = ?",
            (entity_id,),
        ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def get_by_name(self, name: str) -> EntityProfile | None:
        term = _norm(name)
        if not term:
            return None
        for profile in self.list_all():
            if _name_matches(term, profile):
                return profile
        return None

    def find_by_name_fuzzy(self, name: str) -> list[EntityProfile]:
        term = _norm(name)
        if not term:
            return []
        scored = []
        for profile in self.list_all():
            score = _match_score(term, profile.name, profile.aliases)
            if score > 0:
                scored.append((score, profile.confidence, profile.updated_at, profile))
        scored.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
        return [profile for _score, _confidence, _updated_at, profile in scored]

    def list_all(
        self,
        *,
        entity_type: EntityType | None = None,
        active_only: bool = True,
    ) -> list[EntityProfile]:
        sql = "SELECT * FROM entity_profiles"
        clauses = []
        params: list[str | int] = []
        if active_only:
            clauses.append("active = 1")
        if entity_type is not None:
            clauses.append("entity_type = ?")
            params.append(entity_type)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC, name ASC"
        rows = self._db.execute(sql, tuple(params)).fetchall()
        return [self._row_to_profile(row) for row in rows]

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) FROM entity_profiles").fetchone()
        return int(row[0])

    def link_memory(self, entity_id: str, memory_id: str) -> bool:
        cursor = self._db.execute(
            """
            INSERT OR IGNORE INTO entity_memory_links(entity_id, memory_id)
            VALUES (?, ?)
            """,
            (entity_id, memory_id),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def get_linked_memory_ids(self, entity_id: str) -> list[str]:
        rows = self._db.execute(
            """
            SELECT memory_id FROM entity_memory_links
            WHERE entity_id = ?
            ORDER BY memory_id ASC
            """,
            (entity_id,),
        ).fetchall()
        return [str(row["memory_id"]) for row in rows]

    def append_source_memory(self, entity_id: str, memory_id: str) -> None:
        profile = self.get_by_id(entity_id)
        if profile is None:
            return
        if memory_id not in profile.source_memory_ids:
            profile.source_memory_ids.append(memory_id)
        profile.updated_at = utc_now()
        self.upsert(profile)

    def append_alias(
        self,
        entity_id: str,
        alias: str,
        *,
        initial_hit_count: int = 0,
    ) -> bool:
        profile = self.get_by_id(entity_id)
        clean = str(alias).strip()
        if profile is None or not clean:
            return False
        alias_keys = {existing.casefold() for existing in profile.aliases}
        if clean.casefold() in alias_keys or clean.casefold() == profile.name.casefold():
            return False
        profile.aliases.append(clean)
        if initial_hit_count > 0:
            profile.alias_hit_counts[clean] = max(
                profile.alias_hit_counts.get(clean, 0),
                initial_hit_count,
            )
        self._maybe_promote_alias(profile)
        profile.updated_at = utc_now()
        self.upsert(profile)
        return True

    def record_mention(self, entity_id: str, surface: str) -> bool:
        profile = self.get_by_id(entity_id)
        clean = str(surface).strip()
        if profile is None or not clean:
            return False

        if clean.casefold() == profile.name.casefold():
            profile.name_hit_count += 1
        else:
            alias = _find_alias(profile, clean)
            if alias is None:
                return False
            profile.alias_hit_counts[alias] = profile.alias_hit_counts.get(alias, 0) + 1

        self._maybe_promote_alias(profile)
        profile.updated_at = utc_now()
        self.upsert(profile)
        return True

    def _maybe_promote_alias(self, profile: EntityProfile) -> None:
        candidates = []
        for alias in profile.aliases:
            count = profile.alias_hit_counts.get(alias, 0)
            if count > profile.name_hit_count:
                candidates.append((count, alias))
        if not candidates:
            return

        candidates.sort(key=lambda item: (item[0], item[1]), reverse=True)
        promoted_alias = candidates[0][1]
        if self._name_conflicts(profile, promoted_alias):
            return

        old_name = profile.name
        old_name_count = profile.name_hit_count
        promoted_count = profile.alias_hit_counts.pop(promoted_alias, 0)
        profile.name = promoted_alias
        profile.name_hit_count = promoted_count
        profile.aliases = _merge_unique([
            old_name,
            *[alias for alias in profile.aliases if alias.casefold() != promoted_alias.casefold()],
        ])
        profile.alias_hit_counts[old_name] = max(
            profile.alias_hit_counts.get(old_name, 0),
            old_name_count,
        )

    def _name_conflicts(self, profile: EntityProfile, name: str) -> bool:
        key = _norm(name)
        for other in self.list_all(entity_type=profile.entity_type):
            if other.entity_id != profile.entity_id and _norm(other.name) == key:
                return True
        return False

    def close(self) -> None:
        self._db.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def _norm(value: str) -> str:
    return str(value).strip().casefold()


def _name_matches(term: str, profile: EntityProfile) -> bool:
    names = [profile.name, *profile.aliases]
    return any(term == _norm(name) for name in names)


def _find_alias(profile: EntityProfile, surface: str) -> str | None:
    key = surface.casefold()
    for alias in profile.aliases:
        if alias.casefold() == key:
            return alias
    return None


def _merge_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for value in values:
        clean = str(value).strip()
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            merged.append(clean)
    return merged


def _match_score(memory_entity: str, profile_name: str, aliases: list[str]) -> float:
    me = memory_entity.strip()
    pn = profile_name.strip()
    if not me or not pn:
        return 0.0
    if me == pn or me.casefold() == pn.casefold():
        return 1.0
    for alias in aliases:
        a = alias.strip()
        if a and (me == a or me.casefold() == a.casefold()):
            return 0.9
    if me in pn:
        return 0.7 + (len(me) / len(pn)) * 0.2
    if pn in me:
        return 0.7 + (len(pn) / len(me)) * 0.2
    for alias in aliases:
        a = alias.strip()
        if not a:
            continue
        if me in a:
            return 0.6 + (len(me) / len(a)) * 0.1
        if a in me:
            return 0.6 + (len(a) / len(me)) * 0.1
    return 0.0
