from collections.abc import Callable

from app.core.llm import LLMGateway
from app.domain.short_term_memory.schemas import ShortTermScene

from .runner import run_narrative_composition
from .schemas import NarrativeComposerContext, NarrativeComposerResult


class NarrativeComposerAgent:
    def __init__(self, llm: LLMGateway):
        self.llm = llm

    def compose(
        self,
        context: NarrativeComposerContext,
        *,
        recent_scene: ShortTermScene | None = None,
        turn_index: int = 1,
        max_chars: int = 1500,
        trace_id: str | None = None,
        on_scene_text_delta: Callable[[str], None] | None = None,
        on_stream_start: Callable[[int], None] | None = None,
    ) -> NarrativeComposerResult:
        del max_chars
        return run_narrative_composition(
            self.llm,
            task="narrative_compose",
            context=context,
            recent_scene=recent_scene,
            turn_index=turn_index,
            trace_id=trace_id,
            on_scene_text_delta=on_scene_text_delta,
            on_stream_start=on_stream_start,
        )
