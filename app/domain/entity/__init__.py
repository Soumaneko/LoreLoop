from .schemas import (
    EntityProfile,
    EntityProfileUpdateBatch,
    EntityProfileUpdateInput,
    EntityType,
)
from .store import EntityStore

__all__ = [
    "EntityMemoryLinker",
    "EntityProfile",
    "EntityProfileUpdateBatch",
    "EntityProfileUpdateInput",
    "EntityProfileUpdater",
    "EntityStore",
    "EntityType",
]


def __getattr__(name: str):
    if name == "EntityMemoryLinker":
        from .linker import EntityMemoryLinker

        return EntityMemoryLinker
    if name == "EntityProfileUpdater":
        from .updater import EntityProfileUpdater

        return EntityProfileUpdater
    raise AttributeError(name)
