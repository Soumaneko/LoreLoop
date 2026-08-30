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
    resolve_application_paths,
    resolve_data_root,
    validate_resource_paths,
    validate_debug_resource_paths,
)
from app.runtime.model_config import MODEL_CONFIG_FILE
from app.runtime.reader_preferences import READER_PREFERENCES_FILE
from app.runtime.environment import load_source_dotenv


def test_data_root_override_is_absolute_and_has_priority(tmp_path):
    root, overridden = resolve_data_root({DATA_DIR_ENV: str(tmp_path)})

    assert root == tmp_path.resolve()
    assert overridden is True


def test_relative_data_root_override_is_rejected():
    with pytest.raises(RuntimeError, match="must be an absolute path"):
        resolve_data_root({DATA_DIR_ENV: "relative/data"})


def test_dotenv_is_loaded_only_for_source_mode():
    calls = []

    assert load_source_dotenv(
        frozen=False,
        loader=lambda: calls.append("loaded") or True,
    )
    assert calls == ["loaded"]

    assert not load_source_dotenv(
        frozen=True,
        loader=lambda: calls.append("must-not-load") or True,
    )
    assert calls == ["loaded"]


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


def test_desktop_resource_validation_does_not_require_debug_assets(tmp_path):
    paths = resolve_application_paths({DATA_DIR_ENV: str(tmp_path)})
    desktop_paths = replace(
        paths,
        debug_static_dir=tmp_path / "missing-debug-static",
    )

    validate_resource_paths(desktop_paths)
    with pytest.raises(RuntimeError, match="debug resources are missing"):
        validate_debug_resource_paths(desktop_paths)
















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
