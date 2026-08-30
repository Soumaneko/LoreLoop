import shutil
import subprocess
from pathlib import Path

import pytest


STATIC_DIR = Path(__file__).parents[1] / "debug_static"


def test_debug_frontend_javascript_is_syntactically_valid():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    result = subprocess.run(
        [node, "--check", str(STATIC_DIR / "app.js")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
