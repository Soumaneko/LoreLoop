import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.runtime.paths import CampaignPaths
from app.runtime.session import get_campaign_runtime
from app.runtime.services import RuntimeServices


@pytest.fixture
def configure_runtime_services(monkeypatch):
    """Replace explicit runtime dependencies without patching module globals."""
    runtime = get_campaign_runtime()

    def configure(*, gateway_factory=None, **store_factories):
        base_stores = runtime.stores
        stores = SimpleNamespace(**{
            name: store_factories.get(name, getattr(base_stores, name))
            for name in (
                "current_state",
                "memory",
                "pending_memory",
                "short_term",
                "canon_evidence",
                "scene_summary",
                "entity",
                "npc_profile",
                "core_memory",
                "director_event",
                "director_npc",
                "world_setting",
            )
        })
        services = RuntimeServices(
            stores=stores,
            gateway_factory=gateway_factory or runtime.gateway_factory,
            jobs=runtime.jobs,
        )
        monkeypatch.setattr(runtime, "services", services)
        return services

    return configure


@pytest.fixture(autouse=True)
def isolate_default_campaign_runtime(tmp_path):
    """Never allow a test to read or write the user's active save."""
    runtime = get_campaign_runtime()
    # The default runtime is constructed during module import and may have
    # restored the user's pending turns. Detach them in memory without firing
    # persistence callbacks before switching to the isolated test campaign.
    runtime.pending_turns.clear(discard=False)
    runtime.pending_openings.clear(discard=False)
    runtime.reconfigure(
        "pytest",
        CampaignPaths.from_data_dir(tmp_path / "campaign-runtime"),
    )
    try:
        yield
    finally:
        runtime.pending_turns.clear()
        runtime.pending_openings.clear()
        runtime.close(wait_for_jobs=True)
