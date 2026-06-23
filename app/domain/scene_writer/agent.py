import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.retrieval.schemas import MemoryCandidate
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.scene_writer.views import project_outline
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_memory_candidates,
)


class SceneWriterAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def write(
        self,
        *,
        outline: PlotOutlineResult,
        current_state: CurrentState,
        core_memory: CoreMemory,
        selected_memories: list[MemoryCandidate],
        recent_scene: ShortTermScene | None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        turn_index: int = 1,
        max_chars: int = 1500,
        trace_id: str | None = None,
    ) -> SceneWriterResult:
        schema = SceneWriterResult.model_json_schema()

        result = cast(
            SceneWriterResult,
            self.llm.run(
                task="scene_write",
                input_data={
                    "turn_index": turn_index,
                    "max_chars": max_chars,
                    "outline_json": project_outline(outline),
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "selected_memories_json": project_memory_candidates(selected_memories),
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "recent_scene_json": recent_scene.model_dump_json(indent=2) if recent_scene else "null",
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=SceneWriterResult,
                temperature=0.9,
                trace_id=trace_id,
            ),
        )
        return result
