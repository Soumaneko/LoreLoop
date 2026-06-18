from .schemas import (
    EntityProfile,
    EntityAliasMatch,
    EntityExtractionResult,
    EntityProfileUpdateBatch,
    EntityProfileUpdateInput,
    EntityType,
    RetrievedEntity,
)
from .store import EntityStore

__all__ = [
    "EntityMemoryLinker",
    "EntityAliasMatch",
    "EntityExtractionResult",
    "EntityExtractor",
    "EntityProfile",
    "EntityProfileUpdateBatch",
    "EntityProfileUpdateInput",
    "EntityProfileUpdater",
    "EntityStore",
    "EntityType",
    "RetrievedEntity",
]


def __getattr__(name: str):
    if name == "EntityMemoryLinker":
        from .linker import EntityMemoryLinker

        return EntityMemoryLinker
    if name == "EntityProfileUpdater":
        from .updater import EntityProfileUpdater

        return EntityProfileUpdater
    if name == "EntityExtractor":
        from .extractor import EntityExtractor

        return EntityExtractor
    raise AttributeError(name)
