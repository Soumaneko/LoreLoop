import json
import sqlite3

from app.domain.npc_profile.schemas import (
    NPCProfile,
    utc_now,
)
from app.runtime.sqlite import connect_sqlite, ensure_exact_schema_version


class NPCProfileStore:
    def __init__(self, db_path: str = "data/npc_profiles.db"):
        self._db_path = db_path
        self._db = connect_sqlite(db_path)
        self._create_tables()

    def _create_tables(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS npc_profiles (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                aliases_json TEXT NOT NULL DEFAULT '[]',
                profile_json TEXT NOT NULL,
                source_scene_ids_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 0.5,
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
            "confidence",
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
        ensure_exact_schema_version(self._db, "npc_profile", 3)
        self._db.commit()

    def _row_to_profile(self, row: sqlite3.Row) -> NPCProfile:
        profile = NPCProfile.model_validate(json.loads(row["profile_json"]))
        profile.npc_id = row["id"]
        profile.source_scene_ids = json.loads(row["source_scene_ids_json"])
        profile.confidence = float(row["confidence"])
        profile.updated_at = row["updated_at"]
        profile.active = bool(row["active"])
        profile.name_hit_count = int(row["name_hit_count"])
        profile.alias_hit_counts = json.loads(
            row["alias_hit_counts_json"] or "{}"
        )
        return profile

    def upsert(self, profile: NPCProfile) -> str:
        self._ensure_given_name_alias(profile)
        now = profile.updated_at or utc_now()
        profile.updated_at = now
        self._db.execute(
            """
            INSERT INTO npc_profiles (
                id, name, aliases_json, profile_json, source_scene_ids_json,
                confidence, updated_at, active, name_hit_count, alias_hit_counts_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                aliases_json = excluded.aliases_json,
                profile_json = excluded.profile_json,
                source_scene_ids_json = excluded.source_scene_ids_json,
                confidence = excluded.confidence,
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
                profile.confidence,
                now,
                int(profile.active),
                profile.name_hit_count,
                json.dumps(profile.alias_hit_counts, ensure_ascii=False),
            ),
        )
        self._db.commit()
        return profile.npc_id

    def get_by_id(self, npc_id: str) -> NPCProfile | None:
        row = self._db.execute(
            "SELECT * FROM npc_profiles WHERE id = ?",
            (npc_id,),
        ).fetchone()
        return self._row_to_profile(row) if row is not None else None

    def delete_by_id(self, npc_id: str) -> None:
        self._db.execute(
            "DELETE FROM npc_profiles WHERE id = ?",
            (npc_id,),
        )
        self._db.commit()

    def get_by_name(self, name: str) -> NPCProfile | None:
        term = _norm(name)
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
        alias_keys = {existing.casefold() for existing in profile.aliases}
        if clean.casefold() in alias_keys or clean.casefold() == profile.name.casefold():
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
        if clean.casefold() == profile.name.casefold():
            profile.name_hit_count += 1
        else:
            alias = _find_alias(profile, clean)
            if alias is None:
                return False
            profile.alias_hit_counts[alias] = profile.alias_hit_counts.get(alias, 0) + 1
        profile.updated_at = utc_now()
        self.upsert(profile)
        return True

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> "NPCProfileStore":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _ensure_given_name_alias(self, profile: NPCProfile) -> None:
        given_name = profile.name.split("·", 1)[0].strip()
        if given_name == profile.name or not given_name:
            return
        known_names = {
            _norm(profile.name),
            *(_norm(alias) for alias in profile.aliases),
            *(_norm(alias) for alias in profile.transient_aliases),
        }
        if _norm(given_name) in known_names or self._name_conflicts(profile, given_name):
            return
        profile.aliases.append(given_name)

    def _name_conflicts(self, profile: NPCProfile, name: str) -> bool:
        key = _norm(name)
        for other in self.list_all():
            if other.npc_id == profile.npc_id:
                continue
            if key in {_norm(other.name), *[_norm(alias) for alias in other.aliases]}:
                return True
        return False


def _name_matches(term: str, profile: NPCProfile) -> bool:
    return (
        term == _norm(profile.name)
        or term in {_norm(alias) for alias in profile.aliases}
        or term in {_norm(alias) for alias in profile.transient_aliases}
    )


def _find_alias(profile: NPCProfile, surface: str) -> str | None:
    key = surface.casefold()
    for alias in profile.aliases:
        if alias.casefold() == key:
            return alias
    return None


def _norm(value: str) -> str:
    return str(value).strip().casefold()
