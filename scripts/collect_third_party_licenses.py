"""Collect notices for distributions selected by PyInstaller, without networking."""

from importlib import metadata
from pathlib import Path
import re
import shutil
import sys


LICENSE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice|copyright|authors)([.\-_]|$)", re.I
)


def collect_third_party_licenses(project_root, package_dir, entries):
    project_root, package_dir = Path(project_root), Path(package_dir)
    supplemental_root = project_root / "packaging" / "licenses"
    selected = {Path(source).resolve() for _, source, _ in entries if source}
    owned = set()
    components = []

    for dist in metadata.distributions():
        records = list(dist.files or [])
        matched = {
            Path(dist.locate_file(str(p).replace("\\", "/"))).resolve()
            for p in records
        } & selected
        if not matched:
            continue
        owned.update(matched)
        name = re.sub(r"[-_.]+", "-", dist.metadata["Name"]).lower()
        if not re.fullmatch(r"[a-z0-9-]+", name) or not re.fullmatch(
            r"[a-zA-Z0-9.+_-]+", dist.version
        ):
            raise RuntimeError(f"Unsafe distribution name/version: {name} {dist.version}")
        component_dir = package_dir / "licenses" / f"{name}-{dist.version}"
        files = []
        has_license = False
        for record in records:
            # Some Windows wheels use backslashes even in their RECORD entries.
            relative = Path(str(record).replace("\\", "/"))
            if not any(LICENSE_NAME.match(part) for part in relative.parts):
                continue
            if relative.is_absolute() or ".." in relative.parts:
                raise RuntimeError(f"Unsafe license path in {name}: {record}")
            source = Path(dist.locate_file(relative))
            if not source.is_file() or not source.stat().st_size:
                print(f"WARNING: Missing or empty license material: {source}", file=sys.stderr)
                continue
            destination = component_dir / "installed" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            files.append(destination.relative_to(package_dir).as_posix())
            has_license |= bool(re.match(r"^(licen[cs]e|copying)", relative.name, re.I))

        supplemental_dir = supplemental_root / name
        for source in sorted(supplemental_dir.rglob("*")):
            if not source.is_file():
                continue
            relative_path = source.relative_to(supplemental_dir)
            destination = component_dir / "supplemental" / relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            files.append(destination.relative_to(package_dir).as_posix())
            has_license |= bool(re.match(r"^(licen[cs]e|copying)", relative_path.name, re.I))
        if not has_license:
            print(f"WARNING: No license text for bundled distribution {name}=={dist.version}", file=sys.stderr)
        components.append((name, dist.version, sorted(files)))

    # RECORD ownership catches namespace packages and native/data files as well as .py.
    unowned = [p for p in selected - owned if "site-packages" in p.parts]
    if unowned:
        print(
            "WARNING: Bundled files have no distribution ownership: "
            + ", ".join(map(str, sorted(unowned))),
            file=sys.stderr,
        )
    if not components:
        print("WARNING: No third-party distributions found in PyInstaller analysis", file=sys.stderr)

    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file() or not python_license.stat().st_size:
        raise RuntimeError(f"Windows Python runtime license missing: {python_license}")
    runtime_dir = package_dir / "licenses" / f"Python-{sys.version.split()[0]}"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(python_license, runtime_dir / "LICENSE.txt")
    models_license = project_root / "app" / "runtime" / "models_dev_LICENSE.txt"
    models_dir = package_dir / "licenses" / "models.dev"
    models_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(models_license, models_dir / "LICENSE.txt")

    lines = [
        "LoreLoop - Third-party notices",
        "",
        "LoreLoop's own license is in LICENSE. The components below retain their own licenses.",
        "Original license and attribution texts are preserved under licenses/.",
        "Source links identify upstream releases; LoreLoop does not modify these components.",
        "",
        f"Python {sys.version.split()[0]}",
        f"  License and incorporated-software notices: {runtime_dir.relative_to(package_dir).as_posix()}/LICENSE.txt",
        "  Source: https://www.python.org/downloads/source/",
        "",
        "models.dev snapshot",
        "  License: licenses/models.dev/LICENSE.txt",
        "  Source: https://github.com/anomalyco/models.dev",
        "",
    ]
    for name, version, files in sorted(components):
        lines.append(f"{name} {version}")
        lines.append(f"  Source distributions: https://pypi.org/project/{name}/{version}/#files")
        lines.extend("  Text: " + f for f in files)
        if not files:
            lines.append("  No license text collected; see build warnings.")
        lines.append("")
    (package_dir / "THIRD_PARTY_NOTICES.txt").write_text("\n".join(lines), encoding="utf-8")
    return len(components)
