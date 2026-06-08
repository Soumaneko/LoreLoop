import json

import pytest

from app.core.llm import MockLLM
from app.domain.memory.llm_check import (
    LLMCheck,
    LLMCheckResponse,
    LLMCheckChange,
    _apply_changes,
)
from app.domain.memory.schemas import Memory
from app.core.llm.gateway import LLMGateway

SCENE_TEXT = "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。伊莱娜点头答应。"
SCENE_CONTEXT = {
    "scene_id": "scene_002",
    "location": "灰鹭旅店二楼",
    "time_label": "第2日夜晚",
    "participants": ["主角", "伊莱娜"],
    "viewpoint": "主角",
}


def _make_memory(**overrides) -> Memory:
    defaults = {
        "type": "agreement",
        "subject": "沉默暗号",
        "content": "主角和伊莱娜约定：左手轻敲两下桌面表示保持沉默。",
        "entities": ["主角", "伊莱娜"],
        "location": "灰鹭旅店二楼",
        "time_label": "第2日夜晚",
        "visibility": ["主角", "伊莱娜"],
        "tags": ["暗号"],
        "certainty": 1.0,
        "salience": 0.8,
        "source_scene_id": "scene_002",
        "source_text_span": ["用左手轻敲两下桌面，就表示让她保持沉默"],
    }
    defaults.update(overrides)
    return Memory(**defaults)


def _make_gateway(responses: list[str]) -> LLMGateway:
    mock = MockLLM()
    mock.set_responses(responses)
    return mock.build_gateway()


class TestLLMCheckBasic:
    def test_no_changes_returns_original(self):
        gateway = _make_gateway([
            json.dumps({"changes": []}, ensure_ascii=False),
        ])
        checker = LLMCheck(gateway)
        memories = [_make_memory()]
        result, cr = checker.check(memories, SCENE_TEXT, SCENE_CONTEXT)
        assert len(result) == 1
        assert cr.change_count == 0

    def test_change_subject_applied(self):
        gateway = _make_gateway([
            json.dumps({
                "changes": [
                    {
                        "memory_index": 0,
                        "field": "subject",
                        "old_value": "沉默暗号",
                        "new_value": "左手敲桌暗号",
                        "reason": "更具体的标题",
                    }
                ]
            }, ensure_ascii=False),
        ])
        checker = LLMCheck(gateway)
        memories = [_make_memory()]
        result, cr = checker.check(memories, SCENE_TEXT, SCENE_CONTEXT)
        assert len(result) == 1
        assert result[0].subject == "左手敲桌暗号"
        assert cr.change_count == 1

    def test_change_certainty_applied(self):
        gateway = _make_gateway([
            json.dumps({
                "changes": [
                    {
                        "memory_index": 0,
                        "field": "certainty",
                        "old_value": 1.0,
                        "new_value": 0.7,
                        "reason": "含推测成分",
                    }
                ]
            }, ensure_ascii=False),
        ])
        checker = LLMCheck(gateway)
        memories = [_make_memory()]
        result, cr = checker.check(memories, SCENE_TEXT, SCENE_CONTEXT)
        assert result[0].certainty == 0.7

    def test_change_certainty_coerces_validated_type(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=0,
            field="certainty",
            old_value=1.0,
            new_value="0.6",
            reason="LLM returned a numeric string",
        )]

        result, applied = _apply_changes(memories, changes)

        assert "error" not in applied[0]
        assert result[0].certainty == 0.6
        assert isinstance(result[0].certainty, float)

    def test_empty_memories_returns_empty(self):
        gateway = _make_gateway([json.dumps({"changes": []})])
        checker = LLMCheck(gateway)
        result, cr = checker.check([], SCENE_TEXT, SCENE_CONTEXT)
        assert result == []
        assert cr.change_count == 0


class TestApplyChanges:
    def test_invalid_field_rejected(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=0, field="invalid_field",
            old_value="x", new_value="y", reason="test",
        )]
        result, applied = _apply_changes(memories, changes)
        assert len(applied) == 1
        assert "error" in applied[0]
        assert result[0].subject == "沉默暗号"

    def test_out_of_bounds_index_rejected(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=99, field="subject",
            old_value="x", new_value="y", reason="test",
        )]
        result, applied = _apply_changes(memories, changes)
        assert "error" in applied[0]

    def test_type_change_on_valid_type(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=0, field="type",
            old_value="agreement", new_value="fact", reason="更准确",
        )]
        result, applied = _apply_changes(memories, changes)
        assert len(applied) == 1
        assert "error" not in applied[0]
        assert result[0].type == "fact"

    def test_type_change_to_invalid_fails(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=0, field="type",
            old_value="agreement", new_value="invalid_type",
            reason="test",
        )]
        result, applied = _apply_changes(memories, changes)
        assert "error" in applied[0]
        assert result[0].type == "agreement"

    def test_multiple_changes_on_one_memory(self):
        memories = [_make_memory()]
        changes = [
            LLMCheckChange(memory_index=0, field="subject",
                           old_value="沉默暗号", new_value="新标题", reason=""),
            LLMCheckChange(memory_index=0, field="salience",
                           old_value=0.8, new_value=0.9, reason=""),
        ]
        result, applied = _apply_changes(memories, changes)
        assert len(applied) == 2
        assert result[0].subject == "新标题"
        assert result[0].salience == 0.9

    def test_change_multiple_memories(self):
        m1 = _make_memory(subject="记忆A")
        m2 = _make_memory(subject="记忆B")
        changes = [
            LLMCheckChange(memory_index=0, field="subject",
                           old_value="记忆A", new_value="修改A", reason=""),
            LLMCheckChange(memory_index=1, field="subject",
                           old_value="记忆B", new_value="修改B", reason=""),
        ]
        result, applied = _apply_changes([m1, m2], changes)
        assert len(applied) == 2
        assert result[0].subject == "修改A"
        assert result[1].subject == "修改B"

    def test_new_memory_added(self):
        memories = [_make_memory(subject="旧记忆")]
        changes = [LLMCheckChange(
            memory_index=-1, field="__new__",
            old_value=None,
            new_value={
                "type": "event", "subject": "新事件", "content": "新增的记忆。",
                "entities": ["主角"], "certainty": 1.0, "salience": 0.8,
                "source_scene_id": "scene_001",
                "source_text_span": ["原文片段"],
            },
            reason="遗漏了重要事件",
        )]
        result, applied = _apply_changes(memories, changes)
        assert len(result) == 2
        assert result[1].subject == "新事件"
        assert applied[0]["field"] == "__new__"

    def test_new_memory_invalid_data_rejected(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=-1, field="__new__",
            old_value=None, new_value={"type": "invalid_type", "subject": ""},
            reason="",
        )]
        result, applied = _apply_changes(memories, changes)
        assert len(result) == 1
        assert "error" in applied[0]

    def test_delete_memory(self):
        m1 = _make_memory(subject="保留")
        m2 = _make_memory(subject="删除")
        changes = [LLMCheckChange(
            memory_index=1, field="__delete__",
            old_value=None, new_value=None,
            reason="无复用价值",
        )]
        result, applied = _apply_changes([m1, m2], changes)
        assert len(result) == 1
        assert result[0].subject == "保留"
        assert applied[0]["subject"] == "删除"

    def test_delete_out_of_bounds(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=99, field="__delete__",
            old_value=None, new_value=None, reason="",
        )]
        result, applied = _apply_changes(memories, changes)
        assert "error" in applied[0]

    def test_warning_reviewed_tracked(self):
        memories = [_make_memory()]
        changes = [LLMCheckChange(
            memory_index=0, field="__warning_reviewed__",
            old_value=None, new_value=None,
            reason="entity 合理保留",
        )]
        result, applied = _apply_changes(memories, changes)
        assert len(result) == 1
        assert applied[0]["field"] == "__warning_reviewed__"


class TestLLMCheckIntegration:
    def test_basic(self):
        gateway = _make_gateway([
            json.dumps({
                "changes": [
                    {
                        "memory_index": 0,
                        "field": "certainty",
                        "old_value": 1.0,
                        "new_value": 0.9,
                        "reason": "调整确定性",
                    }
                ]
            }, ensure_ascii=False),
        ])
        checker = LLMCheck(gateway)
        memories = [_make_memory()]
        result, cr = checker.check(
            memories, SCENE_TEXT, SCENE_CONTEXT,
            self_check_warnings=["source_text_span is empty"],
        )
        assert len(result) == 1
        assert cr.change_count == 1
