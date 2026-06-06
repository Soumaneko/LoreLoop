import pytest

from app.domain.memory.schemas import Memory, MemoryRecord
from app.domain.memory.store import MemoryStore


def _make_memory(**overrides) -> Memory:
    defaults = {
        "type": "fact",
        "subject": "测试记忆",
        "content": "这是一条测试记忆内容。",
        "entities": ["主角"],
        "location": "测试地点",
        "time_label": "第1日",
        "visibility": ["主角"],
        "tags": ["测试"],
        "certainty": 1.0,
        "salience": 0.5,
        "source_scene_id": "scene_test",
        "source_text_span": "测试文本",
    }
    defaults.update(overrides)
    return Memory(**defaults)


class TestMemoryStoreSaveAndRead:
    def test_save_and_list_all(self):
        with MemoryStore(":memory:") as store:
            memory = _make_memory()
            ids = store.save_many([memory])

            assert len(ids) == 1

            records = store.list_all()
            assert len(records) == 1
            r = records[0]
            assert r.type == "fact"
            assert r.subject == "测试记忆"
            assert r.content == "这是一条测试记忆内容。"
            assert r.entities == ["主角"]
            assert r.location == "测试地点"
            assert r.certainty == 1.0
            assert isinstance(r.id, str) and len(r.id) == 32
            assert r.created_at != ""

    def test_save_multiple_memories(self):
        with MemoryStore(":memory:") as store:
            m1 = _make_memory(subject="记忆A", content="内容A")
            m2 = _make_memory(subject="记忆B", content="内容B")
            m3 = _make_memory(subject="记忆C", content="内容C")

            ids = store.save_many([m1, m2, m3])
            assert len(ids) == 3
            assert store.count() == 3

    def test_get_by_id_returns_correct_record(self):
        with MemoryStore(":memory:") as store:
            memory = _make_memory(subject="查找测试")
            ids = store.save_many([memory])
            rid = ids[0]

            record = store.get_by_id(rid)
            assert record is not None
            assert record.id == rid
            assert record.subject == "查找测试"

    def test_get_by_id_returns_none_for_missing(self):
        with MemoryStore(":memory:") as store:
            assert store.get_by_id("nonexistent") is None

    def test_empty_save_returns_empty_list(self):
        with MemoryStore(":memory:") as store:
            ids = store.save_many([])
            assert ids == []
            assert store.count() == 0


class TestMemoryStoreDedup:
    def test_exact_duplicate_not_saved(self):
        with MemoryStore(":memory:") as store:
            m = _make_memory(subject="去重测试", content="重复内容A")

            ids1 = store.save_many([m])
            assert len(ids1) == 1

            m2 = _make_memory(subject="去重测试", content="重复内容A")
            ids2 = store.save_many([m2])

            assert len(ids2) == 0
            assert store.count() == 1

    def test_different_type_allows_save(self):
        with MemoryStore(":memory:") as store:
            m1 = _make_memory(type="fact", subject="同主题", content="同内容")
            m2 = _make_memory(type="event", subject="同主题", content="同内容")

            ids = store.save_many([m1, m2])
            assert len(ids) == 2
            assert store.count() == 2

    def test_different_subject_allows_save(self):
        with MemoryStore(":memory:") as store:
            m1 = _make_memory(subject="主题A", content="同内容")
            m2 = _make_memory(subject="主题B", content="同内容")

            ids = store.save_many([m1, m2])
            assert len(ids) == 2

    def test_different_entities_allows_save(self):
        with MemoryStore(":memory:") as store:
            m1 = _make_memory(subject="同主题", content="同内容", entities=["主角"])
            m2 = _make_memory(subject="同主题", content="同内容", entities=["主角", "伊莱娜"])

            ids = store.save_many([m1, m2])
            assert len(ids) == 2

    def test_different_content_allows_save(self):
        with MemoryStore(":memory:") as store:
            m1 = _make_memory(subject="同主题", content="内容A")
            m2 = _make_memory(subject="同主题", content="内容B")

            ids = store.save_many([m1, m2])
            assert len(ids) == 2


class TestMemoryStoreDb:
    def test_file_based_db_persists(self, tmp_path):
        db_path = str(tmp_path / "test_memories.db")

        with MemoryStore(db_path) as store:
            ids = store.save_many([_make_memory(subject="持久化测试")])
            assert len(ids) == 1

        with MemoryStore(db_path) as store:
            records = store.list_all()
            assert len(records) == 1
            assert records[0].subject == "持久化测试"
