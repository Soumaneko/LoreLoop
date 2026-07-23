import json
from collections.abc import Callable

from app.core.llm import LLMGateway
from app.domain.narrative_composer.runner import run_narrative_composition
from app.domain.narrative_composer.schemas import (
    NarrativeComposerContext,
    NarrativeComposerResult,
)
from app.domain.short_term_memory.schemas import ShortTermScene

from .schemas import SourceRewriteRequest


class SourceNarrativeRewriter:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def rewrite(
        self,
        context: NarrativeComposerContext,
        request: SourceRewriteRequest,
        *,
        recent_scene: ShortTermScene | None = None,
        turn_index: int = 1,
        trace_id: str | None = None,
        on_scene_text_delta: Callable[[str], None] | None = None,
        on_stream_start: Callable[[int], None] | None = None,
    ) -> NarrativeComposerResult:
        return run_narrative_composition(
            self.llm,
            task="narrative_rewrite_from_source",
            context=context,
            recent_scene=recent_scene,
            turn_index=turn_index,
            extra_input={
                "source_scene_text": request.source_scene_text,
                "rewrite_requirements_json": json.dumps(
                    request.requirements,
                    ensure_ascii=False,
                    indent=2,
                ),
            },
            trace_id=trace_id,
            on_scene_text_delta=on_scene_text_delta,
            on_stream_start=on_stream_start,
        )
