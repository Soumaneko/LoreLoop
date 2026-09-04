from pathlib import Path
import sys


if sys.platform != "win32":
    raise SystemExit("LoreLoop Windows artifacts must be built on Windows")

project_root = Path(SPEC).resolve().parent.parent
version_file = project_root / "build" / "metadata" / "version_info.txt"


def collect_data_tree(source, destination):
    return [
        (
            str(path),
            str(Path(destination) / path.relative_to(source).parent),
        )
        for path in source.rglob("*")
        if path.is_file()
        and ".pytest_cache" not in path.parts
        and "__pycache__" not in path.parts
        and path.suffix not in {".pyc", ".pyo"}
    ]


prompt_root = project_root / "app" / "prompts"
player_static_root = project_root / "app" / "player_static"
models_dev_snapshot = project_root / "app" / "runtime" / "models_dev_snapshot.json"
models_dev_license = project_root / "app" / "runtime" / "models_dev_LICENSE.txt"

analysis = Analysis(
    [str(project_root / "app" / "desktop.py")],
    pathex=[str(project_root)],
    binaries=[],
    datas=(
        collect_data_tree(prompt_root, "app/prompts")
        + collect_data_tree(player_static_root, "app/player_static")
        + [
            (str(models_dev_snapshot), "app/runtime"),
            (str(models_dev_license), "app/runtime"),
        ]
    ),
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "app.debug_server",
        "pytest",
        "PyQt5",
        "PyQt6",
        "PySide2",
        "PySide6",
        "cefpython3",
        "gi",
        "gtk",
        "objc",
    ],
    noarchive=False,
    optimize=1,
)

if any(entry[0] == "app.debug_server" for entry in analysis.pure):
    raise RuntimeError("desktop analysis unexpectedly contains app.debug_server")

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="LoreLoop",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    version=str(version_file),
    contents_directory="_internal",
)

bundle = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="LoreLoop",
)
