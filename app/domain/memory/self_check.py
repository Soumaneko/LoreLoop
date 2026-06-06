from dataclasses import dataclass, field

from app.domain.memory.schemas import Memory

ELLIPSIS_CHARS = ("...", "…")
MAX_TAGS = 8
PUBLIC_VISIBILITY = "public"


@dataclass
class SelfCheckResult:
    original_count: int
    final_count: int = 0
    warnings: list[str] = field(default_factory=list)
    fixes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.final_count > 0

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def fix_count(self) -> int:
        return len(self.fixes)


class MemorySelfCheck:
    def check(
        self,
        memories: list[Memory],
        scene_text: str,
        scene_context: dict,
    ) -> tuple[list[Memory], SelfCheckResult]:
        result = SelfCheckResult(original_count=len(memories))
        cleaned: list[Memory] = []

        participants: list[str] = scene_context.get("participants", [])
        allowed_visibility = set(participants) | {PUBLIC_VISIBILITY}
        scene_text_normalized = scene_text.strip()
        scene_context_values = _flatten_strings(scene_context)

        for i, memory in enumerate(memories):
            memory = self._check_source_text_span(memory, i, scene_text_normalized, result)
            memory = self._fix_scene_context(memory, scene_context, result)
            memory = self._check_and_clean_visibility(memory, i, allowed_visibility, result)
            memory = self._check_and_clean_entities(memory, i, scene_text_normalized, scene_context_values, result)
            memory = self._clean_tags(memory)
            cleaned.append(memory)

        cleaned = self._deduplicate(cleaned, result)
        result.final_count = len(cleaned)
        return cleaned, result

    def _check_source_text_span(
        self,
        memory: Memory,
        index: int,
        scene_text: str,
        result: SelfCheckResult,
    ) -> Memory:
        span = (memory.source_text_span or "").strip()

        if not span:
            result.warnings.append(
                f"Memory #{index} ({memory.subject}) source_text_span is empty"
            )
            return memory

        for sep in ELLIPSIS_CHARS:
            if sep in span:
                result.warnings.append(
                    f"Memory #{index} ({memory.subject}) source_text_span contains '{sep}'"
                )
                return memory

        if span not in scene_text:
            result.warnings.append(
                f"Memory #{index} ({memory.subject}) source_text_span is not a substring "
                f"of scene_text (span={span!r})"
            )

        return memory

    def _fix_scene_context(
        self,
        memory: Memory,
        scene_context: dict,
        result: SelfCheckResult,
    ) -> Memory:
        scene_id = scene_context.get("scene_id")
        updates: dict = {}

        if scene_id is not None and memory.source_scene_id != scene_id:
            updates["source_scene_id"] = scene_id
            result.fixes.append(
                f"Memory '{memory.subject}' source_scene_id corrected: "
                f"{memory.source_scene_id!r} → {scene_id!r}"
            )

        if memory.location is None and scene_context.get("location"):
            updates["location"] = scene_context["location"]
            result.fixes.append(
                f"Memory '{memory.subject}' location filled from scene_context: "
                f"{scene_context['location']!r}"
            )

        if memory.time_label is None and scene_context.get("time_label"):
            updates["time_label"] = scene_context["time_label"]
            result.fixes.append(
                f"Memory '{memory.subject}' time_label filled from scene_context: "
                f"{scene_context['time_label']!r}"
            )

        if updates:
            return memory.model_copy(update=updates)

        return memory

    def _check_and_clean_visibility(
        self,
        memory: Memory,
        index: int,
        allowed: set[str],
        result: SelfCheckResult,
    ) -> Memory:
        visibility = memory.visibility

        if not visibility:
            result.warnings.append(
                f"Memory #{index} ({memory.subject}) visibility is empty"
            )
            return memory

        unknown = [v for v in visibility if v not in allowed]
        if unknown:
            result.warnings.append(
                f"Memory #{index} ({memory.subject}) visibility contains "
                f"unknown roles {unknown}"
            )

        return memory

    def _check_and_clean_entities(
        self,
        memory: Memory,
        index: int,
        scene_text: str,
        context_values: list[str],
        result: SelfCheckResult,
    ) -> Memory:
        entities = memory.entities

        seen: set[str] = set()
        cleaned: list[str] = []
        for entity in entities:
            if not entity or not entity.strip():
                continue
            if entity in seen:
                result.fixes.append(
                    f"Memory '{memory.subject}' duplicate entity removed: {entity!r}"
                )
                continue
            seen.add(entity)

            if not _entity_appears(entity, scene_text, context_values):
                result.warnings.append(
                    f"Memory '{memory.subject}' entity {entity!r} not found in "
                    f"scene_text or scene_context"
                )

            cleaned.append(entity)

        if cleaned != entities:
            return memory.model_copy(update={"entities": cleaned})

        return memory

    def _clean_tags(self, memory: Memory) -> Memory:
        seen: set[str] = set()
        cleaned: list[str] = []
        for tag in memory.tags:
            stripped = tag.strip()
            if not stripped:
                continue
            if stripped in seen:
                continue
            seen.add(stripped)
            cleaned.append(stripped)

        if len(cleaned) > MAX_TAGS:
            cleaned = cleaned[:MAX_TAGS]

        if cleaned != memory.tags:
            return memory.model_copy(update={"tags": cleaned})

        return memory

    def _deduplicate(
        self,
        memories: list[Memory],
        result: SelfCheckResult,
    ) -> list[Memory]:
        seen: set[tuple] = set()
        deduped: list[Memory] = []

        for memory in memories:
            key = _dedup_key(memory)
            if key in seen:
                result.fixes.append(
                    f"Duplicate memory removed: {memory.type}/{memory.subject}"
                )
                continue
            seen.add(key)
            deduped.append(memory)

        return deduped


def _dedup_key(memory: Memory) -> tuple:
    return (
        memory.type,
        memory.subject,
        tuple(sorted(memory.entities)),
        memory.content,
    )


def _flatten_strings(obj: dict) -> list[str]:
    result: list[str] = []
    for value in obj.values():
        if isinstance(value, str):
            result.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    result.append(item)
    return result


def _entity_appears(entity: str, scene_text: str, context_values: list[str]) -> bool:
    if entity in scene_text:
        return True
    for cv in context_values:
        if entity in cv:
            return True
    return False
