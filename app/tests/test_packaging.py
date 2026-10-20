import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app import __version__, desktop
from app.version import APP_VERSION
from app.runtime.webview2 import (
    WEBVIEW2_CLIENT_GUID,
    WebView2RuntimeMissingError,
    require_webview2_runtime,
    webview2_runtime_version,
)
from scripts.generate_build_metadata import (
    build_information,
    parse_application_version,
    render_windows_version_info,
)


ROOT = Path(__file__).resolve().parents[2]


def test_model_runtime_check_initializes_real_adapters_without_starting_desktop(monkeypatch):
    import socket

    def reject_network(*args, **kwargs):
        pytest.fail("model runtime check must not open network connections")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(
        desktop, "run_desktop",
        lambda *_args: pytest.fail("model runtime check must not start desktop"),
    )
    assert desktop.main([desktop.MODEL_RUNTIME_CHECK]) == 0


class _RegistryKey:
    def __init__(self, version):
        self.version = version

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return None


class _FakeRegistry:
    HKEY_CURRENT_USER = "HKCU"
    HKEY_LOCAL_MACHINE = "HKLM"

    def __init__(self, entries=None):
        self.entries = entries or {}

    def OpenKey(self, hive, path):
        try:
            return _RegistryKey(self.entries[(hive, path)])
        except KeyError as exc:
            raise OSError("registry key not found") from exc

    @staticmethod
    def QueryValueEx(key, name):
        if name != "pv":
            raise OSError("registry value not found")
        return key.version, 1


def test_webview2_runtime_detection_reads_evergreen_registry_version():
    path = (
        "SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\"
        + WEBVIEW2_CLIENT_GUID
    )
    registry = _FakeRegistry({("HKLM", path): "135.0.3179.98"})

    assert webview2_runtime_version(
        platform="win32",
        registry=registry,
    ) == "135.0.3179.98"
    assert require_webview2_runtime(
        platform="win32",
        registry=registry,
    ) == "135.0.3179.98"


def test_webview2_runtime_detection_rejects_missing_or_zero_version():
    path = (
        "SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\"
        + WEBVIEW2_CLIENT_GUID
    )
    registry = _FakeRegistry({("HKCU", path): "0.0.0.0"})

    with pytest.raises(WebView2RuntimeMissingError, match="WebView2 Runtime"):
        require_webview2_runtime(platform="win32", registry=registry)


def test_desktop_reports_missing_webview2_without_starting_runtime(monkeypatch):
    messages = []
    monkeypatch.setattr(
        desktop,
        "require_webview2_runtime",
        lambda: (_ for _ in ()).throw(
            WebView2RuntimeMissingError("WebView2 missing")
        ),
    )
    monkeypatch.setattr(
        desktop,
        "run_desktop",
        lambda paths: pytest.fail("desktop runtime must not start"),
    )
    monkeypatch.setattr(
        desktop,
        "_show_message",
        lambda message, *, error: messages.append((message, error)),
    )

    assert desktop.main() == 2
    assert messages == [("WebView2 missing", True)]


def test_desktop_import_graph_does_not_load_debug_server(tmp_path):
    environment = os.environ.copy()
    environment["LORELOOP_DATA_DIR"] = str(tmp_path / "user-data")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.desktop; import app.player_server; "
                "assert 'app.debug_server' not in sys.modules"
            ),
        ],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_frozen_desktop_does_not_load_dotenv_from_launch_directory(tmp_path):
    poisoned_data = tmp_path / "poisoned-data"
    (tmp_path / ".env").write_text(
        "LORELOOP_DATA_DIR=" + str(poisoned_data) + "\n"
        "OPENAI_API_KEY=poisoned-key\n",
        encoding="utf-8",
    )
    environment = os.environ.copy()
    for name in (
        "LORELOOP_DATA_DIR",
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "OPENAI_THINKING",
    ):
        environment.pop(name, None)
    environment["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; sys.frozen = True; "
                "from app.runtime.app_paths import APP_PATHS; "
                "from app.core.llm.config import GatewayConfig; "
                f"assert str(APP_PATHS.data_root) != {str(poisoned_data)!r}; "
                "assert GatewayConfig.from_env().api_key == ''"
            ),
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr



def test_frozen_bundle_collects_models_dev_snapshot_and_license():
    spec = (ROOT / "packaging" / "loreloop.spec").read_text(encoding="utf-8")

    assert "models_dev_snapshot.json" in spec
    assert "models_dev_LICENSE.txt" in spec

    build_script = (ROOT / "scripts" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )
    assert r"_internal\app\runtime\models_dev_snapshot.json" in build_script
    assert r"_internal\app\runtime\models_dev_LICENSE.txt" in build_script

    snapshot = (
        ROOT / "app" / "runtime" / "models_dev_snapshot.json"
    ).read_text(encoding="utf-8")
    assert "models_dev_LICENSE.txt" in snapshot
    assert (ROOT / "app" / "runtime" / "models_dev_LICENSE.txt").exists()


def test_windows_build_lock_requires_hashes():
    build_lock = (
        ROOT / "requirements-build-windows.lock"
    ).read_text(encoding="utf-8")

    assert "--hash=sha256:" in build_lock


def test_application_version_generates_windows_and_build_metadata():
    assert APP_VERSION == "0.1.0"
    assert __version__ == APP_VERSION
    assert parse_application_version(APP_VERSION) == (0, 1, 0, 0)
    assert parse_application_version("1.2.3-alpha.7") == (1, 2, 3, 7)

    version_info = render_windows_version_info(APP_VERSION)
    ast.parse(version_info)
    assert "filevers=(0, 1, 0, 0)" in version_info
    assert "FileVersion', '0.1.0" in version_info
    assert "ProductVersion', '0.1.0" in version_info
    assert "ProductName', 'LoreLoop（续章）" in version_info

    info = build_information(
        environ={
            "GITHUB_SHA": "a" * 40,
            "GITHUB_RUN_NUMBER": "42",
        },
    )
    assert info["version"] == APP_VERSION
    assert info["commit"] == "a" * 40
    assert info["github_run_number"] == "42"
    assert info["built_at"].endswith("Z")


def test_github_workflow_pins_action_shas():
    workflow = (
        ROOT / ".github" / "workflows" / "build-windows.yml"
    ).read_text(encoding="utf-8")

    action_refs = re.findall(r"uses:\s+([^\s#]+)", workflow)
    assert len(action_refs) == 3
    assert all(
        re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", reference)
        for reference in action_refs
    )
