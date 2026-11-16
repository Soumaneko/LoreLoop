"""Record confirmed scene facts on an existing Event."""

import json
from typing import Annotated, cast

from pydantic import BaseModel, ConfigDict, Field

from app.core.llm.schema import schema_json
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState

from .schemas import DirectorEvent, DirectorEventFact


class DirectorEventFactUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, description="本轮重要事实变化及新增、失效的依据。")
    add_facts: list[Annotated[str, Field(min_length=1)]]
    invalidate_fact_ids: list[str]

    def apply(self, event: DirectorEvent) -> list[DirectorEventFact]:
        invalid = set(self.invalidate_fact_ids)
        facts = [fact.model_copy(update={"active": False}) if fact.fact_id in invalid else fact
                 for fact in event.current_situation]
        active_texts = {fact.fact for fact in facts if fact.active}
        for text in self.add_facts:
            if text not in active_texts:
                facts.append(DirectorEventFact(fact=text))
                active_texts.add(text)
        return facts


class DirectorEventFactUpdater:
    def __init__(self, llm):
        self.llm = llm

    def update(
        self, *, event: DirectorEvent, scene_text: str, scene_facts: dict,
        user_input: str, current_state: CurrentState, core_memory: CoreMemory,
        fact_changes: str, trace_id: str | None = None,
    ) -> DirectorEventFactUpdate:
        active_ids = {fact.fact_id for fact in event.current_situation if fact.active}

        def validate_update(result: DirectorEventFactUpdate) -> None:
            if set(result.invalidate_fact_ids) - active_ids:
                raise ValueError("invalidate_fact_ids must reference this Event's active facts")

        return cast(DirectorEventFactUpdate, self.llm.run(
            task="director_event_update",
            input_data={
                "event_json": json.dumps(event.semantic_view(), ensure_ascii=False, indent=2),
                "scene_text": scene_text,
                "scene_facts_json": json.dumps(scene_facts, ensure_ascii=False, indent=2),
                "user_input": user_input,
                "current_state_json": current_state.model_dump_json(indent=2),
                "core_memory_json": core_memory.model_dump_json(indent=2),
                "fact_changes": fact_changes,
                "output_schema_json": schema_json(DirectorEventFactUpdate),
            },
            output_schema=DirectorEventFactUpdate, output_validator=validate_update,
            temperature=0.2, trace_id=trace_id,
        ))
