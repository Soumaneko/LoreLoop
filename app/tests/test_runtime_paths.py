import json
import os
import subprocess
import sys
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.runtime.paths import CampaignPaths, resolve_active_save_id
from app.runtime.saves import SaveManager
from app.runtime.session import CampaignRuntime


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_save(root: Path, save_id: str, last_played: str) -> Path:
    path = root / save_id
    path.mkdir()
    (path / "save_manifest.json").write_text(
        json.dumps({
            "format_version": 1,
            "save_id": save_id,
            "display_name": save_id,
            "created_at": "2026-01-01T00:00:00+00:00",
            "last_played_at": last_played,
        }),
        encoding="utf-8",
    )
    return path


def test_importing_runtime_modules_has_no_side_effects(tmp_path):
    code = (
        "import sys; sys.path.insert(0, %r); "
        "import app.runtime.paths, app.runtime.session, app.runtime.saves"
    ) % str(REPO_ROOT)
    env = dict(os.environ)
    env["LORELOOP_DATA_DIR"] = str(tmp_path / "user-data")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    user_data = tmp_path / "user-data"
    assert not user_data.exists() or not any(user_data.iterdir())


def test_resolve_prefers_persisted_active_without_rewriting(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    save_id = str(uuid4())
    _make_save(root, save_id, "2026-01-01T00:00:00+00:00")
    active = root / "active_save"
    active.write_text(save_id, encoding="utf-8")

    assert resolve_active_save_id(root=root, active_file=active) == save_id
    mtime = active.stat().st_mtime_ns
    resolve_active_save_id(root=root, active_file=active)
    assert active.stat().st_mtime_ns == mtime


def test_resolve_falls_back_to_most_recent_when_active_is_stale(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    older, newer = str(uuid4()), str(uuid4())
    _make_save(root, older, "2026-01-01T00:00:00+00:00")
    _make_save(root, newer, "2026-01-02T00:00:00+00:00")
    active = root / "active_save"
    active.write_text(str(uuid4()), encoding="utf-8")

    assert resolve_active_save_id(root=root, active_file=active) == newer
    assert active.read_text(encoding="utf-8").strip() == newer


def test_resolve_ignores_staging_directories(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    save_id = str(uuid4())
    _make_save(root, save_id, "2026-01-01T00:00:00+00:00")
    (root / ".copy-abc").mkdir()

    resolved = resolve_active_save_id(root=root, active_file=root / "active_save")
    assert resolved == save_id


def test_resolve_creates_new_uuid_when_no_saves_exist(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    active = root / "active_save"

    save_id = resolve_active_save_id(root=root, active_file=active)
    assert str(UUID(save_id)) == save_id
    assert (root / save_id).is_dir()
    assert active.read_text(encoding="utf-8").strip() == save_id


def test_resolve_recent_policy_raises_when_no_saves_exist(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()

    with pytest.raises(RuntimeError, match="No local save is available"):
        resolve_active_save_id(
            root=root,
            active_file=root / "active_save",
            policy="recent",
        )


def test_save_manager_and_resolution_agree_on_stale_active(tmp_path):
    root = tmp_path / "saves"
    root.mkdir()
    older, newer = str(uuid4()), str(uuid4())
    _make_save(root, older, "2026-01-01T00:00:00+00:00")
    _make_save(root, newer, "2026-01-02T00:00:00+00:00")
    (root / ".active_save").write_text(str(uuid4()), encoding="utf-8")

    runtime = CampaignRuntime(
        "test",
        paths=CampaignPaths.from_data_dir(tmp_path / "campaign"),
        max_workers=1,
    )
    manager = SaveManager(root=root, runtime=runtime)
    try:
        assert manager.active_id == newer
        assert (
            resolve_active_save_id(
                root=root,
                active_file=root / ".active_save",
            )
            == newer
        )
    finally:
        runtime.close()


def test_concurrent_first_runtime_access_yields_single_instance(tmp_path):
    code = f"""import sys, threading
sys.path.insert(0, {str(REPO_ROOT)!r})
from app.runtime.session import get_campaign_runtime
results = []
barrier = threading.Barrier(16)
def worker():
    barrier.wait()
    results.append(get_campaign_runtime())
threads = [threading.Thread(target=worker) for _ in range(16)]
for thread in threads:
    thread.start()
for thread in threads:
    thread.join()
assert len({{id(runtime) for runtime in results}}) == 1
print("single")
"""
    env = dict(os.environ)
    env["LORELOOP_DATA_DIR"] = str(tmp_path / "user-data")
    result = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_custom_root_resolution_never_touches_global_user_data(tmp_path):
    code = f"""import sys
sys.path.insert(0, {str(REPO_ROOT)!r})
from pathlib import Path
from app.runtime.app_paths import APP_PATHS
from app.runtime.paths import resolve_active_save_id
custom = Path(sys.argv[1])
custom.mkdir(parents=True, exist_ok=True)
save_id = resolve_active_save_id(root=custom, active_file=custom / "active_save")
assert (custom / save_id).is_dir()
assert (custom / "active_save").read_text(encoding="utf-8").strip() == save_id
assert not (APP_PATHS.data_root / "saves").exists(), "global saves dir created"
assert not (APP_PATHS.data_root / "config").exists(), "global config dir created"
print("ok")
"""
    env = dict(os.environ)
    env["LORELOOP_DATA_DIR"] = str(tmp_path / "user-data")
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path / "custom-root")],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    user_data = tmp_path / "user-data"
    assert not user_data.exists() or not any(user_data.iterdir())
