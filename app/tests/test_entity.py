import json
from types import SimpleNamespace

from app.core.llm import MockLLM
from app.domain.current_state import CurrentState, NPCState, PlayerState, ResourcesState, SceneState
from app.domain.entity.linker import EntityMemoryLinker
from app.domain.entity.schemas import EntityProfile, EntityProfileUpdateInput
from app.domain.entity.store import EntityStore
from app.domain.entity.updater import EntityProfileUpdater
from app.workflows.update_entity_profiles import update_entity_profiles_workflow


def _make_state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院",
            participants=["卢西安", "米莉"],
        ),
        player=PlayerState(name="卢西安"),
        npcs=[NPCState(name="米莉", role="酒馆侍者")],
        resources=ResourcesState(inventory=["旧地图"]),
    )


def _record(memory_id: str = "mem_1", entities: list[str] | None = None):
    return SimpleNamespace(
        id=memory_id,
        type="fact",
        layer="truth",
        subtype="fact",
        subject="米莉",
        content="米莉知道仓库区的传闻。",
        entities=entities or ["米莉"],
        confidence=1.0,
        certainty=1.0,
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
            _record(entities=["玩家"])
        ])

        assert links == []


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
            [existing],
        )

        assert len(result) == 1
        assert result[0].entity_id == existing.entity_id
        assert result[0].name == "米莉"
        assert "酒馆侍者" in result[0].aliases
        assert result[0].confidence == 0.55
        assert result[0].source_scene_ids == ["scene_001"]

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
            [],
        )

        assert len(result) == 1
        assert result[0].name == "北郡修道院"
        assert result[0].entity_type == "location"


class TestUpdateEntityProfilesWorkflow:
    def test_workflow_uses_full_store_and_saves(self):
        store = EntityStore(":memory:")
        existing_id = store.upsert(EntityProfile(entity_type="npc", name="米莉"))
        mock = MockLLM()
        mock.set_responses([
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
            )
        ])

        saved = update_entity_profiles_workflow(
            "米莉递来一张旧地图。",
            _make_state(),
            mock.build_gateway(),
            store,
        )

        assert saved == [existing_id]
        assert store.get_by_id(existing_id).summary == "米莉是北郡酒馆的侍者。"
