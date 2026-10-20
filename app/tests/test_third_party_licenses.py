from importlib.metadata import PathDistribution
from pathlib import Path

import pytest

from scripts import collect_third_party_licenses as collector


@pytest.fixture
def license_build(tmp_path, monkeypatch):
    project = tmp_path / "project"
    supplements = project / "packaging" / "licenses"
    supplements.mkdir(parents=True)
    runtime = tmp_path / "python"
    runtime.mkdir()
    (runtime / "LICENSE.txt").write_bytes(b"Python and incorporated software licenses\r\n")
    models = project / "app" / "runtime"
    models.mkdir(parents=True)
    (models / "models_dev_LICENSE.txt").write_text("models.dev MIT")
    monkeypatch.setattr(collector.sys, "base_prefix", str(runtime))
    return project, tmp_path / "package", tmp_path / "site-packages"


def distribution(site, name, files):
    info = site / f"{name}-1.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text(f"Name: {name}\nVersion: 1.0\nLicense-Expression: MIT\n")
    (info / "RECORD").write_text("".join(f"{path},,\n" for path in files))
    for path, data in files.items():
        target = site / path.replace("\\", "/")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return PathDistribution(info)


def test_collection_uses_record_ownership_and_preserves_native_and_vendor_notices(license_build, monkeypatch):
    project, package, site = license_build
    first = distribution(site, "namespace-one", {
        "shared/one.py": b"",
        "namespace-one-1.0.dist-info/licenses/LICENSE": b"MIT original\r\n",
        "namespace-one-1.0.dist-info/licenses/vendor/NOTICE": b"vendor attribution",
    })
    second = distribution(site, "namespace-two", {
        "shared/two.pyd": b"binary",
        "namespace-two-1.0.dist-info/LICENSE.txt": b"BSD original",
    })
    tool = distribution(site, "test-tool", {"test_tool.py": b""})
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [tool, second, first])
    count = collector.collect_third_party_licenses(project, package, [
        ("shared.one", str(site / "shared/one.py"), "PYMODULE"),
        ("shared/two.pyd", str(site / "shared/two.pyd"), "EXTENSION"),
    ])
    assert count == 2
    notices = (package / "THIRD_PARTY_NOTICES.txt").read_text()
    assert "namespace-one 1.0" in notices and "namespace-two 1.0" in notices
    assert "test-tool" not in notices
    assert (package / "licenses/namespace-one-1.0/installed/namespace-one-1.0.dist-info/licenses/LICENSE").read_bytes() == b"MIT original\r\n"
    assert any(p.read_bytes() == b"vendor attribution" for p in (package / "licenses").rglob("NOTICE"))
    assert any(p.read_bytes() == b"Python and incorporated software licenses\r\n" for p in (package / "licenses").rglob("LICENSE.txt"))


def test_supplement_is_copied_without_version_or_checksum_gates(license_build, monkeypatch):
    project, package, site = license_build
    dist = distribution(site, "missing", {"missing.py": b""})
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [dist])
    entries = [("missing", str(site / "missing.py"), "PYMODULE")]
    supplements = project / "packaging/licenses"
    (supplements / "missing").mkdir()
    original = b"Original upstream license"
    (supplements / "missing/LICENSE").write_bytes(original)
    assert collector.collect_third_party_licenses(project, package, entries) == 1
    assert (package / "licenses/missing-1.0/supplemental/LICENSE").read_bytes() == original
    (site / "missing-1.0.dist-info/METADATA").write_text("Name: missing\nVersion: 2.0\n")
    (supplements / "missing/LICENSE").write_bytes(b"Updated upstream license")
    assert collector.collect_third_party_licenses(project, package, entries) == 1
    assert (package / "licenses/missing-2.0/supplemental/LICENSE").read_bytes() == b"Updated upstream license"
    assert "https://pypi.org/project/missing/2.0/#files" in (package / "THIRD_PARTY_NOTICES.txt").read_text()


def test_missing_license_and_unknown_ownership_warn_without_blocking(license_build, monkeypatch, capsys):
    project, package, site = license_build
    dist = distribution(site, "missing", {"missing.py": b""})
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [dist])
    assert collector.collect_third_party_licenses(project, package, [
        ("missing", str(site / "missing.py"), "PYMODULE"),
        ("unowned", str(site / "unowned.py"), "PYMODULE"),
    ]) == 1
    warnings = capsys.readouterr().err
    assert "WARNING: No license text" in warnings
    assert "WARNING: Bundled files have no distribution ownership" in warnings
    assert "No license text collected" in (package / "THIRD_PARTY_NOTICES.txt").read_text()


def test_windows_backslash_record_paths_are_collected(license_build, monkeypatch):
    project, package, site = license_build
    dist = distribution(site, "windows-wheel", {
        r"windows_wheel\module.py": b"",
        r"windows-wheel-1.0.dist-info\licenses\LICENSE-MIT": b"Windows wheel license",
    })
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [dist])
    assert collector.collect_third_party_licenses(project, package, [
        ("windows_wheel.module", str(site / "windows_wheel/module.py"), "PYMODULE")
    ]) == 1
    text = package / "licenses/windows-wheel-1.0/installed/windows-wheel-1.0.dist-info/licenses/LICENSE-MIT"
    assert text.read_bytes() == b"Windows wheel license"


def test_license_copy_failure_remains_an_error(license_build, monkeypatch):
    project, package, site = license_build
    dist = distribution(site, "example", {
        "example.py": b"",
        "example-1.0.dist-info/LICENSE": b"MIT original",
    })
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [dist])

    def fail_copy(source, destination):
        raise PermissionError("cannot write license")

    monkeypatch.setattr(collector.shutil, "copyfile", fail_copy)
    with pytest.raises(PermissionError, match="cannot write license"):
        collector.collect_third_party_licenses(project, package, [
            ("example", str(site / "example.py"), "PYMODULE")
        ])


def test_license_destination_cannot_escape_component_directory(license_build, monkeypatch):
    project, package, site = license_build
    dist = distribution(site, "example", {"example.py": b"", "../LICENSE": b"MIT"})
    monkeypatch.setattr(collector.metadata, "distributions", lambda: [dist])
    with pytest.raises(RuntimeError, match="Unsafe license path"):
        collector.collect_third_party_licenses(project, package, [
            ("example", str(site / "example.py"), "PYMODULE")
        ])
