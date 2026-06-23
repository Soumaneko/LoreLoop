import json
from types import SimpleNamespace

from app.core.llm import MockLLM
from app.domain.current_state import CurrentState, InventoryItem, NPCState, PlayerState, ResourcesState, SceneState
from app.domain.entity.linker import EntityMemoryLinker
from app.domain.entity.extractor import EntityExtractor
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
    subject: str = "米莉",
):
    return SimpleNamespace(
        id=memory_id,
        layer="truth",
        subtype="fact",
        subject=subject,
        content="米莉知道仓库区的传闻。",
        entities=entities or ["米莉"],
        confidence=1.0,
        salience=0.7,
    )


class TestEntityStore:
    def test_upsert_and_get_by_name_alias(self):
        store = EntityStore(":memory:")
        profile = EntityProfile(
            entity_type="npc",
            name="米莉",
            aliases=["酒馆侍者"],
            summary="北郡酒馆的侍者。",
        )

        entity_id = store.upsert(profile)

        assert store.get_by_id(entity_id).name == "米莉"
        assert store.get_by_name("米莉").entity_id == entity_id
        assert store.get_by_name("酒馆侍者").entity_id == entity_id

    def test_find_by_name_fuzzy_substring(self):
        store = EntityStore(":memory:")
        store.upsert(
            EntityProfile(
                entity_type="npc",
                name="莎拉·月刃",
                aliases=["莎拉"],
                confidence=0.7,
            )
        )

        matches = store.find_by_name_fuzzy("莎拉")

        assert len(matches) == 1
        assert matches[0].name == "莎拉·月刃"

    def test_link_memory_idempotent_and_append_source(self):
        store = EntityStore(":memory:")
        profile = EntityProfile(entity_type="npc", name="米莉")
        entity_id = store.upsert(profile)

        assert store.link_memory(entity_id, "mem_1") is True
        assert store.link_memory(entity_id, "mem_1") is False
        store.append_source_memory(entity_id, "mem_1")
        store.append_source_memory(entity_id, "mem_1")

        assert store.get_linked_memory_ids(entity_id) == ["mem_1"]
        assert store.get_by_id(entity_id).source_memory_ids == ["mem_1"]

    def test_append_alias_is_idempotent(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(EntityProfile(entity_type="npc", name="米莉"))

        assert store.append_alias(entity_id, "红发女侍者") is True
        assert store.append_alias(entity_id, "红发女侍者") is False

        assert store.get_by_id(entity_id).aliases == ["红发女侍者"]


class TestEntityExtractor:
    def test_extracts_alias_matches_and_retrieved_entities(self):
        existing = EntityProfile(
            entity_id="ent_milly",
            entity_type="npc",
            name="米莉",
            aliases=["酒馆侍者"],
        )
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "红发女侍者",
                            "matched_entity_index": 99,
                            "matched_entity_name": "米莉",
                            "matched_entity_type": "npc",
                            "new_alias": "红发女侍者",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "米莉",
                            "entity_type": "npc",
                            "source": "alias_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "米莉",
                        },
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "new",
                            "context_span": "红发女侍者递来一张旧地图。",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        ])

        result = EntityExtractor(mock.build_gateway()).extract(
            scene_text="红发女侍者递来一张旧地图。",
            existing_profiles=[existing],
        )

        assert isinstance(result, EntityExtractionResult)
        assert result.alias_matches[0].new_alias == "红发女侍者"
        assert result.retrieved_entities[0].source == "alias_match"
        assert result.retrieved_entities[1].source == "new"


class TestEntityMemoryLinker:
    def test_exact_name_match_creates_link(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(EntityProfile(entity_type="npc", name="米莉"))

        links = EntityMemoryLinker(store).link_records([_record()])

        assert links == [(entity_id, "mem_1")]
        assert store.get_linked_memory_ids(entity_id) == ["mem_1"]

    def test_alias_match_creates_link(self):
        store = EntityStore(":memory:")
        entity_id = store.upsert(
            EntityProfile(entity_type="npc", name="米莉", aliases=["酒馆侍者"])
        )

        links = EntityMemoryLinker(store).link_records([
            _record(entities=["酒馆侍者"])
        ])

        assert links == [(entity_id, "mem_1")]

    def test_generic_terms_are_skipped(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="npc", name="米莉"))

        links = EntityMemoryLinker(store).link_records([
            _record(entities=["玩家"], subject="玩家")
        ])

        assert links == []


class TestEntityProfileGathering:
    def test_gathers_from_current_state_and_anchor_name_alias_only(self):
        store = EntityStore(":memory:")
        milly_id = store.upsert(EntityProfile(entity_type="npc", name="米莉", aliases=["酒馆侍者"]))
        map_id = store.upsert(EntityProfile(entity_type="item", name="旧地图", aliases=["破旧地图"]))
        store.upsert(EntityProfile(entity_type="npc", name="无关记忆人物"))

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

        assert [profile.entity_id for profile in profiles] == [milly_id, map_id]

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
                            "entity_type": "npc",
                            "name": "米莉",
                            "aliases": ["酒馆侍者"],
                            "summary": "米莉是北郡酒馆中谨慎友善的侍者。",
                            "role": "酒馆侍者",
                            "personality_traits": ["谨慎", "友善"],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ])
        existing = EntityProfile(
            entity_type="npc",
            name="酒馆侍者",
            aliases=["米莉"],
            confidence=0.5,
        )

        result = EntityProfileUpdater(mock.build_gateway()).update(
            "米莉压低声音提供线索。",
            _make_state(),
            relevant_profiles=[existing],
        )

        assert len(result) == 1
        assert result[0].entity_id == existing.entity_id
        assert result[0].name == "米莉"
        assert "酒馆侍者" in result[0].aliases
        assert result[0].confidence == 0.55
        assert result[0].source_scene_ids == ["scene_001"]

    def test_item_source_is_preserved(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_type": "item",
                            "name": "旧地图",
                            "aliases": ["破旧羊皮纸"],
                            "item_type": "地图",
                            "item_source": "从米莉处获得",
                            "description": "一张标注了仓库区路线的旧地图。",
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
                            "entity_type": "location",
                            "name": "北郡修道院",
                            "aliases": [],
                            "summary": "北郡的重要修道院。",
                            "location_type": "修道院",
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
        )

        assert len(result) == 1
        assert result[0].name == "北郡修道院"
        assert result[0].entity_type == "location"


class TestUpdateEntityProfilesWorkflow:
    def test_workflow_updates_aliases_and_only_updates_retrieved_profiles(self):
        store = EntityStore(":memory:")
        existing_id = store.upsert(EntityProfile(entity_type="npc", name="米莉"))
        unrelated_id = store.upsert(EntityProfile(entity_type="npc", name="莎拉"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "红发女侍者",
                            "matched_entity_index": 0,
                            "matched_entity_name": "米莉",
                            "matched_entity_type": "npc",
                            "new_alias": "红发女侍者",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "米莉",
                            "entity_type": "npc",
                            "source": "alias_match",
                            "matched_entity_index": 0,
                            "matched_entity_name": "米莉",
                        },
                        {
                            "name": "旧地图",
                            "entity_type": "item",
                            "source": "new",
                            "context_span": "红发女侍者递来一张旧地图。",
                        },
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_type": "npc",
                            "name": "米莉",
                            "aliases": ["红发女侍者"],
                            "summary": "米莉是北郡酒馆的侍者。",
                        },
                        {
                            "entity_type": "item",
                            "name": "旧地图",
                            "aliases": [],
                            "item_type": "地图",
                            "description": "一张标注了仓库区路线的旧地图。",
                        },
                    ]
                },
                ensure_ascii=False,
            )
        ])

        saved = update_entity_profiles_workflow(
            "红发女侍者递来一张旧地图。",
            _make_state(),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids[0] == existing_id
        assert len(saved.saved_ids) == 2
        assert unrelated_id not in saved.saved_ids
        assert "红发女侍者" in store.get_by_id(existing_id).aliases
        assert store.get_by_id(existing_id).summary == "米莉是北郡酒馆的侍者。"

    def test_workflow_deduplicates_existing_entity_missed_by_extractor(self):
        store = EntityStore(":memory:")
        existing_id = store.upsert(EntityProfile(entity_type="npc", name="米莉"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "旧地图",
                            "entity_type": "item",
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
                            "entity_type": "npc",
                            "name": "米莉",
                            "aliases": [],
                            "summary": "米莉是北郡酒馆的侍者。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张旧地图。",
            _make_state(),
            mock.build_gateway(),
            store,
        )

        assert saved.saved_ids == [existing_id]
        assert store.count() == 1
        assert store.get_by_id(existing_id).summary == "米莉是北郡酒馆的侍者。"

    def test_workflow_recovers_when_extractor_uses_wrong_entity_index(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="npc", name="莎拉"))
        milly_id = store.upsert(EntityProfile(entity_type="npc", name="米莉", aliases=["酒馆侍者"]))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [
                        {
                            "extracted_text": "红发女侍者",
                            "matched_entity_index": 0,
                            "matched_entity_name": "米莉",
                            "matched_entity_type": "npc",
                            "new_alias": "红发女侍者",
                        }
                    ],
                    "retrieved_entities": [
                        {
                            "name": "米莉",
                            "entity_type": "npc",
                            "source": "alias_match",
                            "matched_entity_index": 99,
                            "matched_entity_name": "米莉",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {
                    "entities": [
                        {
                            "entity_type": "npc",
                            "name": "米莉",
                            "aliases": ["红发女侍者"],
                            "summary": "米莉是北郡酒馆的侍者。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "红发女侍者递来一张旧地图。",
            _make_state(),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.alias_matches[0].matched_entity_index != 99
        assert saved.extraction.alias_matches[0].matched_entity_name == "米莉"
        assert saved.extraction.retrieved_entities[0].matched_entity_index != 99
        assert saved.extraction.retrieved_entities[0].matched_entity_name == "米莉"
        assert saved.saved_ids == [milly_id]
        assert "红发女侍者" in store.get_by_id(milly_id).aliases

    def test_workflow_downgrades_bad_existing_match_to_new(self):
        store = EntityStore(":memory:")
        store.upsert(EntityProfile(entity_type="npc", name="米莉"))
        mock = MockLLM()
        mock.set_responses([
            json.dumps(
                {
                    "alias_matches": [],
                    "retrieved_entities": [
                        {
                            "name": "黑石信使",
                            "entity_type": "npc",
                            "source": "exact_match",
                            "matched_entity_index": 99,
                            "matched_entity_name": "不存在的人",
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
                            "entity_type": "npc",
                            "name": "黑石信使",
                            "aliases": [],
                            "summary": "黑石信使带来一封密封信。",
                        }
                    ]
                },
                ensure_ascii=False,
            ),
        ])

        saved = update_entity_profiles_workflow(
            "黑石信使把密封信交到吧台。",
            _make_state(),
            mock.build_gateway(),
            store,
        )

        assert saved.extraction.retrieved_entities[0].source == "new"
        assert saved.extraction.retrieved_entities[0].matched_entity_index is None
        assert store.count() == 2
