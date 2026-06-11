import json
from dataclasses import dataclass, field
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.core.llm import LLMGateway
from app.domain.current_state.schemas import CurrentState
from app.domain.recall_memory.schemas import Memory, MemoryExtractResult
from app.domain.recall_memory.self_check import SelfCheckResult


# ── LLM 输出 schema ─────────────────────────────────────────────

class LLMCheckChange(BaseModel):
    """LLM 返回的单条修改记录。不改完整 memory，只描述差异。"""
    memory_index: int = Field(description="被修改的 memory 在列表中的索引 (0-based), -1 表示新增")
    field: str = Field(description="修改的字段名, 或 '__new__' 表示新增一条 memory")
    old_value: Optional[Any] = Field(default=None, description="修改前的值")
    new_value: Optional[Any] = Field(default=None, description="修改后的值")
    reason: str = Field(default="", description="修改原因")


class LLMCheckResponse(BaseModel):
    """LLM 检查的完整返回。"""
    changes: list[LLMCheckChange] = Field(default_factory=list)


# ── 结果 ─────────────────────────────────────────────────────────

@dataclass
class LLMCheckResult:
    original_count: int
    changes_applied: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.changes_applied)


# ── LLM 检查器 ───────────────────────────────────────────────────

CHECKLIST_FIELDS = [
    "type",
    "subject",
    "content",
    "entities",
    "location",
    "time_label",
    "certainty",
    "salience",
    "source_text_span",
    "visibility",
]


class LLMCheck:
    def __init__(self, gateway: Optional[LLMGateway] = None):
        self._gateway = gateway

    @property
    def gateway(self) -> LLMGateway:
        if self._gateway is None:
            raise RuntimeError("LLMCheck not connected to a gateway")
        return self._gateway

    def set_gateway(self, gateway: LLMGateway) -> None:
        self._gateway = gateway

    def check(
        self,
        memories: list[Memory],
        scene_text: str,
        current_state: CurrentState,
        *,
        self_check_warnings: Optional[list[str]] = None,
    ) -> tuple[list[Memory], LLMCheckResult]:
        check_result = LLMCheckResult(original_count=len(memories))

        if not memories:
            return [], check_result

        memories_json = _memories_to_check_json(memories)
        warnings_text = _format_warnings(self_check_warnings, memories)

        response = self.gateway.run(
            task="llm_check",
            input_data={
                "scene_text": scene_text,
                "current_state_json": current_state.model_dump_json(indent=2),
                "memories_json": memories_json,
                "self_check_warnings": warnings_text,
            },
            output_schema=LLMCheckResponse,
        )

        modified, applied = _apply_changes(memories, response.changes)
        check_result.changes_applied = applied

        return modified, check_result


# ── helpers ──────────────────────────────────────────────────────

def _memories_to_check_json(memories: list[Memory]) -> str:
    items = []
    for i, m in enumerate(memories):
        items.append({
            "index": i,
            "type": m.type,
            "subject": m.subject,
            "content": m.content,
            "entities": m.entities,
            "location": m.location,
            "time_label": m.time_label,
            "visibility": m.visibility,
            "tags": m.tags,
            "certainty": m.certainty,
            "salience": m.salience,
            "source_scene_id": m.source_scene_id,
            "source_text_span": m.source_text_span,
        })
    return json.dumps(items, ensure_ascii=False, indent=2)


def _format_warnings(warnings: Optional[list[str]], memories: list[Memory]) -> str:
    if not warnings:
        return "(无警告)"
    lines = []
    for w in warnings:
        ridx = _match_warning_to_index(w, memories)
        marker = f"[{ridx}]" if ridx >= 0 else "[?]"
        lines.append(f"- {marker} {w}")
    return "\n".join(lines)


def _match_warning_to_index(warning: str, memories: list[Memory]) -> int:
    for i, m in enumerate(memories):
        if m.subject in warning:
            return i
    return -1


def _apply_changes(
    memories: list[Memory],
    changes: list[LLMCheckChange],
) -> tuple[list[Memory], list[dict]]:
    working = [m.model_copy() for m in memories]
    applied: list[dict] = []

    for ch in changes:
        # ── 新增记忆 ──
        if ch.field == "__new__":
            try:
                if not isinstance(ch.new_value, dict):
                    applied.append({"memory_index": -1, "field": "__new__", "error": "new_value must be a dict"})
                    continue
                new_memory = Memory.model_validate(ch.new_value)
                working.append(new_memory)
                applied.append({
                    "memory_index": len(working) - 1,
                    "field": "__new__",
                    "subject": new_memory.subject,
                    "reason": ch.reason,
                })
            except Exception as e:
                applied.append({"memory_index": -1, "field": "__new__", "error": str(e)})
            continue

        # ── 删除记忆 ──
        if ch.field == "__delete__":
            if 0 <= ch.memory_index < len(working):
                removed = working.pop(ch.memory_index)
                applied.append({
                    "memory_index": ch.memory_index,
                    "field": "__delete__",
                    "subject": removed.subject,
                    "reason": ch.reason,
                })
            else:
                applied.append({"memory_index": ch.memory_index, "field": "__delete__", "error": "索引越界"})
            continue

        # ── 警告审阅 ──
        if ch.field == "__warning_reviewed__":
            applied.append({
                "memory_index": ch.memory_index,
                "field": "__warning_reviewed__",
                "reason": ch.reason,
            })
            continue

        # ── 字段修改 ──
        if ch.memory_index < 0 or ch.memory_index >= len(working):
            applied.append({"memory_index": ch.memory_index, "field": ch.field, "error": "索引越界"})
            continue

        if ch.field not in CHECKLIST_FIELDS:
            applied.append({"memory_index": ch.memory_index, "field": ch.field, "error": "不可修改的字段"})
            continue

        memory = working[ch.memory_index]
        old_value = getattr(memory, ch.field, None)
        new_value = ch.new_value

        try:
            updated_data = memory.model_dump()
            updated_data[ch.field] = new_value
            validated = Memory.model_validate(updated_data)
            working[ch.memory_index] = validated
            applied.append({
                "memory_index": ch.memory_index,
                "field": ch.field,
                "old": old_value,
                "new": new_value,
                "reason": ch.reason,
            })
        except Exception as e:
            applied.append({
                "memory_index": ch.memory_index,
                "field": ch.field,
                "old": old_value,
                "new": new_value,
                "error": f"修改后校验失败: {e}",
            })

    return working, applied
