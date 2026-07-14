import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.runtime.paths import CampaignPaths
from app.runtime.session import get_campaign_runtime


@pytest.fixture(autouse=True)
def isolate_default_campaign_runtime(tmp_path):
    """Never allow a test to read or write the user's active save."""
    runtime = get_campaign_runtime()
    runtime.reconfigure(
        "pytest",
        CampaignPaths.from_data_dir(tmp_path / "campaign-runtime"),
        discard_pending=True,
    )
    try:
        yield
    finally:
        runtime.pending_turns.mapping.clear()
        runtime.pending_openings.mapping.clear()
        runtime.close(wait_for_jobs=True)
