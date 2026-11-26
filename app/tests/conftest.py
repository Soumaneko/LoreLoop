import os
import sys
import tempfile
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Resolve every runtime path against an isolated directory before any runtime
# module is imported, so importing them can never touch the user's real saves.
os.environ["LORELOOP_DATA_DIR"] = tempfile.mkdtemp(prefix="loreloop-tests-")

from app.runtime.paths import CampaignPaths
from app.runtime import session
from app.runtime.services import RuntimeServices


@pytest.fixture
def configure_runtime_services(monkeypatch, campaign_runtime):
    """Replace explicit runtime dependencies without patching module globals."""
    runtime = campaign_runtime

    def configure(*, gateway_factory=None, **store_factories):
        base_stores = runtime.stores

        def _store(name):
            if name in store_factories:
                return store_factories[name]()
            return getattr(base_stores, name)

        stores = SimpleNamespace(
            paths=base_stores.paths,
            close=lambda: None,
            **{
                name: _store(name)
                for name in (
                    "current_state",
                    "continuity",
                    "short_term",
                    "confirmed_history",
                    "scene_summary",
                    "npc_profile",
                    "core_memory",
                    "director_event",
                    "director_npc",
                    "director_log",
                    "world_setting",
                )
            },
        )
        services = RuntimeServices(
            stores=stores,
            gateway_factory=gateway_factory or runtime.gateway_factory,
            jobs=runtime.jobs,
        )
        monkeypatch.setattr(runtime, "services", services)
        return services

    return configure


@pytest.fixture
def campaign_runtime(tmp_path, monkeypatch):
    """Keep the shared default runtime scoped to this test's campaign."""
    runtime = session.CampaignRuntime(
        "pytest",
        paths=CampaignPaths.from_data_dir(tmp_path / "campaign-runtime"),
    )
    monkeypatch.setattr(session, "_default_runtime", runtime)
    try:
        yield runtime
    finally:
        runtime.pending_turns.clear()
        runtime.pending_openings.clear()
        runtime.close()
