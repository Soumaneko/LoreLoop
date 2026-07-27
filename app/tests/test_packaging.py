import ast
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from app import desktop
from app.runtime.webview2 import (
    WEBVIEW2_CLIENT_GUID,
    WebView2RuntimeMissingError,
    require_webview2_runtime,
    webview2_runtime_version,
)


ROOT = Path(__file__).resolve().parents[2]


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


def test_windows_packaging_contract_is_onedir_player_only():
    spec_path = ROOT / "packaging" / "loreloop.spec"
    spec_source = spec_path.read_text(encoding="utf-8")
    ast.parse(spec_source)

    assert '"app.debug_server"' in spec_source
    assert '"app/player_static"' in spec_source
    assert '"app/prompts"' in spec_source
    assert "app/debug_static" not in spec_source
    assert '".pytest_cache" not in path.parts' in spec_source
    assert 'console=False' in spec_source
    assert 'contents_directory="_internal"' in spec_source
    assert 'name="LoreLoop"' in spec_source


def test_windows_build_dependencies_and_release_assets_are_fixed():
    desktop_requirements = (
        ROOT / "requirements-desktop.txt"
    ).read_text(encoding="utf-8")
    build_requirements = (
        ROOT / "requirements-build-windows.txt"
    ).read_text(encoding="utf-8")
    build_lock = (
        ROOT / "requirements-build-windows.lock"
    ).read_text(encoding="utf-8")
    script = (
        ROOT / "scripts" / "build_windows.ps1"
    ).read_text(encoding="utf-8")
    version_info = (
        ROOT / "packaging" / "windows" / "version_info.txt"
    ).read_text(encoding="utf-8")
    executable_config = (
        ROOT / "packaging" / "windows" / "LoreLoop.exe.config"
    ).read_text(encoding="utf-8")
    ast.parse(version_info)

    assert ">=" not in desktop_requirements
    assert "pywebview==6.2.1" in desktop_requirements
    assert 'pythonnet==3.1.0; sys_platform == "win32"' in desktop_requirements
    assert "pyinstaller==6.21.0" in build_requirements
    assert "pytest==9.0.3" in build_requirements
    for requirement in (
        "pydantic==2.13.4",
        "openai==2.41.0",
        "python-dotenv==1.2.2",
        "langgraph==1.2.8",
        "platformdirs==4.11.0",
        "pywebview==6.2.1",
        "pythonnet==3.1.0",
        "pytest==9.0.3",
        "pyinstaller==6.21.0",
    ):
        assert requirement in build_lock
    assert "--hash=sha256:" in build_lock
    assert "--require-hashes" in script
    assert "requirements-build-windows.lock" in script
    assert "packaging\\loreloop.spec" in script
    assert "MicrosoftEdgeWebview2Setup.exe" in script
    assert "Get-AuthenticodeSignature -LiteralPath" in script
    assert '"$($Signature.Status)" -ne "Valid"' in script
    assert "O=Microsoft Corporation" in script
    assert "SHA256SUMS.txt" in script
    assert "Compress-Archive" in script
    assert "Get-FileHash -Algorithm SHA256" in script
    assert "LoreLoop.exe.config" in script
    assert "Python.Runtime.dll" in script
    assert 'Stream "Zone.Identifier"' in script
    assert "--check-winforms-runtime" in script
    assert "Unblock-File -LiteralPath" in script
    assert '<loadFromRemoteSources enabled="true"/>' in executable_config
    assert "FileVersion', '0.1.0" in version_info
    assert "ProductName', 'LoreLoop（续章）" in version_info


def test_github_windows_build_is_manual_read_only_and_pinned():
    workflow = (
        ROOT / ".github" / "workflows" / "build-windows.yml"
    ).read_text(encoding="utf-8")
    script = (
        ROOT / "scripts" / "build_windows.ps1"
    ).read_text(encoding="utf-8")
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert workflow.count("workflow_dispatch:") == 1
    assert "\n  push:" not in workflow
    assert "\n  pull_request:" not in workflow
    assert "permissions:\n  contents: read" in workflow
    assert "runs-on: windows-2025" in workflow
    assert "timeout-minutes: 45" in workflow
    assert "secrets." not in workflow
    assert "retention-days: 7" in workflow
    assert "if-no-files-found: error" in workflow
    assert 'steps.python.outputs.python-path' in workflow
    assert '-PythonExecutable' in workflow

    action_refs = re.findall(r"uses:\s+([^\s#]+)", workflow)
    assert len(action_refs) == 3
    assert all(
        re.fullmatch(r"actions/[a-z-]+@[0-9a-f]{40}", reference)
        for reference in action_refs
    )
    assert "[string]$PythonExecutable" in script
    assert '$BaseRuntime.Trim() -ne "3.11|64bit"' in script
    for ignored in (
        "/.venv-build-windows/",
        "/build/",
        "/dist/",
        "/release/",
    ):
        assert ignored in gitignore
