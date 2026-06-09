from app.core.llm import LLMGateway
from app.domain.recall_memory.extractor import MemoryExtractor
from app.domain.recall_memory.store import MemoryStore
from app.domain.recall_memory.schemas import MemoryExtractResult


class MemoryService:
    def __init__(
        self,
        llm: LLMGateway,
        store: MemoryStore,
    ):
        self._extractor = MemoryExtractor(llm)
        self._store = store

    @property
    def extractor(self) -> MemoryExtractor:
        return self._extractor

    @property
    def store(self) -> MemoryStore:
        return self._store

    def process_scene(
        self,
        scene_text: str,
        scene_context: dict,
        *,
        trace_id: str | None = None,
    ) -> list[str]:
        result = self._extractor.extract(
            scene_text=scene_text,
            scene_context=scene_context,
            trace_id=trace_id,
        )
        return self._store.save_many(result.memories)
