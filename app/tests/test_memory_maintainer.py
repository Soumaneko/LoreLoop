import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.recall_memory.maintainer import MemoryMaintainer
from app.domain.recall_memory.maintenance import maintain_memories_workflow
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.store import MemoryStore
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.store import SceneSummaryStore


def _memory(**overrides) -> Memory:
    values = {
        "memory_kind": "commitment",
        "evidence_mode": "observed",
        "subject": "偿还药费的承诺",
        "content": "米莉承诺在抵达港口后偿还主角三枚银币。",
        "entities": ["米莉", "主角"],
        "owner": "米莉",
    }
    values.update(overrides)
    return Memory(**values)


def test_maintainer_invalidates_memory_and_uses_inactive_l1_summary():
    mock = MockLLM()

    with MemoryStore(":memory:") as memory_store, SceneSummaryStore(
        ":memory:"
    ) as summary_store:
        saved_id = memory_store.save_one_returning_id(_memory())
        mock.set_responses(
            [
                json.dumps(
                    {
                        "proposals": [
                            {
                                "action": "invalidate",
                                "source_memory_ids": [saved_id],
                                "reason": "米莉已经偿还三枚银币，承诺完成。",
                                "evidence_summary_ids": ["summary-1"],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        summary_store.save_one(
            SceneSummary(
                id="summary-1",
                level=1,
                content="抵达港口后，米莉把三枚银币交给主角，结清药费。",
                scene_ids=["scene-6"],
                turn_start=6,
                turn_end=6,
                active=False,
                condensed_into="summary-l2",
            )
        )

        result = maintain_memories_workflow(
            store=memory_store,
            summary_store=summary_store,
            maintainer=MemoryMaintainer(mock.build_gateway()),
            core_memory=CoreMemory(items=[]),
            through_turn=6,
        )

        record = memory_store.get_by_id(saved_id)
        active_ids = [memory.id for memory in memory_store.list_active()]

    assert result.summary_count == 1
    assert result.cursor_before == -1
    assert result.cursor_after == 6
    assert result.changed_count == 1
    assert record is not None
    assert record.status == "invalidated"
    assert "偿还" in record.status_reason
    assert saved_id not in active_ids


def test_failed_proposal_does_not_advance_summary_cursor():
    mock = MockLLM()
    mock.set_responses(
        [
            json.dumps(
                {
                    "proposals": [
                        {
                            "action": "invalidate",
                            "source_memory_ids": ["known-memory"],
                            "reason": "这条本可正常失效。",
                            "evidence_summary_ids": ["summary-2"],
                        },
                        {
                            "action": "invalidate",
                            "source_memory_ids": ["unknown-memory"],
                            "reason": "测试未知记忆。",
                            "evidence_summary_ids": ["summary-2"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ]
    )

    with MemoryStore(":memory:") as memory_store, SceneSummaryStore(
        ":memory:"
    ) as summary_store:
        saved_id = memory_store.save_one_returning_id(_memory())
        memory_store._conn.execute(
            "UPDATE memories SET id = ? WHERE id = ?",
            ("known-memory", saved_id),
        )
        memory_store._conn.commit()
        summary_store.save_one(
            SceneSummary(
                id="summary-2",
                level=1,
                content="发生了新的后续证据。",
                scene_ids=["scene-2"],
                turn_start=2,
                turn_end=2,
            )
        )

        result = maintain_memories_workflow(
            store=memory_store,
            summary_store=summary_store,
            maintainer=MemoryMaintainer(mock.build_gateway()),
            core_memory=CoreMemory(items=[]),
        )

        cursor = memory_store.get_meta_int("memory_maintainer_last_turn", -1)
        known = memory_store.get_by_id("known-memory")

    assert all(item.skipped_reason for item in result.apply_results)
    assert result.cursor_after == -1
    assert cursor == -1
    assert known is not None
    assert known.status == "active"


def test_maintainer_prompt_can_narrow_overbroad_memories():
    prompt = Path("app/prompts/memory_maintain_v1.md").read_text(encoding="utf-8")

    assert "content 可能过宽" in prompt
    assert "supersede 收窄" in prompt
    assert "直接 discard_summary_like" in prompt
    assert "不需要新剧情推翻" in prompt
    assert "source_text_span/L1 summaries 只支持" in prompt
    assert "合伙人、潜在合伙人、盟友、根本转变、完全信任" in prompt
    assert "即使 memory_kind 不同" in prompt
    assert "不得分别 keep" in prompt
