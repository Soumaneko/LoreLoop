from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.recall_memory.schemas import MemoryDraftExtractResult, PendingMemoryItem


class MemoryDraftExtractor:
    def __init__(self, llm: LLMGateway):
        self._llm = llm

    def extract(
        self,
        scene_text: str,
        current_state: CurrentState,
        *,
        turn_index: int,
        scene_id: str | None = None,
        trace_id: str | None = None,
    ) -> MemoryDraftExtractResult:
        from app.domain.views import project_current_state

        scene_id = scene_id or current_state.scene.scene_id
        result = self._llm.run(
            task="memory_draft_extract",
            input_data={
                "scene_text": scene_text,
                "current_state_json": project_current_state(current_state),
            },
            output_schema=MemoryDraftExtractResult,
            trace_id=trace_id,
        )

        items: list[PendingMemoryItem] = []
        for item in result.items[:5]:
            updates = {
                "scene_id": scene_id,
                "turn_index": turn_index,
            }
            items.append(item.model_copy(update=updates))

        return MemoryDraftExtractResult(items=items)
