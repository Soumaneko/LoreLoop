import pytest
from pydantic import ValidationError

from app.domain.short_term_memory import ShortTermMemory, ShortTermScene, ShortTermMemoryStore


def _make_scene(scene_id: str, turn_index: int, text: str) -> ShortTermScene:
    return ShortTermScene(
        scene_id=scene_id,
        turn_index=turn_index,
        text=text,
    )


class TestShortTermMemoryStore:
    def test_add_and_get_recent(self):
        store = ShortTermMemoryStore(max_items=5)
        store.add_scene(_make_scene("s1", 1, "第一段剧情"))
        store.add_scene(_make_scene("s2", 2, "第二段剧情"))

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert memory.recent_scenes[0].scene_id == "s1"
        assert memory.recent_scenes[1].scene_id == "s2"
        assert memory.max_items == 5

    def test_evicts_oldest_when_exceeded(self):
        store = ShortTermMemoryStore(max_items=3)
        for i in range(1, 6):
            store.add_scene(_make_scene(f"s{i}", i, f"第{i}段剧情"))

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 3
        ids = [s.scene_id for s in memory.recent_scenes]
        assert ids == ["s3", "s4", "s5"]

    def test_custom_max_items(self):
        store = ShortTermMemoryStore(max_items=2)
        store.add_scene(_make_scene("s1", 1, "剧情1"))
        store.add_scene(_make_scene("s2", 2, "剧情2"))
        store.add_scene(_make_scene("s3", 3, "剧情3"))

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert [s.scene_id for s in memory.recent_scenes] == ["s2", "s3"]

    def test_empty_store_returns_empty(self):
        store = ShortTermMemoryStore()
        memory = store.get_recent()
        assert memory.recent_scenes == []
        assert memory.max_items == 5

    def test_scenes_returns_in_order(self):
        store = ShortTermMemoryStore(max_items=3)
        store.add_scene(_make_scene("s1", 1, "剧情1"))
        store.add_scene(_make_scene("s2", 2, "剧情2"))

        memory = store.get_recent()
        assert memory.recent_scenes[0].created_at is None
        assert memory.recent_scenes[0].turn_index == 1
        assert memory.recent_scenes[1].turn_index == 2

    def test_get_recent_returns_copy(self):
        store = ShortTermMemoryStore()
        store.add_scene(_make_scene("s1", 1, "剧情"))
        memory = store.get_recent()
        memory.recent_scenes.pop()
        assert len(store.recent_scenes) == 1

    def test_get_recent_returns_scene_copies(self):
        store = ShortTermMemoryStore()
        store.add_scene(_make_scene("s1", 1, "原始剧情"))

        memory = store.get_recent()
        memory.recent_scenes[0].text = "被外部修改"

        assert store.get_recent().recent_scenes[0].text == "原始剧情"

    def test_invalid_max_items_rejected(self):
        with pytest.raises(ValueError):
            ShortTermMemoryStore(max_items=0)
        with pytest.raises(ValueError):
            ShortTermMemoryStore(max_items=-1)

    def test_short_term_memory_default_list_is_isolated(self):
        m1 = ShortTermMemory()
        m2 = ShortTermMemory()

        m1.recent_scenes.append(_make_scene("s1", 1, "剧情"))

        assert m2.recent_scenes == []

    def test_short_term_memory_invalid_max_items_rejected(self):
        with pytest.raises(ValidationError):
            ShortTermMemory(max_items=0)
