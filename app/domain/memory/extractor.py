import json
from logging import getLogger

from app.core.llm import LLMGateway
from app.domain.memory.schemas import MemoryExtractResult
from app.domain.memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.memory.llm_check import LLMCheck, LLMCheckResult

logger = getLogger(__name__)


class MemoryExtractor:
    def __init__(
        self,
        llm: LLMGateway,
        *,
        self_check: MemorySelfCheck | None = None,
        enable_self_check: bool = True,
        llm_check: LLMCheck | None = None,
        enable_llm_check: bool = False,
    ):
        self._llm = llm
        self._self_check = self_check or MemorySelfCheck()
        self._enable_self_check = enable_self_check
        self._llm_check = llm_check or LLMCheck(gateway=llm)
        self._enable_llm_check = enable_llm_check

    @property
    def llm(self) -> LLMGateway:
        return self._llm

    @property
    def last_check_result(self) -> SelfCheckResult | None:
        return self._last_check_result

    @property
    def last_llm_check_result(self) -> LLMCheckResult | None:
        return self._last_llm_check_result

    def extract(
        self,
        scene_text: str,
        scene_context: dict,
        *,
        trace_id: str | None = None,
        **kwargs,
    ) -> MemoryExtractResult:
        context_text = json.dumps(scene_context, ensure_ascii=False, indent=2)

        result = self._llm.run(
            task="memory_extract",
            input_data={
                "scene_text": scene_text,
                "scene_context": context_text,
            },
            output_schema=MemoryExtractResult,
            trace_id=trace_id,
            **kwargs,
        )

        if self._enable_self_check and result.memories:
            cleaned, check_result = self._self_check.check(
                result.memories, scene_text, scene_context
            )
            self._last_check_result = check_result

            if check_result.warnings:
                for w in check_result.warnings:
                    logger.warning(f"[MemoryExtractor/self_check] {w}")
            if check_result.fixes:
                for f in check_result.fixes:
                    logger.info(f"[MemoryExtractor/self_check] {f}")

            result = MemoryExtractResult(memories=cleaned)

        if self._enable_llm_check and result.memories:
            cleaned, llm_result = self._llm_check.check(
                result.memories,
                scene_text,
                scene_context,
                self_check_warnings=(
                    self._last_check_result.warnings
                    if self._last_check_result
                    else None
                ),
            )
            self._last_llm_check_result = llm_result

            if llm_result.changes_applied:
                logger.info(
                    f"[MemoryExtractor/llm_check] {llm_result.change_count} changes applied"
                )
                for ch in llm_result.changes_applied:
                    logger.info(f"[MemoryExtractor/llm_check]   {ch}")

            result = MemoryExtractResult(memories=cleaned)

        return result
