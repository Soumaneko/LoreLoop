import tempfile

import pytest
from pydantic import ValidationError

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.short_term_memory import ShortTermMemory, ShortTermScene, ShortTermMemoryStore
from app.domain.turn_resolution import PlayerTurnResolution


def _action_preview() -> ActionPreviewResult:
    return ActionPreviewResult(previews=[
        {"level": "追击施压", "label": "追上去", "good_result": "追上目标。", "risk": "可能暴露。"},
        {"level": "静待时机", "label": "留在原地", "good_result": "保持安全。", "risk": "可能错失机会。"},
        {"level": "痕迹追查", "label": "检查痕迹", "good_result": "发现线索。", "risk": "会消耗时间。"},
        {"level": "声东击西", "label": "制造声响", "good_result": "引开注意。", "risk": "可能惊动更多人。"},
    ])


def _player_resolution() -> PlayerTurnResolution:
    return PlayerTurnResolution(actions=[{
        "action_text": "追上逃跑的目标",
        "resolution_source": "roll",
        "outcome_band": "success",
        "dice": "2d20kh",
        "raw_values": [8, 14],
        "modifiers": [{
            "label": "抢占先机",
            "reason": "目标尚未拉开距离。",
            "value": 2,
        }],
        "modifier_total": 2,
        "final_value": 16,
        "dice_sides": 20,
    }])


class TestShortTermMemoryStore:
    def test_add_and_get_recent(self):
        store = ShortTermMemoryStore(max_items=5)
        store.add_turn(
            "s1", 1, "观察四周", "第一段剧情", "接下来做什么？",
            action_preview=_action_preview(),
            player_resolution=_player_resolution(),
        )
        store.add_turn("s2", 2, "与米莉交谈", "第二段剧情")

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert memory.recent_scenes[0].scene_id == "s1"
        assert memory.recent_scenes[0].user_input == "观察四周"
        assert memory.recent_scenes[0].scene_text == "第一段剧情"
        assert memory.recent_scenes[0].closing_prompt == "接下来做什么？"
        assert memory.recent_scenes[0].action_preview == _action_preview()
        assert memory.recent_scenes[0].player_resolution == _player_resolution()
        assert memory.recent_scenes[1].scene_id == "s2"
        assert memory.recent_scenes[1].user_input == "与米莉交谈"
        assert memory.recent_scenes[1].scene_text == "第二段剧情"
        assert memory.recent_scenes[1].closing_prompt == ""
        assert memory.max_items == 5

    def test_evicts_oldest_when_exceeded(self):
        store = ShortTermMemoryStore(max_items=3)
        for i in range(1, 6):
            store.add_turn(f"s{i}", i, "action", f"第{i}段剧情")

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 3
        ids = [s.scene_id for s in memory.recent_scenes]
        assert ids == ["s3", "s4", "s5"]

        all_scenes = store.list_all()
        assert len(all_scenes) == 5
        assert [s.scene_id for s in all_scenes] == ["s1", "s2", "s3", "s4", "s5"]

    def test_custom_max_items(self):
        store = ShortTermMemoryStore(max_items=2)
        store.add_turn("s1", 1, "action", "剧情1")
        store.add_turn("s2", 2, "action", "剧情2")
        store.add_turn("s3", 3, "action", "剧情3")

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert [s.scene_id for s in memory.recent_scenes] == ["s2", "s3"]

        all_scenes = store.list_all()
        assert len(all_scenes) == 3

    def test_list_page_uses_exclusive_turn_cursor(self):
        store = ShortTermMemoryStore(max_items=2)
        for i in range(1, 13):
            store.add_turn(f"s{i}", i, "action", f"剧情{i}")

        latest, has_more = store.list_page(limit=5)
        assert [scene.turn_index for scene in latest] == [8, 9, 10, 11, 12]
        assert has_more is True

        previous, has_more = store.list_page(before_turn_index=8, limit=5)
        assert [scene.turn_index for scene in previous] == [3, 4, 5, 6, 7]
        assert has_more is True

        oldest, has_more = store.list_page(before_turn_index=3, limit=5)
        assert [scene.turn_index for scene in oldest] == [1, 2]
        assert has_more is False

    def test_empty_store_returns_empty(self):
        store = ShortTermMemoryStore()
        memory = store.get_recent()
        assert memory.recent_scenes == []
        assert memory.max_items == 5

    def test_clear_removes_existing_scenes(self):
        store = ShortTermMemoryStore(max_items=5)
        store.add_turn("s1", 1, "观察四周", "第一段剧情")
        store.add_turn("s2", 2, "继续前进", "第二段剧情")

        store.clear()

        assert store.get_recent().recent_scenes == []
        assert store.list_all() == []

    def test_scenes_returns_in_order(self):
        store = ShortTermMemoryStore(max_items=3)
        store.add_turn("s1", 1, "action", "剧情1")
        store.add_turn("s2", 2, "action", "剧情2")

        memory = store.get_recent()
        assert memory.recent_scenes[0].turn_index == 1
        assert memory.recent_scenes[1].turn_index == 2
        assert memory.recent_scenes[0].created_at is not None

    def test_invalid_max_items_rejected(self):
        with pytest.raises(ValueError):
            ShortTermMemoryStore(max_items=0)


class TestShortTermMemoryModel:
    def test_default_list_is_isolated(self):
        m1 = ShortTermMemory()
        m2 = ShortTermMemory()
        m1.recent_scenes.append(ShortTermScene(
            scene_id="s1", turn_index=1, scene_text="剧情",
        ))
        assert m2.recent_scenes == []

    def test_invalid_max_items_rejected(self):
        with pytest.raises(ValidationError):
            ShortTermMemory(max_items=0)

    def test_user_input_default_empty(self):
        scene = ShortTermScene(
            scene_id="s1", turn_index=1, scene_text="剧情",
        )
        assert scene.user_input == ""
        assert scene.closing_prompt == ""
        assert scene.action_preview is None
        assert scene.player_resolution is None


class TestShortTermMemoryPersistence:
    def test_data_persists_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store1 = ShortTermMemoryStore(db_path=db_path)
        store1.add_turn("s1", 1, "探索酒馆", "主角环顾四周。")
        store1.add_turn(
            "s2",
            2,
            "与米莉交谈",
            "米莉告诉主角一条密道。",
            "要进入密道吗？",
            action_preview=_action_preview(),
            player_resolution=_player_resolution(),
        )
        store1.close()

        store2 = ShortTermMemoryStore(db_path=db_path)
        memory = store2.get_recent()
        store2.close()

        assert len(memory.recent_scenes) == 2
        assert memory.recent_scenes[0].scene_id == "s1"
        assert memory.recent_scenes[0].user_input == "探索酒馆"
        assert memory.recent_scenes[0].scene_text == "主角环顾四周。"
        assert memory.recent_scenes[1].scene_id == "s2"
        assert memory.recent_scenes[1].user_input == "与米莉交谈"
        assert memory.recent_scenes[1].scene_text == "米莉告诉主角一条密道。"
        assert memory.recent_scenes[1].closing_prompt == "要进入密道吗？"
        assert memory.recent_scenes[1].action_preview == _action_preview()
        assert memory.recent_scenes[1].player_resolution == _player_resolution()

        import os
        os.unlink(db_path)

    def test_existing_minimal_db_is_supported(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        import sqlite3
        con = sqlite3.connect(db_path)
        con.execute(
            """
            CREATE TABLE short_term_scenes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scene_id TEXT NOT NULL,
                turn_index INTEGER NOT NULL,
                user_input TEXT NOT NULL DEFAULT '',
                scene_text TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        con.execute(
            """
            INSERT INTO short_term_scenes (
                scene_id, turn_index, user_input, scene_text, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            ("s1", 1, "观察", "旧剧情", "2026-01-01T00:00:00+00:00"),
        )
        con.commit()
        con.close()

        store = ShortTermMemoryStore(db_path=db_path)
        memory = store.get_recent()
        store.add_turn("s2", 2, "继续", "新剧情")
        updated = store.get_recent()
        store.close()

        assert memory.recent_scenes[0].scene_text == "旧剧情"
        assert memory.recent_scenes[0].closing_prompt == ""
        assert memory.recent_scenes[0].action_preview is None
        assert updated.recent_scenes[1].scene_text == "新剧情"

        import os
        os.unlink(db_path)

    def test_persisted_eviction(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = ShortTermMemoryStore(db_path=db_path, max_items=3)
        for i in range(1, 6):
            store.add_turn(f"s{i}", i, "action", f"剧情{i}")
        store.close()

        store2 = ShortTermMemoryStore(db_path=db_path, max_items=3)
        memory = store2.get_recent()
        all_scenes = store2.list_all()
        store2.close()

        assert len(memory.recent_scenes) == 3
        assert [s.scene_id for s in memory.recent_scenes] == ["s3", "s4", "s5"]

        assert len(all_scenes) == 5
        assert [s.scene_id for s in all_scenes] == ["s1", "s2", "s3", "s4", "s5"]

        import os
        os.unlink(db_path)

    def test_empty_db_returns_empty(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store = ShortTermMemoryStore(db_path=db_path)
        memory = store.get_recent()
        store.close()

        assert memory.recent_scenes == []

        import os
        os.unlink(db_path)


class TestListAll:
    def test_list_all_returns_all_in_order(self):
        store = ShortTermMemoryStore(max_items=2)
        for i in range(1, 8):
            store.add_turn(f"s{i}", i, f"action{i}", f"剧情{i}")

        all_scenes = store.list_all()
        assert len(all_scenes) == 7
        assert [s.turn_index for s in all_scenes] == [1, 2, 3, 4, 5, 6, 7]

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert [s.scene_id for s in memory.recent_scenes] == ["s6", "s7"]

    def test_list_all_empty_store(self):
        store = ShortTermMemoryStore()
        assert store.list_all() == []

    def test_list_all_returns_short_term_scene_objects(self):
        store = ShortTermMemoryStore(max_items=5)
        store.add_turn("s1", 1, "观察", "剧情")

        all_scenes = store.list_all()
        assert len(all_scenes) == 1
        scene = all_scenes[0]
        assert isinstance(scene, ShortTermScene)
        assert scene.scene_id == "s1"
        assert scene.user_input == "观察"
        assert scene.scene_text == "剧情"
        assert scene.created_at is not None
