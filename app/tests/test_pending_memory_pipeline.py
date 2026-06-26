import json

from app.core.llm import MockLLM
from app.domain.action.schemas import ActionParseResult, Anchor, AtomicAction
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState, WorldClock
from app.domain.recall_memory.batch_classifier import MemoryBatchClassifier
from app.domain.recall_memory.draft_extractor import MemoryDraftExtractor
from app.domain.recall_memory.pending_store import PendingMemoryStore
from app.domain.recall_memory.schemas import PendingMemoryItem
from app.domain.recall_memory.store import MemoryStore
from app.workflows.extract_memory import (
    batch_classify_pending_memories_workflow,
    extract_memory_drafts_workflow,
    fill_pending_memory_scene_metadata,
)
from app.workflows.retrieve_candidates import retrieve_candidates_workflow


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="旧旅店",
            participants=["主角", "老板"],
        ),
        world_clock=WorldClock(day=2, phase="晚上"),
        player=PlayerState(name="主角"),
    )


def _action_parse() -> ActionParseResult:
    return ActionParseResult(
        turn_intent="询问老板钥匙的传闻",
        atomic_actions=[
            AtomicAction(
                action_id="act_1",
                sequence=1,
                actor="主角",
                action_type="speak",
                raw_text="询问老板钥匙的传闻",
                verb="询问",
                target="老板",
                goal="了解钥匙",
                likely_needs_roll=False,
                confidence=0.9,
            )
        ],
        anchors=[
            Anchor(
                text="钥匙",
                normalized="钥匙",
                kind="item",
                source="explicit",
                confidence=0.9,
            )
        ],
    )


class TestPendingMemoryStore:
    def test_save_list_and_trigger_by_distinct_turns(self):
        with PendingMemoryStore(":memory:") as store:
            ids = store.save_many(
                [
                    PendingMemoryItem(
                        scene_id=f"scene_{i}",
                        turn_index=i,
                        raw_text=f"候选 {i}",
                        source_text_span=[f"证据 {i}"],
                    )
                    for i in range(1, 6)
                ]
            )

            assert len(ids) == 5
            assert store.unclassified_turn_count() == 5
            assert store.should_trigger_batch(turn_threshold=5)

            store.mark_classified(ids[:2])
            assert store.unclassified_turn_count() == 3

    def test_source_text_span_list_round_trips(self):
        with PendingMemoryStore(":memory:") as store:
            store.save_many(
                [
                    PendingMemoryItem(
                        scene_id="scene_001",
                        turn_index=1,
                        raw_text="老板和侍者都提到钥匙。",
                        source_text_span=[
                            "老板说钥匙在钟楼。",
                            "侍者也说钥匙在钟楼。",
                        ],
                    )
                ]
            )

            item = store.list_unclassified()[0]

            assert item.source_text_span == [
                "老板说钥匙在钟楼。",
                "侍者也说钥匙在钟楼。",
            ]

    def test_batch_classify_count_persists_and_triggers_consolidation(self, tmp_path):
        db_path = str(tmp_path / "pending.db")

        with PendingMemoryStore(db_path) as store:
            assert store.batch_classify_count() == 0
            assert store.should_trigger_consolidation(every=3) is False
            assert store.record_batch_classified() == 1
            assert store.record_batch_classified() == 2
            assert store.should_trigger_consolidation(every=3) is False
            assert store.record_batch_classified() == 3
            assert store.should_trigger_consolidation(every=3) is True

        with PendingMemoryStore(db_path) as store:
            assert store.batch_classify_count() == 3
            assert store.should_trigger_consolidation(every=3) is True


class TestMemoryDraftExtractor:
    def test_extracts_pending_items_and_fills_scene_metadata(self):
        mock = MockLLM()
        mock.set_responses(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "raw_text": "老板声称钥匙被送往钟楼。",
                                "source_text_span": [
                                    "老板压低声音说，钥匙被送往钟楼。"
                                ],
                                "entities": ["老板", "钥匙", "钟楼"],
                                "extractor_hint": "reported_info",
                                "importance_hint": "high",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        extractor = MemoryDraftExtractor(mock.build_gateway())

        with PendingMemoryStore(":memory:") as pending_store:
            result = extract_memory_drafts_workflow(
                scene_text="老板压低声音说，钥匙被送往钟楼。",
                current_state=_state(),
                core_memory=CoreMemory(items=[]),
                turn_index=7,
                extractor=extractor,
                pending_store=pending_store,
            )

            assert len(result.pending_items) == 1
            item = result.pending_items[0]
            assert item.scene_id == "scene_001"
            assert item.turn_index == 7
            assert item.location == "旧旅店"
            assert item.time_label == "第2日晚上"
            assert item.source_text_span == ["老板压低声音说，钥匙被送往钟楼。"]
            assert item.classified is False
            assert pending_store.count() == 1

    def test_extract_discards_model_generated_location_and_time_label(self):
        mock = MockLLM()
        mock.set_responses(
            [
                json.dumps(
                    {
                        "items": [
                            {
                                "raw_text": "老板声称钥匙被送往钟楼。",
                                "source_text_span": ["老板压低声音说，钥匙被送往钟楼。"],
                                "entities": ["老板", "钥匙", "钟楼"],
                                "extractor_hint": "reported_info",
                                "importance_hint": "high",
                                "location": "模型瞎编的地点",
                                "time_label": "第999日凌晨",
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )
        extractor = MemoryDraftExtractor(mock.build_gateway())

        result = extractor.extract(
            "老板压低声音说，钥匙被送往钟楼。",
            _state(),
            core_memory=CoreMemory(items=[]),
            turn_index=7,
        )

        assert len(result.items) == 1
        item = result.items[0]
        assert item.scene_id == "scene_001"
        assert item.turn_index == 7
        assert item.location is None
        assert item.time_label is None

    def test_scene_metadata_can_be_filled_from_updated_state_after_extract(self):
        item = PendingMemoryItem(
            scene_id="scene_001",
            turn_index=8,
            raw_text="老板声称钥匙在钟楼。",
            source_text_span=["老板声称钥匙在钟楼。"],
            location=None,
            time_label=None,
        )
        updated_state = CurrentState(
            scene=SceneState(
                scene_id="scene_002",
                location="钟楼门厅",
                participants=["主角"],
            ),
            world_clock=WorldClock(day=2, phase="深夜"),
            player=PlayerState(name="主角"),
        )

        filled = fill_pending_memory_scene_metadata([item], updated_state)

        assert filled[0].location == "钟楼门厅"
        assert filled[0].time_label == "第2日深夜"


class TestBatchClassifyPendingMemories:
    def test_batch_classifier_merges_pending_items_and_saves_memory(self):
        state = _state()
        pending_a = PendingMemoryItem(
            id="pending_a",
            scene_id="scene_001",
            turn_index=1,
            raw_text="老板声称钥匙被送往钟楼。",
            source_text_span=["老板说钥匙被送往钟楼。"],
            entities=["老板", "钥匙", "钟楼"],
            extractor_hint="reported_info",
        )
        pending_b = PendingMemoryItem(
            id="pending_b",
            scene_id="scene_002",
            turn_index=2,
            raw_text="侍者也听说钥匙在钟楼。",
            source_text_span=["侍者补充说，他也听说钥匙在钟楼。"],
            entities=["侍者", "钥匙", "钟楼"],
            extractor_hint="reported_info",
        )
        mock = MockLLM()
        mock.set_responses(
            [
                json.dumps(
                    {
                        "candidates": [
                            {
                                "keep": True,
                                "layer": "claim",
                                "subtype": "rumor",
                                "subject": "钥匙在钟楼的传闻",
                                "content": "老板和侍者都提到钥匙可能在钟楼。",
                                "entities": ["老板", "侍者", "钥匙", "钟楼"],
                                "location": "旧旅店",
                                "time_label": "第2日晚上",
                                "owner": None,
                                "visibility": ["主角", "老板", "侍者"],
                                "confidence": 0.9,
                                "salience": 0.8,
                                "tags": ["钥匙", "传闻"],
                                "source_pending_ids": ["pending_a", "pending_b"],
                                "source_scene_id": "scene_001",
                                "source_text_span": [
                                    "老板说钥匙被送往钟楼。",
                                    "侍者补充说，他也听说钥匙在钟楼。",
                                ],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            ]
        )

        with PendingMemoryStore(":memory:") as pending_store, MemoryStore(":memory:") as memory_store:
            pending_store.save_many([pending_a, pending_b])
            result = batch_classify_pending_memories_workflow(
                current_state=state,
                core_memory=CoreMemory(items=[]),
                classifier=MemoryBatchClassifier(mock.build_gateway()),
                pending_store=pending_store,
                memory_store=memory_store,
                force=True,
            )

            assert result.triggered is True
            assert len(result.saved_ids) == 1
            assert pending_store.unclassified_turn_count() == 0
            assert result.batch_classify_count == 1
            assert result.should_maintain is True

            memory = memory_store.list_all()[0]
            assert memory.memory_kind == "belief"
            assert memory.evidence_mode == "reported"
            assert memory.confidence == 0.9
            assert memory.source_pending_ids == ["pending_a", "pending_b"]
            assert len(memory.source_text_span) == 2


class TestRetrievalWithPendingNotes:
    def test_retrieval_keeps_pending_notes_separate_from_classified_memories(self):
        with MemoryStore(":memory:") as memory_store, PendingMemoryStore(":memory:") as pending_store:
            pending_store.save_many(
                [
                    PendingMemoryItem(
                        scene_id="scene_001",
                        turn_index=3,
                        raw_text="老板声称钥匙在钟楼。",
                        source_text_span=[
                            "老板声称钥匙在钟楼。",
                            "他指了指钟楼方向。",
                        ],
                        entities=["老板", "钥匙", "钟楼"],
                        extractor_hint="reported_info",
                        importance_hint="high",
                    )
                ]
            )

            result = retrieve_candidates_workflow(
                _action_parse(),
                memory_store,
                pending_memory_store=pending_store,
            )

            assert result.candidates == []
            assert len(result.pending_notes) == 1
            assert result.pending_notes[0].raw_text == "老板声称钥匙在钟楼。"
            assert result.pending_notes[0].source_text_span == [
                "老板声称钥匙在钟楼。",
                "他指了指钟楼方向。",
            ]
