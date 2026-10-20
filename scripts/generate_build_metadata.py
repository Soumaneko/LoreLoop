from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.version import APP_VERSION  # noqa: E402


_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\."
    r"(?P<minor>0|[1-9]\d*)\."
    r"(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<metadata>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def parse_application_version(version: str) -> tuple[int, int, int, int]:
    match = _SEMVER_PATTERN.fullmatch(version)
    if match is None:
        raise ValueError(f"Invalid application version: {version!r}")
    prerelease = match.group("prerelease") or ""
    revision = 0
    if prerelease:
        last_identifier = prerelease.rsplit(".", 1)[-1]
        if last_identifier.isdigit():
            revision = int(last_identifier)
    components = (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        revision,
    )
    if any(component > 65_535 for component in components):
        raise ValueError("Windows version components must be <= 65535")
    return components


def render_windows_version_info(version: str) -> str:
    major, minor, patch, revision = parse_application_version(version)
    numeric = f"({major}, {minor}, {patch}, {revision})"
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={numeric},
    prodvers={numeric},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable(
        '040904B0',
        [
          StringStruct('CompanyName', 'LoreLoop'),
          StringStruct('FileDescription', '续章 · LoreLoop'),
          StringStruct('FileVersion', '{version}'),
          StringStruct('InternalName', 'LoreLoop'),
          StringStruct('LegalCopyright', 'Copyright (c) 2026 Soumaneko'),
          StringStruct('OriginalFilename', 'LoreLoop.exe'),
          StringStruct('ProductName', 'LoreLoop（续章）'),
          StringStruct('ProductVersion', '{version}')
        ]
      )
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def build_information(
    *,
    environ: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    values = os.environ if environ is None else environ
    commit = str(values.get("GITHUB_SHA", "")).strip() or _git_commit()
    run_number = str(values.get("GITHUB_RUN_NUMBER", "")).strip()
    built_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {
        "version": APP_VERSION,
        "commit": commit,
        "github_run_number": run_number,
        "built_at": built_at.isoformat().replace("+00:00", "Z"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--print-version", action="store_true")
    parser.add_argument("--version-info", type=Path)
    parser.add_argument("--build-info", type=Path)
    arguments = parser.parse_args(argv)
    parse_application_version(APP_VERSION)

    if arguments.print_version:
        print(APP_VERSION)
    if arguments.version_info is not None:
        _write_text(
            arguments.version_info,
            render_windows_version_info(APP_VERSION),
        )
    if arguments.build_info is not None:
        _write_text(
            arguments.build_info,
            json.dumps(
                build_information(),
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
    if not (
        arguments.print_version
        or arguments.version_info is not None
        or arguments.build_info is not None
    ):
        parser.error("at least one output option is required")
    return 0


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())

