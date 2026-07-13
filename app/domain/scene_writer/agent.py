import json
from typing import cast

from pydantic import Field, create_model

from app.core.llm import LLMGateway
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.entity.schemas import EntityProfileUpdateInput
from app.domain.npc_profile.schemas import NPCProfilePublicView
from app.domain.plot_outline.schemas import PlotOutlineResult
from app.domain.scene_writer.schemas import SceneWriterResult
from app.domain.scene_writer.views import project_outline, project_recent_scene
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.views import (
    project_current_state,
    project_entity_profiles,
    project_npc_profiles,
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
        recent_scene: ShortTermScene | None = None,
        entity_profiles: list[EntityProfileUpdateInput] | None = None,
        npc_profiles: list[NPCProfilePublicView] | None = None,
        turn_index: int = 1,
        max_chars: int = 1500,
        trace_id: str | None = None,
    ) -> SceneWriterResult:
        if max_chars < 1:
            raise ValueError("max_chars must be >= 1")
        runtime_schema = create_model(
            "RuntimeSceneWriterResult",
            __base__=SceneWriterResult,
            scene_reasoning=(str, Field(min_length=1)),
            scene_text=(str, Field(min_length=1)),
            closing_prompt=(str, Field(min_length=1)),
        )
        schema = runtime_schema.model_json_schema()

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
                    "entity_profiles_json": project_entity_profiles(entity_profiles or []),
                    "npc_profiles_json": project_npc_profiles(npc_profiles or []),
                    "recent_scene_json": project_recent_scene(recent_scene),
                    "output_schema_json": json.dumps(schema, ensure_ascii=False, indent=2),
                },
                output_schema=runtime_schema,
                temperature=0.8,
                trace_id=trace_id,
            ),
        )
        closing_prompt = result.closing_prompt.strip()
        scene_text = result.scene_text.strip()
        if closing_prompt and scene_text.endswith(closing_prompt):
            scene_text = scene_text[:-len(closing_prompt)].rstrip()
        return SceneWriterResult(
            scene_reasoning=result.scene_reasoning,
            scene_text=scene_text,
            closing_prompt=closing_prompt,
            next_action_directions=result.next_action_directions,
        )
