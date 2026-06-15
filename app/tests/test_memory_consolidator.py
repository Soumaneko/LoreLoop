import json

from app.core.llm import MockLLM
from app.domain.recall_memory.consolidator import MemoryConsolidator
from app.domain.recall_memory.schemas import Memory
from app.domain.recall_memory.store import MemoryStore
from app.workflows.consolidate_memories import consolidate_memories_workflow


def _memory(**overrides) -> Memory:
    defaults = {
        "layer": "claim",
        "subtype": "rumor",
        "subject": "钥匙在钟楼的传闻",
        "content": "老板声称钥匙在钟楼。",
        "entities": ["老板", "钥匙", "钟楼"],
        "location": "旧旅店",
        "time_label": "第2日晚上",
        "visibility": ["玩家", "老板"],
        "tags": ["钥匙"],
        "confidence": 0.7,
        "salience": 0.6,
        "source_scene_id": "scene_001",
        "source_text_span": ["老板声称钥匙在钟楼。"],
        "source_pending_ids": ["pending_a"],
    }
    defaults.update(overrides)
    return Memory(**defaults)


class TestMemoryConsolidatorGrouping:
    def test_build_groups_caps_group_size_at_five(self):
        with MemoryStore(":memory:") as store:
            store.save_many([
                _memory(subject="共同主题", content=f"内容 {index}", source_text_span=[f"证据 {index}"])
                for index in range(6)
            ])

            groups = MemoryConsolidator(MockLLM().build_gateway()).build_groups(
                store.list_active(),
                max_group_size=5,
            )

            assert [len(group.memories) for group in groups] == [5]

    def test_generic_player_entity_does_not_group_by_itself(self):
        with MemoryStore(":memory:") as store:
            store.save_many([
                _memory(subject="主题A", content="内容A", entities=["玩家"], location="地点A"),
                _memory(subject="主题B", content="内容B", entities=["玩家"], location="地点B"),
            ])

            groups = MemoryConsolidator(MockLLM().build_gateway()).build_groups(
                store.list_active()
            )

            assert groups == []


class TestConsolidateMemoriesWorkflow:
    def test_merges_and_deactivates_source_memories(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "proposals": [
                        {
                            "group_id": "group_1",
                            "action": "merge",
                            "source_memory_ids": ["mem_a", "mem_b"],
                            "layer": "claim",
                            "subtype": "rumor",
                            "subject": "钥匙在钟楼的传闻",
                            "content": "老板和侍者都声称钥匙在钟楼。",
                            "location": "旧旅店",
                            "time_label": "第2日晚上",
                            "owner": None,
                            "tags": ["传闻"],
                            "reason": "两条记忆描述同一传闻。",
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])

        with MemoryStore(":memory:") as store:
            ids = store.save_many([
                _memory(
                    subject="钥匙在钟楼的传闻",
                    content="老板声称钥匙在钟楼。",
                    entities=["老板", "钥匙", "钟楼"],
                    confidence=0.7,
                    salience=0.6,
                    source_text_span=["老板声称钥匙在钟楼。", "老板压低声音。"],
                    source_pending_ids=["pending_a"],
                ),
                _memory(
                    subject="钥匙在钟楼的传闻",
                    content="侍者也听说钥匙在钟楼。",
                    entities=["侍者", "钥匙", "钟楼"],
                    visibility=["玩家", "侍者"],
                    confidence=0.9,
                    salience=0.8,
                    source_scene_id="scene_002",
                    source_text_span=["侍者也听说钥匙在钟楼。"],
                    source_pending_ids=["pending_b"],
                ),
            ])

            # Stable ids make the mock proposal easy to read.
            store._conn.execute("UPDATE memories SET id = ? WHERE id = ?", ("mem_a", ids[0]))
            store._conn.execute("UPDATE memories SET id = ? WHERE id = ?", ("mem_b", ids[1]))
            store._conn.commit()

            result = consolidate_memories_workflow(
                store=store,
                consolidator=MemoryConsolidator(mock.build_gateway()),
            )

            assert result.merged_count == 1
            assert result.deactivated_count == 2
            assert store.get_by_id("mem_a").active is False
            assert store.get_by_id("mem_b").active is False

            active = store.list_active()
            assert len(active) == 1
            merged = active[0]
            assert merged.subject == "钥匙在钟楼的传闻"
            assert merged.content == "老板和侍者都声称钥匙在钟楼。"
            assert merged.confidence == 0.9
            assert merged.salience == 0.8
            assert set(merged.entities) == {"老板", "侍者", "钥匙", "钟楼"}
            assert set(merged.visibility) == {"玩家", "老板", "侍者"}
            assert merged.source_text_span == [
                "老板声称钥匙在钟楼。",
                "老板压低声音。",
                "侍者也听说钥匙在钟楼。",
            ]
            assert merged.source_pending_ids == ["pending_a", "pending_b"]
