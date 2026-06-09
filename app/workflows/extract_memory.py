from dataclasses import dataclass, field

from app.core.llm import LLMGateway
from app.domain.recall_memory.extractor import MemoryExtractor
from app.domain.recall_memory.schemas import Memory, MemoryExtractResult
from app.domain.recall_memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.recall_memory.llm_check import LLMCheck, LLMCheckResult
from app.domain.recall_memory.store import MemoryStore


@dataclass
class ExtractMemoryWorkflowResult:
    raw_memories: list[Memory] = field(default_factory=list)
    self_check_result: SelfCheckResult | None = None
    llm_check_result: LLMCheckResult | None = None
    final_memories: list[Memory] = field(default_factory=list)
    saved_ids: list[str] = field(default_factory=list)
    store_count_before: int = 0
    store_count_after: int = 0
    store_total: int = 0


def run_extract_memory_workflow(
    scene_text: str,
    scene_context: dict,
    gateway: LLMGateway,
    db_path: str = "data/memories.db",
) -> ExtractMemoryWorkflowResult:
    extractor = MemoryExtractor(gateway, enable_self_check=False)

    raw = extractor.extract(scene_text, scene_context)
    wfr = ExtractMemoryWorkflowResult(raw_memories=raw.memories)

    sc = MemorySelfCheck()
    cleaned, sc_result = sc.check(raw.memories, scene_text, scene_context)
    wfr.self_check_result = sc_result

    if cleaned:
        llm_c = LLMCheck(gateway)
        cleaned, llm_result = llm_c.check(
            cleaned, scene_text, scene_context,
            self_check_warnings=sc_result.warnings,
        )
        wfr.llm_check_result = llm_result

    wfr.final_memories = cleaned

    with MemoryStore(db_path) as store:
        wfr.store_count_before = store.count()
        wfr.saved_ids = store.save_many(cleaned)
        wfr.store_count_after = store.count()
        wfr.store_total = len(store.list_all())

    return wfr


def extract_memory_workflow(
    scene_text: str,
    scene_context: dict,
    extractor: MemoryExtractor,
    store: MemoryStore,
) -> list[str]:
    result = extractor.extract(scene_text, scene_context)
    ids = store.save_many(result.memories)
    return ids
