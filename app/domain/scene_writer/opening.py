from typing import cast

from app.core.llm import LLMGateway
from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.opening_foundation import OpeningFoundation
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.views import project_current_state


class OpeningSceneAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        current_state: CurrentState,
        core_memory: CoreMemory,
        opening_foundation: OpeningFoundation,
        trace_id: str | None = None,
    ) -> SceneWriterResult:
        schema = schema_json(SceneWriterResult)
        return cast(
            SceneWriterResult,
            self.llm.run(
                task="opening_scene",
                input_data={
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "opening_foundation_json": opening_foundation.model_dump_json(
                        indent=2
                    ),
                    "output_schema_json": schema,
                },
                output_schema=SceneWriterResult,
                temperature=0.4,
                trace_id=trace_id,
            ),
        )
