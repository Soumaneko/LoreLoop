import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from app.runtime.campaign_migration import ensure_campaign_database
from app.runtime.paths import CampaignPaths
from app.runtime.save_archive import SaveArchiveError, create_consistent_snapshot, extract_archive, inspect_archive
from app.runtime.saves import SaveManager
from app.runtime.session import CampaignRuntime


def _valid_paths(tmp_path: Path, name: str = "save") -> CampaignPaths:
    p = CampaignPaths.from_data_dir(tmp_path / name)
    ensure_campaign_database(p)
    return p


def test_runtime_open_rejects_legacy_journal(tmp_path):
    base = _valid_paths(tmp_path, "ok")
    rt_ok = CampaignRuntime("ok", paths=base, max_workers=1)
    rt_ok.close()
    # Create a save that contains legacy journal
    bad_dir = tmp_path / "bad"
    bad_dir.mkdir()
    bad_paths = CampaignPaths.from_data_dir(bad_dir)
    ensure_campaign_database(bad_paths)
    journal = bad_dir / ".runtime" / "director_mutation.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text("{}", encoding="utf-8")
    with pytest.raises(RuntimeError, match="legacy director_mutation"):
        CampaignRuntime("bad", paths=bad_paths, max_workers=1)
    # reconfigure from ok to bad should also fail and keep old runtime usable
    rt = CampaignRuntime("ok2", paths=_valid_paths(tmp_path, "ok2"), max_workers=1)
    try:
        with pytest.raises(RuntimeError, match="legacy director_mutation"):
            rt.reconfigure("bad", bad_paths)
        assert rt.campaign_id == "ok2"
        assert not rt.stores.is_closed
    finally:
        rt.close()


def test_save_manager_copy_and_export_reject_journal_and_cleanup(tmp_path):
    root = tmp_path / "saves"
    rt = CampaignRuntime("bootstrap", paths=CampaignPaths.from_data_dir(tmp_path / "bootstrap"), max_workers=1)
    manager = SaveManager(root, runtime=rt)
    try:
        src = manager.create("src")
        src_dir = root / src.save_id
        # Inject journal into source
        (src_dir / ".runtime" / "director_mutation.json").write_text("{}", encoding="utf-8")
        # copy should fail closed and not leave new save or .copy-* staging
        with pytest.raises(SaveArchiveError, match="director_mutation"):
            manager.copy(src.save_id)
        assert len([p for p in root.iterdir() if p.is_dir() and p.name not in {src.save_id}]) == 1  # only default + src
        assert not list(root.glob(".copy-*"))
        # export should also fail and not create archive
        dest_zip = tmp_path / "out.zip"
        with pytest.raises(SaveArchiveError, match="director_mutation"):
            manager.export_to(src.save_id, dest_zip)
        assert not dest_zip.exists()
        # Also low-level save_archive should fail
        with pytest.raises(SaveArchiveError, match="director_mutation"):
            create_consistent_snapshot(src_dir, tmp_path / "staging2")
    finally:
        rt.close()


def test_zip_with_journal_fails_and_cleans_staging(tmp_path):
    bad_zip = tmp_path / "bad.zip"
    manifest = {
        "format_version": 1,
        "files": [{"path": ".runtime/director_mutation.json", "size": 2, "sha256": "a" * 64}],
        "metadata": {},
    }
    with zipfile.ZipFile(bad_zip, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("save/.runtime/director_mutation.json", "{}")
    with pytest.raises(SaveArchiveError, match="director_mutation"):
        inspect_archive(bad_zip)
    dest = tmp_path / "staging"
    with pytest.raises(SaveArchiveError, match="director_mutation"):
        extract_archive(bad_zip, dest)
    # Staging must be cleaned (either not created or left empty)
    if dest.exists():
        assert list(dest.iterdir()) == []
    # SaveManager import should also fail and clean .import-*
    root = tmp_path / "saves2"
    rt2 = CampaignRuntime("bootstrap2", paths=CampaignPaths.from_data_dir(tmp_path / "bootstrap2"), max_workers=1)
    manager2 = SaveManager(root, runtime=rt2)
    try:
        with pytest.raises(SaveArchiveError, match="director_mutation"):
            manager2.import_from(bad_zip, "new")
        assert not list(root.glob(".import-*"))
        # No new save should have been created beyond default
        assert len([p for p in root.iterdir() if p.is_dir()]) == 1
    finally:
        rt2.close()
