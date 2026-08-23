from __future__ import annotations

from dataclasses import dataclass

from .campaign_migration import ensure_campaign_database
from .initialization import InitializationStore
from .paths import CampaignPaths
from .turn_history import CampaignHistory
from .uow import CampaignUnitOfWork


@dataclass(frozen=True)
class OpenedCampaignResources:
    history: CampaignHistory
    stores: CampaignUnitOfWork
    initialization: InitializationStore


def open_campaign_resources(paths: CampaignPaths) -> OpenedCampaignResources:
    """Open one validated campaign after completing interrupted recovery."""
    ensure_campaign_database(paths)
    history = CampaignHistory(paths.data_dir)
    history.recover_interrupted()
    stores = CampaignUnitOfWork(
        paths,
        expected_epoch=history.connection_epoch,
    )
    try:
        initialization = InitializationStore(paths.initialization_json)
    except BaseException:
        stores.close()
        raise
    return OpenedCampaignResources(
        history=history,
        stores=stores,
        initialization=initialization,
    )

