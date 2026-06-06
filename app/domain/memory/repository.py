from app.domain.memory.schemas import Memory, MemoryRecord
from app.domain.memory.store import MemoryStore


class MemoryRepository:
    def __init__(self, store: MemoryStore):
        self._store = store

    def save_many(self, memories: list[Memory]) -> list[str]:
        return self._store.save_many(memories)

    def list_all(self) -> list[MemoryRecord]:
        return self._store.list_all()

    def get_by_id(self, memory_id: str) -> MemoryRecord | None:
        return self._store.get_by_id(memory_id)

    def count(self) -> int:
        return self._store.count()
