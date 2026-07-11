from __future__ import annotations

from dataclasses import dataclass, field

from .generation_state import GenerationEvent


_ACTION_PROGRESS_STEPS = {
    "retrieval_done",
    "modifiers_done",
    "rolls_done",
}


@dataclass
class GenerationEventPresenter:
    context_events: list[GenerationEvent] = field(default_factory=list)
    action_events_started: bool = False

    def present(
        self,
        node_name: str,
        events: list[GenerationEvent],
    ) -> list[GenerationEvent]:
        if node_name == "extract_context_facts":
            self.context_events.extend(events)
            return self._drain_context() if self.action_events_started else []

        ordered: list[GenerationEvent] = []
        for event in events:
            step = event.get("step")
            if step == "action_parsed":
                self.action_events_started = True
                ordered.append(event)
                ordered.extend(self._drain_context())
                continue
            if self.context_events and step not in _ACTION_PROGRESS_STEPS:
                ordered.extend(self._drain_context())
            ordered.append(event)
        return ordered

    def finish(self) -> list[GenerationEvent]:
        return self._drain_context()

    def _drain_context(self) -> list[GenerationEvent]:
        events = self.context_events
        self.context_events = []
        return events
