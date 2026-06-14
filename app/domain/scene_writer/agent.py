import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.retrieval.schemas import MemoryCandidate
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.short_term_memory.schemas import ShortTermScene


class SceneWriterAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def write(
        self,
        *,
        outline: PlotOutlineResult,
        current_state: CurrentState,
        core_memory: CoreMemory,
        user_input: str,
        selected_memories: list[MemoryCandidate],
        recent_scene: ShortTermScene | None,
        turn_index: int = 1,
        trace_id: str | None = None,
    ) -> SceneWriterResult:
        schema = SceneWriterResult.model_json_schema()

        result = cast(
            SceneWriterResult,
            self.llm.run(
                task="scene_write",
                input_data={
                    "turn_index": turn_index,
                    "outline_json": outline.model_dump_json(indent=2),
                    "current_state_json": current_state.model_dump_json(indent=2),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "user_input": user_input,
                    "selected_memories_json": json.dumps(
                        [m.model_dump() for m in selected_memories],
                        ensure_ascii=False, indent=2,
                    ),
                    "recent_scene_json": recent_scene.model_dump_json(indent=2) if recent_scene else "null",
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=SceneWriterResult,
                trace_id=trace_id,
            ),
        )
        return result
