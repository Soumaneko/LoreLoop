from .schemas import (
    EntityProfile,
    EntityAliasMatch,
    EntityExtractionResult,
    EntityProfileIndexedUpdate,
    EntityProfileUpdateBatch,
    EntityProfileUpdateInput,
    EntityType,
    RetrievedEntity,
)
from .store import EntityStore

__all__ = [
    "EntityAliasMatch",
    "EntityExtractionResult",
    "EntityExtractor",
    "EntityProfile",
    "EntityProfileIndexedUpdate",
    "EntityProfileUpdateBatch",
    "EntityProfileUpdateInput",
    "EntityProfileUpdater",
    "EntityStore",
    "EntityType",
    "RetrievedEntity",
]


def __getattr__(name: str):
    if name == "EntityProfileUpdater":
        from .updater import EntityProfileUpdater

        return EntityProfileUpdater
    if name == "EntityExtractor":
        from .extractor import EntityExtractor

        return EntityExtractor
    raise AttributeError(name)
