import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.core_memory.init import InitConfig
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.views import project_current_state


class OpeningSceneAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def generate(
        self,
        *,
        config: InitConfig,
        current_state: CurrentState,
        core_memory: CoreMemory,
        trace_id: str | None = None,
    ) -> SceneWriterResult:
        schema = SceneWriterResult.model_json_schema()
        return cast(
            SceneWriterResult,
            self.llm.run(
                task="opening_scene",
                input_data={
                    "init_config_json": config.model_dump_json(indent=2),
                    "current_state_json": project_current_state(current_state),
                    "core_memory_json": core_memory.model_dump_json(indent=2),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=SceneWriterResult,
                temperature=0.4,
                trace_id=trace_id,
            ),
        )
