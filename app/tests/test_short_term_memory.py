from app.tests.factories.stores import open_short_term_store
import tempfile

import pytest
from pydantic import ValidationError

from app.domain.action_preview.schemas import ActionPreviewResult
from app.domain.short_term_memory import ShortTermMemory, ShortTermScene, ShortTermMemoryStore
from app.domain.action_judgment import PlayerTurnResolution


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
        store = open_short_term_store(max_items=5)
        store.add_turn(
            "s1", 1, "观察四周", "第一段剧情", "接下来做什么？",
            action_preview=_action_preview(),
            player_resolution=_player_resolution(),
            token_usage={
                "generation": {
                    "input_tokens": 120,
                    "output_tokens": 30,
                    "total_tokens": 150,
                },
                "confirmation": {
                    "input_tokens": 40,
                    "output_tokens": 10,
                    "total_tokens": 50,
                },
            },
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
        assert memory.recent_scenes[0].token_usage == {
            "generation": {
                "input_tokens": 120,
                "output_tokens": 30,
                "total_tokens": 150,
            },
            "confirmation": {
                "input_tokens": 40,
                "output_tokens": 10,
                "total_tokens": 50,
            },
        }
        assert memory.recent_scenes[1].scene_id == "s2"
        assert memory.recent_scenes[1].user_input == "与米莉交谈"
        assert memory.recent_scenes[1].scene_text == "第二段剧情"
        assert memory.recent_scenes[1].closing_prompt == ""
        assert memory.max_items == 5

    def test_evicts_oldest_when_exceeded(self):
        store = open_short_term_store(max_items=3)
        for i in range(1, 6):
            store.add_turn(f"s{i}", i, "action", f"第{i}段剧情")

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 3
        ids = [s.scene_id for s in memory.recent_scenes]
        assert ids == ["s3", "s4", "s5"]

        all_scenes = store.list_all()
        assert len(all_scenes) == 5
        assert [s.scene_id for s in all_scenes] == ["s1", "s2", "s3", "s4", "s5"]


    def test_list_page_uses_exclusive_turn_cursor(self):
        store = open_short_term_store(max_items=2)
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









class TestShortTermMemoryPersistence:
    def test_data_persists_across_instances(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        store1 = open_short_term_store(db_path=db_path)
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

        store2 = open_short_term_store(db_path=db_path)
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





class TestListAll:
    def test_list_all_returns_all_in_order(self):
        store = open_short_term_store(max_items=2)
        for i in range(1, 8):
            store.add_turn(f"s{i}", i, f"action{i}", f"剧情{i}")

        all_scenes = store.list_all()
        assert len(all_scenes) == 7
        assert [s.turn_index for s in all_scenes] == [1, 2, 3, 4, 5, 6, 7]

        memory = store.get_recent()
        assert len(memory.recent_scenes) == 2
        assert [s.scene_id for s in memory.recent_scenes] == ["s6", "s7"]
