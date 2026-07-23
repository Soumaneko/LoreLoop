import json
from typing import cast

from app.core.llm import LLMGateway
from app.domain.narrative_composer.runner import composer_context_input
from app.domain.narrative_composer.schemas import NarrativeComposerContext
from app.domain.short_term_memory.schemas import ShortTermScene

from .schemas import RewriteStrategyDecision


class RewriteStrategySelector:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def select(
        self,
        *,
        context: NarrativeComposerContext,
        source_scene_text: str,
        user_feedback: str,
        recent_scene: ShortTermScene | None = None,
        turn_index: int = 1,
        trace_id: str | None = None,
    ) -> RewriteStrategyDecision:
        input_data = composer_context_input(context, recent_scene, turn_index)
        input_data.update({
            "source_scene_text": source_scene_text,
            "user_feedback": user_feedback,
            "output_schema_json": json.dumps(
                RewriteStrategyDecision.model_json_schema(),
                ensure_ascii=False,
                indent=2,
            ),
        })
        return cast(
            RewriteStrategyDecision,
            self.llm.run(
                task="narrative_rewrite_select",
                input_data=input_data,
                output_schema=RewriteStrategyDecision,
                temperature=0.2,
                trace_id=trace_id,
            ),
        )
