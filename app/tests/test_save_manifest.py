import json
import sqlite3
from datetime import datetime, timezone
from uuid import UUID

import pytest

import app.runtime.save_manifest as save_manifest_module
from app.runtime.save_manifest import (
    SAVE_MANIFEST_FILE,
    SaveManifestRepository,
    load_save_metadata,
)


def test_manifest_create_load_rename_and_touch_preserve_identity(tmp_path):
    save_dir = tmp_path / "opaque-directory-id"
    created = datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc)
    played = datetime(2026, 2, 3, 4, 5, tzinfo=timezone.utc)
    repository = SaveManifestRepository(save_dir)

    manifest = repository.create("  第一幕  ", created_at=created)

    assert UUID(manifest.save_id).version == 4
    assert manifest.display_name == "第一幕"
    assert manifest.format_version == 1
    assert manifest.created_at == "2026-01-02T03:04:00+00:00"
    assert manifest.last_played_at == manifest.created_at

    renamed = repository.rename("第二幕")
    touched = repository.touch(played_at=played)

    assert renamed.save_id == manifest.save_id
    assert renamed.created_at == manifest.created_at
    assert renamed.last_played_at == manifest.last_played_at
    assert touched.save_id == manifest.save_id
    assert touched.display_name == "第二幕"
    assert touched.created_at == manifest.created_at
    assert touched.last_played_at == "2026-02-03T04:05:00+00:00"
    assert repository.load() == touched


def test_display_name_validation_does_not_determine_manifest_directory(tmp_path):
    save_dir = tmp_path / "03b25f4e-cfe3-482a-bba2-cb45e37d1f66"
    repository = SaveManifestRepository(save_dir)
    repository.create("可展示的名称")

    assert repository.path == save_dir / SAVE_MANIFEST_FILE
    assert not (tmp_path / "可展示的名称").exists()
    with pytest.raises(ValueError):
        repository.rename("../escape")
    assert repository.load().display_name == "可展示的名称"


def test_create_refuses_to_replace_existing_manifest(tmp_path):
    repository = SaveManifestRepository(tmp_path / "save")
    original = repository.create("original")

    with pytest.raises(FileExistsError):
        repository.create("replacement")

    assert repository.load() == original


def test_failed_atomic_replace_preserves_previous_manifest(monkeypatch, tmp_path):
    repository = SaveManifestRepository(tmp_path / "save")
    original = repository.create("original")

    def fail_replace(source, destination):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(save_manifest_module.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated"):
        repository.rename("replacement")

    payload = json.loads(repository.path.read_text(encoding="utf-8"))
    assert payload == original.as_dict()
    assert list(repository.save_dir.glob("*.tmp")) == []


@pytest.mark.parametrize(
    "change",
    [
        {"format_version": 2},
        {"save_id": "not-a-uuid"},
        {"created_at": "not-a-date"},
        {"last_played_at": "2026-01-01T00:00:00"},
    ],
)
def test_load_rejects_invalid_manifest(change, tmp_path):
    repository = SaveManifestRepository(tmp_path / "save")
    manifest = repository.create("valid")
    payload = manifest.as_dict()
    payload.update(change)
    repository.path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        repository.load()


def test_load_save_metadata_reads_state_and_confirmed_turn_count(tmp_path):
    state_db = tmp_path / "current_state.db"
    with sqlite3.connect(state_db) as connection:
        connection.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO current_state VALUES (1, ?)",
            (
                json.dumps(
                    {
                        "player": {"name": " 沈砚 "},
                        "scene": {"location": " 洛阳城 "},
                    },
                    ensure_ascii=False,
                ),
            ),
        )

    short_term_db = tmp_path / "short_term.db"
    with sqlite3.connect(short_term_db) as connection:
        connection.execute(
            "CREATE TABLE short_term_scenes "
            "(id INTEGER PRIMARY KEY, turn_index INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO short_term_scenes(turn_index) VALUES (?)",
            [(1,), (3,), (8,)],
        )

    metadata = load_save_metadata(tmp_path)

    assert metadata.protagonist_name == "沈砚"
    assert metadata.location == "洛阳城"
    assert metadata.turn_count == 3


def test_load_save_metadata_does_not_create_missing_databases(tmp_path):
    metadata = load_save_metadata(tmp_path)

    assert metadata.protagonist_name is None
    assert metadata.location is None
    assert metadata.turn_count == 0
    assert not (tmp_path / "current_state.db").exists()
    assert not (tmp_path / "short_term.db").exists()


def test_load_save_metadata_handles_uninitialized_and_corrupt_databases(tmp_path):
    # A valid SQLite file without application tables must remain untouched.
    current_state_db = tmp_path / "current_state.db"
    with sqlite3.connect(current_state_db) as connection:
        connection.execute("CREATE TABLE unrelated (value TEXT)")
    before = current_state_db.read_bytes()
    (tmp_path / "short_term.db").write_bytes(b"not sqlite")

    metadata = load_save_metadata(tmp_path)

    assert metadata.protagonist_name is None
    assert metadata.location is None
    assert metadata.turn_count == 0
    assert current_state_db.read_bytes() == before


def test_state_corruption_does_not_hide_valid_turn_count(tmp_path):
    with sqlite3.connect(tmp_path / "current_state.db") as connection:
        connection.execute(
            "CREATE TABLE current_state "
            "(id INTEGER PRIMARY KEY, state_json TEXT NOT NULL)"
        )
        connection.execute("INSERT INTO current_state VALUES (1, '{broken')")
    with sqlite3.connect(tmp_path / "short_term.db") as connection:
        connection.execute("CREATE TABLE short_term_scenes (id INTEGER)")
        connection.executemany(
            "INSERT INTO short_term_scenes VALUES (?)", [(1,), (2,)]
        )

    metadata = load_save_metadata(tmp_path)

    assert metadata.protagonist_name is None
    assert metadata.location is None
    assert metadata.turn_count == 2
