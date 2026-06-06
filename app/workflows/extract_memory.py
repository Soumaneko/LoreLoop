from app.core.llm import LLMGateway
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.store import MemoryStore


def extract_memory_workflow(
    scene_text: str,
    scene_context: dict,
    extractor: MemoryExtractor,
    store: MemoryStore,
) -> list[str]:
    result = extractor.extract(scene_text, scene_context)
    ids = store.save_many(result.memories)
    return ids
