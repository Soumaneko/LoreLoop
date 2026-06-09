import pytest
from pydantic import ValidationError

from app.domain.core_memory import (
    CoreMemory,
    CoreMemoryItem,
    create_default_core_memory,
)


class TestCoreMemoryItem:
    def test_default_priority_is_100(self):
        item = CoreMemoryItem(id="test", category="world", content="测试内容")
        assert item.priority == 100

    def test_custom_priority(self):
        item = CoreMemoryItem(id="test", category="world", content="测试", priority=50)
        assert item.priority == 50

    def test_empty_id_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="", category="world", content="测试")

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="test", category="world", content="")

    def test_negative_priority_rejected(self):
        with pytest.raises(ValidationError):
            CoreMemoryItem(id="test", category="world", content="测试", priority=-1)


class TestDefaultCoreMemory:
    def test_has_three_items(self):
        cm = create_default_core_memory()
        assert len(cm.items) == 3

    def test_contains_expected_categories(self):
        cm = create_default_core_memory()
        categories = {item.category for item in cm.items}
        assert categories == {"player", "tone", "world"}

    def test_can_find_by_id(self):
        cm = create_default_core_memory()
        player_item = next(item for item in cm.items if item.id == "core_player_identity")
        assert player_item.category == "player"
        assert "卢西安" in player_item.content

    def test_can_filter_by_category(self):
        cm = create_default_core_memory()
        tone_items = [item for item in cm.items if item.category == "tone"]
        assert len(tone_items) == 1
        assert tone_items[0].id == "core_tone"

    def test_empty_core_memory(self):
        cm = CoreMemory(items=[])
        assert cm.items == []

    def test_multiple_items_same_category(self):
        cm = CoreMemory(items=[
            CoreMemoryItem(id="r1", category="world", content="规则1"),
            CoreMemoryItem(id="r2", category="world", content="规则2"),
        ])
        world_items = [item for item in cm.items if item.category == "world"]
        assert len(world_items) == 2
