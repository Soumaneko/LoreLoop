import json
from dataclasses import dataclass, field
from typing import Any, cast

from pydantic import TypeAdapter, ValidationError

from app.core.llm import LLMGateway
from app.core.llm.errors import (
    LLMOutputParseError,
    LLMOutputValidationError,
)
from app.core.llm.schema import schema_json
from app.domain.action.schemas import (
    ActionExtractionResult,
    ActionExtractionResultLoose,
    ActionRetrievalTopic,
    AtomicAction,
)
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.views import project_current_state


def _event_hook_view(event: DirectorEvent) -> dict:
    return {
        "event_id": event.event_id,
        "title": event.title,
        "subject": event.subject,
        "intent": event.intent,
        "hooks": [
            {
                "hook_id": hook.hook_id,
                "kind": hook.kind,
                "content": hook.content,
                "trigger_anchors": hook.trigger_anchors,
            }
            for hook in event.hook_pool
            if not hook.consumed
        ],
    }


@dataclass(frozen=True)
class ActionParserWarning:
    code: str
    message: str


@dataclass
class _AttemptRecord:
    analysis: str = ""
    topics: list[ActionRetrievalTopic] = field(default_factory=list)
    actions: list[AtomicAction] = field(default_factory=list)
    touched_ids: list[str] = field(default_factory=list)
    warnings: list[ActionParserWarning] = field(default_factory=list)


_ACTION_ADAPTER = TypeAdapter(AtomicAction)
_TOPIC_ADAPTER = TypeAdapter(ActionRetrievalTopic)


def _parse_atomic_action(raw: Any) -> AtomicAction:
    if isinstance(raw, dict):
        cleaned = dict(raw)
        for key in ("action_id", "raw_text", "verb", "target", "method", "condition"):
            value = cleaned.get(key)
            if isinstance(value, str):
                cleaned[key] = value.strip()
        actor = cleaned.get("actor")
        if isinstance(actor, list):
            cleaned_actor: list[str] = []
            for value in actor:
                if not isinstance(value, str):
                    raise ValueError("actor entries must be strings")
                stripped = value.strip()
                if not stripped:
                    raise ValueError("actor must not contain blank entries")
                cleaned_actor.append(stripped)
            cleaned["actor"] = cleaned_actor
        raw = cleaned
    action = _ACTION_ADAPTER.validate_python(raw)
    if not action.action_id.strip():
        raise ValueError("action_id must be non-blank")
    if not action.raw_text.strip():
        raise ValueError("raw_text must be non-blank")
    if not action.verb.strip():
        raise ValueError("verb must be non-blank")
    if not action.actor:
        raise ValueError("actor must not be blank")
    return action


def _parse_topic(raw: Any) -> ActionRetrievalTopic:
    if isinstance(raw, dict) and isinstance(raw.get("text"), str):
        raw = {**raw, "text": raw["text"].strip()}
    return _TOPIC_ADAPTER.validate_python(raw)


def _collect_record(
    output: ActionExtractionResultLoose,
    allowed_event_ids: set[str],
) -> tuple[_AttemptRecord, list[str]]:
    warnings: list[ActionParserWarning] = []
    retry_errors: list[str] = []

    analysis = (output.analysis or "").strip()

    if not isinstance(output.retrieval_topics, list):
        retry_errors.append("retrieval_topics must be a list")
        topics: list[ActionRetrievalTopic] = []
    else:
        if len(output.retrieval_topics) > 8:
            retry_errors.append("retrieval_topics must have at most 8 items")
            warnings.append(ActionParserWarning(
                code="action_dropped",
                message=(
                    f"retrieval_topics contained {len(output.retrieval_topics)} "
                    "items; keeping first 8"
                ),
            ))
        topics = []
        for index, raw in enumerate(output.retrieval_topics[:8]):
            try:
                topic = _parse_topic(raw)
            except (ValidationError, ValueError) as exc:
                retry_errors.append(f"retrieval_topics[{index}]: {exc}")
                warnings.append(ActionParserWarning(
                    code="action_dropped",
                    message=f"retrieval_topics[{index}] dropped: {exc}",
                ))
                continue
            topics.append(topic)

    if not isinstance(output.atomic_actions, list):
        retry_errors.append("atomic_actions must be a list")
        actions: list[AtomicAction] = []
    else:
        actions = []
        for index, raw in enumerate(output.atomic_actions):
            try:
                action = _parse_atomic_action(raw)
            except (ValidationError, ValueError) as exc:
                retry_errors.append(f"atomic_actions[{index}]: {exc}")
                warnings.append(ActionParserWarning(
                    code="action_dropped",
                    message=f"atomic_actions[{index}] dropped: {exc}",
                ))
                continue
            actions.append(action)

    if not isinstance(output.touched_event_ids, list):
        retry_errors.append("touched_event_ids must be a list")
        touched_ids: list[str] = []
    else:
        touched_ids = []
        for event_id in output.touched_event_ids:
            if event_id not in allowed_event_ids:
                retry_errors.append(
                    f"touched_event_ids references unknown event ID: {event_id}"
                )
                warnings.append(ActionParserWarning(
                    code="action_dropped",
                    message=(
                        f"touched_event_id {event_id} dropped: not a visible "
                        "hidden event"
                    ),
                ))
                continue
            if event_id not in touched_ids:
                touched_ids.append(event_id)

    return _AttemptRecord(
        analysis=analysis,
        topics=topics,
        actions=actions,
        touched_ids=touched_ids,
        warnings=warnings,
    ), retry_errors


def _build_result(record: _AttemptRecord) -> ActionExtractionResult:
    return ActionExtractionResult(
        analysis=record.analysis,
        retrieval_topics=record.topics,
        atomic_actions=record.actions,
        touched_event_ids=record.touched_ids,
    )


class ActionParser:
    def __init__(self, llm: LLMGateway):
        self.llm = llm
        self.last_warnings: list[ActionParserWarning] = []

    def parse(
        self,
        user_input: str,
        current_state: CurrentState,
        *,
        core_memory: CoreMemory,
        events: list[DirectorEvent] | None = None,
        trace_id: str | None = None,
    ) -> ActionExtractionResult:
        self.last_warnings = []
        if not user_input.strip():
            return ActionExtractionResult()

        events = events or []
        allowed_event_ids = {event.event_id for event in events}
        attempt_records: dict[int, _AttemptRecord] = {}
        current_attempt = 0

        def on_attempt_start(attempt: int) -> None:
            nonlocal current_attempt
            current_attempt = attempt

        def validate_output(output: ActionExtractionResultLoose) -> None:
            record, retry_errors = _collect_record(
                output, allowed_event_ids
            )
            attempt_records[current_attempt] = record
            if retry_errors:
                raise ValueError("; ".join(retry_errors))

        try:
            result = cast(
                ActionExtractionResultLoose,
                self.llm.run(
                    task="action_extract",
                    input_data={
                        "user_input": user_input,
                        "current_state_json": project_current_state(current_state),
                        "core_memory_json": core_memory.model_dump_json(indent=2),
                        "director_events_json": json.dumps(
                            [_event_hook_view(event) for event in events],
                            ensure_ascii=False,
                            indent=2,
                        ),
                        "output_schema_json": schema_json(ActionExtractionResult),
                    },
                    output_schema=ActionExtractionResultLoose,
                    output_validator=validate_output,
                    on_attempt_start=on_attempt_start,
                    trace_id=trace_id,
                ),
            )
            record = attempt_records[current_attempt]
            self.last_warnings = list(record.warnings)
            return _build_result(record)
        except (
            LLMOutputParseError,
            LLMOutputValidationError,
        ) as exc:
            return self._salvage(attempt_records, exc)

    def _salvage(
        self,
        attempt_records: dict[int, _AttemptRecord],
        exception: Exception,
    ) -> ActionExtractionResult:
        latest = attempt_records.get(2)
        if latest is None:
            latest = attempt_records.get(1)
        # Actions are the primary product. We select a single attempt and never
        # cross-merge components across attempts; if the second attempt has no
        # valid actions, the first attempt's actions win even when the second
        # attempt has valid retrieval topics.
        usable = attempt_records.get(2)
        if usable is None or not usable.actions:
            first = attempt_records.get(1)
            if first is not None and first.actions:
                usable = first
        if usable is None:
            usable = attempt_records.get(2) or attempt_records.get(1)
        if usable is not None and (usable.actions or usable.topics or usable.touched_ids):
            warnings = list(usable.warnings)
            if latest is not None and latest is not usable:
                for warning in latest.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            self.last_warnings = warnings
            return _build_result(usable)

        warnings: list[ActionParserWarning] = []
        for record in attempt_records.values():
            for warning in record.warnings:
                if warning not in warnings:
                    warnings.append(warning)
        warnings.append(ActionParserWarning(
            code="action_extract_fallback",
            message=(
                "Action extraction failed; no valid actions were generated: "
                f"{exception}"
            ),
        ))
        self.last_warnings = warnings
        return ActionExtractionResult(
            analysis="行动解析失败",
            retrieval_topics=[],
            atomic_actions=[],
            touched_event_ids=[],
        )
