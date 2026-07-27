from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.diagnostics import DiagnosticLogger
from app.core.llm.prompts import PromptLoader
from app.core.llm.tracing import TraceLogger
from app.runtime import app_paths
from app.runtime.app_paths import (
    APP_NAME,
    DATA_DIR_ENV,
    ensure_user_directories,
    migrate_legacy_user_data,
    resolve_application_paths,
    resolve_data_root,
    validate_resource_paths,
)
from app.runtime.model_config import MODEL_CONFIG_FILE
from app.runtime.reader_preferences import READER_PREFERENCES_FILE


def test_data_root_override_is_absolute_and_has_priority(tmp_path):
    root, overridden = resolve_data_root({DATA_DIR_ENV: str(tmp_path)})

    assert root == tmp_path.resolve()
    assert overridden is True


def test_relative_data_root_override_is_rejected():
    with pytest.raises(RuntimeError, match="must be an absolute path"):
        resolve_data_root({DATA_DIR_ENV: "relative/data"})


def test_platform_default_uses_single_loreloop_directory(monkeypatch, tmp_path):
    calls = []

    def fake_user_data_path(**kwargs):
        calls.append(kwargs)
        return tmp_path / APP_NAME

    monkeypatch.setattr(app_paths, "user_data_path", fake_user_data_path)

    root, overridden = resolve_data_root({})

    assert root == tmp_path / APP_NAME
    assert overridden is False
    assert calls == [{
        "appname": "LoreLoop",
        "appauthor": False,
        "roaming": False,
    }]


def test_application_paths_separate_resources_and_user_data(tmp_path):
    paths = resolve_application_paths({DATA_DIR_ENV: str(tmp_path)})

    assert paths.saves_dir == tmp_path / "saves"
    assert paths.config_dir == tmp_path / "config"
    assert paths.logs_dir == tmp_path / "logs"
    assert paths.debug_prompts_dir == tmp_path / "debug_prompts"
    assert paths.webview_dir == tmp_path / "webview"
    assert paths.prompts_dir.name == "prompts"
    assert paths.player_static_dir.name == "player_static"
    assert paths.debug_static_dir.name == "debug_static"
    assert not paths.prompts_dir.is_relative_to(paths.data_root)
    validate_resource_paths(paths)


def test_ensure_user_directories_creates_only_user_layout(tmp_path):
    paths = resolve_application_paths({DATA_DIR_ENV: str(tmp_path)})

    ensure_user_directories(paths)

    for name in ("saves", "config", "logs", "webview"):
        assert (tmp_path / name).is_dir()
    assert not (tmp_path / "prompts").exists()
    assert not (tmp_path / "player_static").exists()


def test_legacy_migration_copies_without_deleting_source(tmp_path):
    legacy_root = tmp_path / "legacy"
    destination = tmp_path / "destination"
    save_id = str(uuid4())
    save_dir = legacy_root / "saves" / save_id
    save_dir.mkdir(parents=True)
    (save_dir / "core_memory.json").write_text("{}", encoding="utf-8")
    (legacy_root / "saves" / ".active_save").write_text(
        save_id,
        encoding="utf-8",
    )
    legacy_config = legacy_root / "saves" / ".app"
    legacy_config.mkdir()
    (legacy_config / "model_config.json").write_text(
        '{"model":"test"}',
        encoding="utf-8",
    )
    (legacy_config / "reader_preferences.json").write_text(
        '{"theme":"dark"}',
        encoding="utf-8",
    )
    legacy_logs = legacy_root / "logs"
    legacy_logs.mkdir()
    (legacy_logs / "llm_traces.jsonl").write_text(
        '{"trace_id":"old"}\n',
        encoding="utf-8",
    )
    paths = replace(
        resolve_application_paths({DATA_DIR_ENV: str(destination)}),
        data_dir_overridden=False,
    )

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=False,
    )

    assert result == "migrated"
    assert (destination / "saves" / save_id / "core_memory.json").is_file()
    assert (destination / "config" / "active_save").read_text() == save_id
    assert (destination / "config" / "model_config.json").is_file()
    assert (destination / "config" / "reader_preferences.json").is_file()
    assert (destination / "config" / "migration-v1.json").is_file()
    assert (destination / "logs" / "llm_traces.jsonl").is_file()
    assert (legacy_root / "saves" / save_id / "core_memory.json").is_file()
    assert (legacy_root / "saves" / ".app" / "model_config.json").is_file()


def test_legacy_migration_never_runs_for_explicit_override(tmp_path):
    legacy_root = tmp_path / "legacy"
    (legacy_root / "saves").mkdir(parents=True)
    paths = resolve_application_paths({
        DATA_DIR_ENV: str(tmp_path / "destination")
    })

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=False,
    )

    assert result == "skipped_override"
    assert not paths.data_root.exists()


def test_legacy_migration_never_runs_from_frozen_bundle(tmp_path):
    legacy_root = tmp_path / "legacy"
    (legacy_root / "saves").mkdir(parents=True)
    paths = replace(
        resolve_application_paths({
            DATA_DIR_ENV: str(tmp_path / "destination")
        }),
        data_dir_overridden=False,
    )

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=True,
    )

    assert result == "skipped_frozen"
    assert not paths.data_root.exists()


def test_legacy_migration_does_not_merge_into_nonempty_destination(tmp_path):
    legacy_root = tmp_path / "legacy"
    (legacy_root / "saves").mkdir(parents=True)
    destination = tmp_path / "destination"
    destination.mkdir()
    sentinel = destination / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")
    paths = replace(
        resolve_application_paths({DATA_DIR_ENV: str(destination)}),
        data_dir_overridden=False,
    )

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=False,
    )

    assert result == "skipped_destination_exists"
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_legacy_migration_does_not_remove_existing_empty_destination(tmp_path):
    legacy_root = tmp_path / "legacy"
    save_id = str(uuid4())
    (legacy_root / "saves" / save_id).mkdir(parents=True)
    destination = tmp_path / "destination"
    destination.mkdir()
    paths = replace(
        resolve_application_paths({DATA_DIR_ENV: str(destination)}),
        data_dir_overridden=False,
    )

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=False,
    )

    assert result == "skipped_destination_exists"
    assert destination.is_dir()
    assert not list(destination.iterdir())


def test_legacy_migration_yields_to_concurrent_destination_writer(
    tmp_path,
    monkeypatch,
):
    legacy_root = tmp_path / "legacy"
    save_id = str(uuid4())
    (legacy_root / "saves" / save_id).mkdir(parents=True)
    destination = tmp_path / "destination"
    paths = replace(
        resolve_application_paths({DATA_DIR_ENV: str(destination)}),
        data_dir_overridden=False,
    )

    def concurrent_replace(_source, target):
        Path(target).mkdir()
        (Path(target) / "other-process.txt").write_text(
            "keep",
            encoding="utf-8",
        )
        raise OSError("destination appeared")

    monkeypatch.setattr(app_paths.os, "replace", concurrent_replace)

    result = migrate_legacy_user_data(
        paths,
        legacy_project_root=legacy_root,
        frozen=False,
    )

    assert result == "skipped_destination_race"
    assert (destination / "other-process.txt").read_text() == "keep"
    assert not list(tmp_path.glob(".destination-migration-*"))


def test_failed_legacy_migration_keeps_source_and_removes_stage(
    tmp_path,
    monkeypatch,
):
    legacy_root = tmp_path / "legacy"
    save_id = str(uuid4())
    source = legacy_root / "saves" / save_id
    source.mkdir(parents=True)
    sentinel = source / "keep.db"
    sentinel.write_text("source", encoding="utf-8")
    destination = tmp_path / "destination"
    paths = replace(
        resolve_application_paths({DATA_DIR_ENV: str(destination)}),
        data_dir_overridden=False,
    )

    def fail_copy(*_args, **_kwargs):
        raise OSError("copy failed")

    monkeypatch.setattr(app_paths.shutil, "copytree", fail_copy)

    with pytest.raises(RuntimeError, match="Failed to copy legacy LoreLoop data"):
        migrate_legacy_user_data(
            paths,
            legacy_project_root=legacy_root,
            frozen=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "source"
    assert not destination.exists()
    assert not list(tmp_path.glob(".destination-migration-*"))


def test_default_resource_and_log_consumers_use_registered_paths():
    assert PromptLoader().prompts_dir == app_paths.APP_PATHS.prompts_dir
    assert (
        Path(TraceLogger().log_path)
        == app_paths.APP_PATHS.logs_dir / "llm_traces.jsonl"
    )
    assert (
        Path(DiagnosticLogger().log_path)
        == app_paths.APP_PATHS.logs_dir / "diagnostics.jsonl"
    )
    assert MODEL_CONFIG_FILE == app_paths.APP_PATHS.config_dir / "model_config.json"
    assert (
        READER_PREFERENCES_FILE
        == app_paths.APP_PATHS.config_dir / "reader_preferences.json"
    )
