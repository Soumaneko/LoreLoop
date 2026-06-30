import json
from types import SimpleNamespace

from app.core.llm import MockLLM
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, InventoryItem, NPCState, PlayerState, ResourcesState, SceneState
from app.domain.entity.linker import EntityMemoryLinker
from app.domain.entity.extractor import EntityExtractor, _profile_for_extraction
from app.domain.entity.schemas import (
    EntityExtractionResult,
    EntityProfile,
    EntityProfileUpdateInput,
    RetrievedEntity,
)
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater
from app.domain.action.schemas import Anchor
from app.workflows.update_entity_profiles import (
    gather_entity_profiles_from_state_and_anchors,
    update_entity_profiles_from_extraction,
    update_entity_profiles_workflow,
)


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院",
            participants=["卢西安", "米莉"],
        ),
        player=PlayerState(name="卢西安"),
        npcs=[NPCState(name="米莉", role="酒馆侍者")],
        resources=ResourcesState(inventory=[InventoryItem(name="旧地图")]),
    )


def _record(
    memory_id: str = "mem_1",
    entities: list[str] | None = None,
    subject: str = "旧地图",
):
    return SimpleNamespace(
        id=memory_id,
        memory_kind="open_thread", evidence_mode="observed",
        subject=subject,
        content="旧地图标注了仓库区的路线。",
        entities=entities or ["旧地图"],
        confidence=1.0,
        salience=0.7,
    )


class TestEntityStore:
    def test_upsert_and_get_by_name_alias(self):
        store = EntityStore(":memory:")
        profile = EntityProfile(
            entity_type="item",
            name="旧地图",
            aliases=["破旧地图"],
            summary="一张标注了仓库区路线的旧地图。",
        )

        entity_id = store.upsert(profile)

        assert store.get_by_id(entity_id).name == "旧地图"
        assert store.get_by_name("旧地图").entity_id == entity_id
        assert store.get_by_name("破旧地图").entity_id == entity_id

    def test_find_by_name_fuzzy_substring(self):
        store = EntityStore(":memory:")
        store.upsert(
            EntityProfile(
                entity_type="organization",
                name="银月商会",
                aliases=["商会"],
                confidence=0.7,
            )
        )

        matches = store.find_by_name_fuzzy("银月")

        assert len(matches) == 1
        assert matches[0].name == "银月商会"

    def test_link_memory_idempotent_and_append_source(self):
        store = EntityStore(":memory:")
        profile = EntityProfile(entity_type="item", name="旧地图")
        entity_id = store.upsert(profile)

        assert store.link_memory(entity_id, "mem_1") is True
        assert store.link_memory(entity_id, "mem_1") is False
        store.append_source_memory(entity_id, "mem_1")
        store.append_source_memory(entity_id, "mem_1")

        assert store.get_linked_memory_ids(entity_id) == ["mem_1"]
        assert store.get_by_id(entity_id).source_memory_ids == ["mem_1"]

    def test_append_alias_is_idempotent(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))

        assert store.append_alias(entity_id, "破旧地图") is True
        assert store.append_alias(entity_id, "破旧地图") is False

        assert store.get_by_id(entity_id).aliases == ["破旧地图"]

    def test_entity_hit_counts_are_not_prompt_semantic_fields(self):
        profile = EntityProfile(
            entity_type="item",
            name="旧地图",
            aliases=["破旧地图"],
            name_hit_count=3,
            alias_hit_counts={"破旧地图": 2},
        )

        view = profile.semantic_view()

        assert not hasattr(view, "name_hit_count")
        assert not hasattr(view, "alias_hit_counts")

    def test_alias_promotes_when_hits_exceed_name_for_any_entity_type(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(
            EntityProfile(entity_type="item", name="旧图纸", aliases=["星钥蓝图"])
        )

        assert store.record_mention(entity_id, "星钥蓝图") is True
        assert store.record_mention(entity_id, "星钥蓝图") is True

        profile = store.get_by_id(entity_id)
        assert profile.name == "星钥蓝图"
        assert "旧图纸" in profile.aliases
        assert store.get_by_name("旧图纸").entity_id == entity_id
        assert store.get_by_name("星钥蓝图").entity_id == entity_id
        assert profile.name_hit_count == 2
        assert profile.alias_hit_counts["旧图纸"] == 1


class TestEntityExtractor:
    def test_existing_profile_prompt_only_contains_identity_fields(self):
        existing = EntityProfile(
            entity_id="ent_map",
            entity_type="item",
            name="旧地图",
            aliases=["破旧地图"],
            summary="不应传给实体提取器的完整档案。",
            confidence=0.9,
        )

        projected = _profile_for_extraction(0, existing)

        assert projected == {
            "entity_index": 0,
            "entity_type": "item",
            "name": "旧地图",
            "aliases": ["破旧地图"],
        }

    def test_extracts_alias_matches_and_retrieved_entities(self):
        existing = EntityProfile(
            entity_id="ent_map",
            entity_type="item",
            name="旧地图",
            aliases=["破旧地图"],
        )
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "破旧羊皮纸",
                            "matched_entity_index": 99,
                            "matched_entity_name": "旧地图",
                            "matched_entity_type": "item",
                            "new_alias": "破旧羊皮纸",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "alias_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                        },
                        {
                            "name": "银钢匕首",
                            "entity_type": "item",
                            "source": "new",
                            "context_span": "米莉递来一张旧地图和一把银钢匕首。",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        ])

        result = EntityExtractor(mock.build_gateway()).extract(
            core_memory=CoreMemory(items=[]),
            scene_text="米莉递来一张破旧羊皮纸。",
            existing_profiles=[existing],
        )

        assert isinstance(result, EntityExtractionResult)
        assert result.alias_matches[0].new_alias == "破旧羊皮纸"
        assert result.retrieved_entities[0].source == "alias_match"
        assert result.retrieved_entities[1].source == "new"


class TestEntityMemoryLinker:
    def test_exact_name_match_creates_link(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))

        links = EntityMemoryLinker(store).link_records([_record()])

        assert links == [(entity_id, "mem_1")]
        assert store.get_linked_memory_ids(entity_id) == ["mem_1"]

    def test_alias_match_creates_link(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(
            EntityProfile(entity_type="item", name="旧地图", aliases=["破旧地图"])
        )

        links = EntityMemoryLinker(store).link_records([
            _record(entities=["破旧地图"])
        ])

        assert links == [(entity_id, "mem_1")]

    def test_generic_terms_are_skipped(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="item", name="旧地图"))

        links = EntityMemoryLinker(store).link_records([
            _record(entities=["玩家"], subject="玩家")
        ])

        assert links == []


class TestEntityProfileGathering:
    def test_gathers_from_current_state_and_anchor_name_alias_only(self):
        store = EntityStore(":memory:")
        map_id = store.upsert(EntityProfile(entity_type="item", name="旧地图", aliases=["破旧地图"]))
        store.upsert(EntityProfile(entity_type="organization", name="无关组织"))

        profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=_make_state(),
            anchors=[
                Anchor(
                    text="破旧地图",
                    normalized="破旧地图",
                    kind="item",
                    source="explicit",
                    confidence=0.9,
                ),
                Anchor(
                    text="无关记忆人物传闻",
                    normalized="无关记忆人物传闻",
                    kind="concept",
                    source="explicit",
                    confidence=0.9,
                ),
            ],
            entity_store=store,
        )

        assert [profile.entity_id for profile in profiles] == [map_id]

    def test_gather_does_not_use_fuzzy_substring(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="location", name="暴风城王宫"))

        profiles = gather_entity_profiles_from_state_and_anchors(
            current_state=CurrentState(
                scene=SceneState(scene_id="scene_x", location="别处", participants=[]),
                player=PlayerState(name="卢西安"),
            ),
            anchors=[
                Anchor(
                    text="王宫",
                    normalized="王宫",
                    kind="location",
                    source="explicit",
                    confidence=0.9,
                )
            ],
            entity_store=store,
        )

        assert profiles == []


class TestEntityProfileUpdater:
    def test_merges_returned_entity_by_alias(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "entities": [
                        {
                                "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "旧地图",
                                "aliases": ["破旧地图"],
                                "summary": "一张标注了仓库区路线的旧地图。",
                                "item_type": "地图",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])
        existing = EntityProfile(
            entity_type="item",
            name="破旧地图",
            aliases=["旧地图"],
            confidence=0.5,
        )

        result = EntityProfileUpdater(mock.build_gateway()).update(
            "卢西安展开旧地图查看仓库区路线。",
            _make_state(),
            relevant_profiles=[existing],
            core_memory=CoreMemory(items=[]),
        )

        assert len(result) == 1
        assert result[0].entity_id == existing.entity_id
        assert result[0].name == "旧地图"
        assert "破旧地图" in result[0].aliases
        assert result[0].confidence == 0.55
        assert result[0].source_scene_ids == ["scene_001"]

    def test_item_source_is_preserved(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "旧地图",
                                "aliases": ["破旧羊皮纸"],
                                "item_type": "地图",
                                "item_source": "从米莉处获得",
                                "description": "一张标注了仓库区路线的旧地图。",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])
        existing = EntityProfile(
            entity_type="item",
            name="旧地图",
            aliases=["破旧羊皮纸"],
            item_type="杂物",
            confidence=0.5,
        )

        result = EntityProfileUpdater(mock.build_gateway()).update(
            "米莉从围裙口袋掏出一张旧地图递给卢西安。",
            _make_state(),
            relevant_profiles=[existing],
            core_memory=CoreMemory(items=[]),
        )

        assert len(result) == 1
        assert result[0].entity_id == existing.entity_id
        assert result[0].name == "旧地图"
        assert result[0].item_source == "从米莉处获得"
        assert result[0].item_type == "地图"

    def test_creates_new_entity_when_no_alias_matches(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "location",
                                "name": "北郡修道院",
                                "aliases": [],
                                "summary": "北郡的重要修道院。",
                                "location_type": "修道院",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])

        result = EntityProfileUpdater(mock.build_gateway()).update(
            "北郡修道院的钟声响起。",
            _make_state(),
            relevant_profiles=[],
            new_entity_hints=[
                RetrievedEntity(
                    name="北郡修道院",
                    entity_type="location",
                    source="new",
                    context_span="北郡修道院的钟声响起。",
                )
            ],
            core_memory=CoreMemory(items=[]),
        )

        assert len(result) == 1
        assert result[0].name == "北郡修道院"
        assert result[0].entity_type == "location"
        assert result[0].name_hit_count == 1


class TestUpdateEntityProfilesWorkflow:
    def test_workflow_updates_aliases_and_only_updates_retrieved_profiles(self):
        store = EntityStore(":memory:")
        existing_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        unrelated_id = store.upsert(EntityProfile(entity_type="item", name="银钥匙"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "破旧羊皮纸",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                            "matched_entity_type": "item",
                            "new_alias": "破旧羊皮纸",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "alias_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                        },
                        {
                            "name": "银钢匕首",
                            "entity_type": "item",
                            "source": "new",
                            "context_span": "米莉递来一张旧地图和一把银钢匕首。",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "旧地图",
                                "aliases": ["破旧羊皮纸"],
                                "item_type": "地图",
                                "description": "一张标注了仓库区路线的旧地图。",
                            },
                        },
                        {
                            "entity_index": 1,
                            "profile": {
                                "entity_type": "item",
                                "name": "银钢匕首",
                                "aliases": [],
                                "item_type": "匕首",
                                "description": "一把银钢匕首。",
                            },
                        },
                    ]
                },
                ensure_ascii=False,
            )
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张破旧羊皮纸和一把银钢匕首，那羊皮纸其实是一张旧地图。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids[0] == existing_id
        assert len(saved.saved_ids) == 2
        assert unrelated_id not in saved.saved_ids
        assert "破旧羊皮纸" in store.get_by_id(existing_id).aliases
        assert store.get_by_id(existing_id).alias_hit_counts["破旧羊皮纸"] == 1
        assert store.get_by_id(existing_id).item_type == "地图"

    def test_workflow_counts_raw_alias_before_normalized_extraction(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(
            EntityProfile(
                entity_type="location",
                name="星塔旧址",
                aliases=["星钟塔"],
                alias_hit_counts={"星钟塔": 1},
            )
        )
        mock = MockLLM()
        mock.set_responses([
            json.dumps({"entities": []}, ensure_ascii=False),
        ])

        saved = update_entity_profiles_from_extraction(
            scene_text="星钟塔的顶层再次亮起蓝光。",
            current_state=_make_state(),
            core_memory=CoreMemory(items=[]),
            gateway=mock.build_gateway(),
            entity_store=store,
            existing_profiles=store.list_all(),
            extraction=EntityExtractionResult(
                alias_matches=[],
                retrieved_entities=[
                    RetrievedEntity(
                        name="星钟塔",
                        entity_type="location",
                        source="exact_match",
                        matched_entity_index=0,
                        matched_entity_name="星塔旧址",
                    )
                ],
            ),
        )

        assert saved.extraction.retrieved_entities[0].name == "星钟塔"
        assert store.get_by_id(entity_id).name == "星钟塔"
        assert store.get_by_id(entity_id).name_hit_count == 2

    def test_workflow_rejects_updater_entity_not_matching_extraction_candidate(self):
        store = EntityStore(":memory:")
        existing_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "location",
                            "source": "new",
                            "context_span": "米莉递来一张旧地图。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "旧地图",
                                "aliases": [],
                                "summary": "一张旧地图。",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张旧地图。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids == []
        assert store.count() == 1
        assert store.get_by_id(existing_id).summary is None

    def test_workflow_recovers_when_extractor_uses_wrong_entity_index(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="item", name="银钥匙"))
        map_id = store.upsert(EntityProfile(entity_type="item", name="旧地图", aliases=["破旧地图"]))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "破旧羊皮纸",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                            "matched_entity_type": "item",
                            "new_alias": "破旧羊皮纸",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "alias_match",
                            "matched_entity_index": 99,
                            "matched_entity_name": "旧地图",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "旧地图",
                                "aliases": ["破旧羊皮纸"],
                                "item_type": "地图",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张破旧羊皮纸。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.alias_matches[0].matched_entity_index != 99
        assert saved.extraction.alias_matches[0].matched_entity_name == "旧地图"
        assert saved.extraction.retrieved_entities[0].matched_entity_index != 99
        assert saved.extraction.retrieved_entities[0].matched_entity_name == "旧地图"
        assert saved.saved_ids == [map_id]
        assert "破旧羊皮纸" in store.get_by_id(map_id).aliases

    def test_workflow_downgrades_bad_existing_match_to_new(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "黑石密信",
                            "entity_type": "item",
                            "source": "exact_match",
                            "matched_entity_index": 99,
                            "matched_entity_name": "不存在的物品",
                            "context_span": "黑石信使把密封信交到吧台。",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "黑石密信",
                                "aliases": [],
                                "summary": "一封由黑石信使带来的密封信。",
                                "item_type": "信件",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "黑石信使把黑石密信交到吧台。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.retrieved_entities[0].source == "new"
        assert saved.extraction.retrieved_entities[0].matched_entity_index is None
        assert store.count() == 2

    def test_workflow_rejects_forged_update_index(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "exact_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 3,
                            "profile": {
                                "entity_type": "item",
                                "name": "凭空出现的物品",
                                "aliases": [],
                                "summary": "这不在候选列表里。",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张旧地图。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids == []
        assert store.count() == 1

    def test_workflow_rejects_existing_index_with_wrong_profile_name(self):
        store = EntityStore(":memory:")
        map_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "exact_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_index": 0,
                            "profile": {
                                "entity_type": "item",
                                "name": "银钥匙",
                                "aliases": [],
                                "summary": "错误地把旧地图改成了银钥匙。",
                            },
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张旧地图。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids == []
        assert store.get_by_id(map_id).name == "旧地图"
        assert store.get_by_id(map_id).summary is None

    def test_workflow_filters_generic_entities_and_aliases(self):
        store = EntityStore(":memory:")
        map_id = store.upsert(EntityProfile(entity_type="item", name="旧地图"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "任务单",
                            "matched_entity_index": 0,
                            "matched_entity_name": "旧地图",
                            "matched_entity_type": "item",
                            "new_alias": "任务单",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "镇公所",
                            "entity_type": "location",
                            "source": "new",
                            "context_span": "镇公所门开着。",
                        },
                        {
                            "name": "任务单",
                            "entity_type": "item",
                            "source": "new",
                            "context_span": "墙上贴着任务单。",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps({"entities": []}, ensure_ascii=False),
        ])

        saved = update_entity_profiles_workflow(
            "登记员指着任务单说镇公所门开着。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.alias_matches == []
        assert saved.extraction.retrieved_entities == []
        assert saved.saved_ids == []
        assert store.get_by_id(map_id).aliases == []

    def test_trace_turn_37_filters_existing_bad_entities(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="location", name="三丘农场"))
        store.upsert(EntityProfile(entity_type="location", name="镇东铁匠铺", aliases=["铁匠铺"]))
        store.upsert(EntityProfile(entity_type="location", name="北境"))
        store.upsert(EntityProfile(entity_type="item", name="密林古树实验笔记卷轴", aliases=["深棕色的卷轴"]))
        store.upsert(EntityProfile(entity_type="organization", name="冒险者公会"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "铁匠铺",
                            "matched_entity_index": 1,
                            "matched_entity_name": "镇东铁匠铺",
                            "matched_entity_type": "location",
                            "new_alias": "铁匠铺",
                        },
                        {
                            "extracted_text": "深棕色的卷轴",
                            "matched_entity_index": 3,
                            "matched_entity_name": "密林古树实验笔记卷轴",
                            "matched_entity_type": "item",
                            "new_alias": "深棕色的卷轴",
                        },
                    ],
                    "retrieved_entities": [
                        {
                            "name": "三丘农场",
                            "entity_type": "location",
                            "source": "exact_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "三丘农场",
                        },
                        {
                            "name": "镇东铁匠铺",
                            "entity_type": "location",
                            "source": "alias_match",
                            "matched_entity_index": 1,
                            "matched_entity_name": "镇东铁匠铺",
                        },
                        {
                            "name": "北境",
                            "entity_type": "location",
                            "source": "exact_match",
                            "matched_entity_index": 2,
                            "matched_entity_name": "北境",
                        },
                        {
                            "name": "密林古树实验笔记卷轴",
                            "entity_type": "item",
                            "source": "alias_match",
                            "matched_entity_index": 3,
                            "matched_entity_name": "密林古树实验笔记卷轴",
                        },
                        {
                            "name": "冒险者公会",
                            "entity_type": "organization",
                            "source": "exact_match",
                            "matched_entity_index": 4,
                            "matched_entity_name": "冒险者公会",
                        },
                        {
                            "name": "原谷物仓库",
                            "entity_type": "location",
                            "source": "new",
                            "context_span": "镇上以前那座谷物仓库——原谷物仓库，我记得好像是在东边？",
                        },
                        {
                            "name": "镇东旧城区",
                            "entity_type": "location",
                            "source": "new",
                            "context_span": "镇东旧城区。",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps({"entities": []}, ensure_ascii=False),
        ])

        saved = update_entity_profiles_workflow(
            "登记员提到铁匠铺、深棕色的卷轴、北境霜爪狼人、原谷物仓库和镇东旧城区。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        kept = {(entity.entity_type, entity.name) for entity in saved.extraction.retrieved_entities}
        assert ("location", "镇东铁匠铺") not in kept
        assert ("location", "原谷物仓库") in kept
        assert ("location", "镇东旧城区") in kept
        assert [match.extracted_text for match in saved.extraction.alias_matches] == ["深棕色的卷轴"]

    def test_existing_low_quality_entity_mentions_do_not_increment_counts(self):
        store = EntityStore(":memory:")
        bad_id = store.upsert(EntityProfile(entity_type="item", name="任务单"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "任务单",
                            "entity_type": "item",
                            "source": "exact_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "任务单",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps({"entities": []}, ensure_ascii=False),
        ])

        saved = update_entity_profiles_workflow(
            "任务单被登记员提到。",
            _make_state(),
            CoreMemory(items=[]),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.retrieved_entities == []
        assert store.get_by_id(bad_id).name_hit_count == 1
