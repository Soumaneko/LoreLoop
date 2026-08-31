from app.tests.factories.stores import open_current_state_store
import json
import sqlite3

from app.domain.current_state import (
    CurrentState,
    CurrentStateStore,
    InventoryItem,
    ParticipantState,
    PlayerState,
    ResourcesState,
    SceneState,
    WorldClock,
)


class TestSceneState:
    def test_required_scene_fields(self):
        state = SceneState(
            scene_id="scene_001",
            location="修道院门口",
            participants=["主角", "米莉"],
        )

        assert state.participant_names == ["主角", "米莉"]
        assert not hasattr(state, "atmosphere")
        assert not hasattr(state, "time_label")


class TestWorldClock:
    def test_default_phase_is_early_morning(self):
        assert WorldClock().label == "第1日清晨"

    def test_label_and_phase_advance(self):
        clock = WorldClock(day=1, phase="深夜")
        advanced = clock.advance_phase()

        assert clock.label == "第1日深夜"
        assert advanced.day == 2
        assert advanced.phase == "凌晨"
        assert advanced.label == "第2日凌晨"

    def test_has_reached_compares_world_day_and_phase(self):
        deadline = WorldClock(day=3, phase="黄昏")

        assert not WorldClock(day=3, phase="下午").has_reached(deadline)
        assert WorldClock(day=3, phase="黄昏").has_reached(deadline)
        assert WorldClock(day=4, phase="凌晨").has_reached(deadline)


class TestPlayerState:
    def test_defaults_are_empty_or_none(self):
        state = PlayerState(name="卢西安")

        assert state.visible_status is None
        assert not hasattr(state, "condition")
        assert not hasattr(state, "position")


class TestParticipantState:
    def test_current_participant_state(self):
        state = ParticipantState(
            name="米莉",
            attitude_to_player="感激主角",
        )

        assert state.attitude_to_player == "感激主角"
        assert not hasattr(state, "npc_conditions")


class TestResourcesState:
    def test_resource_defaults_are_isolated(self):
        r1 = ResourcesState()
        r2 = ResourcesState()

        r1.inventory.append(InventoryItem(name="匕首", quantity=1))
        r1.fixed_assets.append("葡萄园")
        r1.temporary_assets.append("临时通行证")

        assert r2.inventory == []
        assert r2.fixed_assets == []
        assert r2.temporary_assets == []

    def test_money_can_be_none_or_int(self):
        assert ResourcesState().money is None
        assert ResourcesState(money=12).money == 12
        assert ResourcesState(money=12).money_unit is None

    def test_inventory_item_quantity(self):
        item = InventoryItem(name="箭矢", quantity=12)

        assert item.name == "箭矢"
        assert item.quantity == 12


class TestCurrentState:
    def test_combines_state_parts(self):
        state = CurrentState(
            scene=SceneState(
                scene_id="scene_001",
                location="修道院门口",
                participants=[
                    "卢西安",
                    "米莉",
                    {
                        "name": "莎拉",
                        "attitude_to_player": "仍警惕但开始认可主角",
                    },
                ],
            ),
            world_clock=WorldClock(day=1, phase="上午"),
            player=PlayerState(
                name="卢西安",
                visible_status="衣袍沾着泥水",
            ),
            resources=ResourcesState(fixed_assets=["葡萄园"]),
        )

        assert state.scene.scene_id == "scene_001"
        assert state.world_clock.phase == "上午"
        assert state.player.name == "卢西安"
        assert state.scene.participants[2].name == "莎拉"
        assert state.resources.fixed_assets == ["葡萄园"]

    def test_upgrades_legacy_in_scene_npc_data_without_retaining_removed_fields(self):
        state = CurrentState.model_validate({
            "scene": {
                "scene_id": "scene_003",
                "location": "酒馆",
                "participants": ["主角", "米莉"],
                "atmosphere": "紧张",
                "scene_condition": [
                    {"condition": "房间正在燃烧", "expires_after_phases": 2}
                ],
            },
            "player": {
                "name": "主角",
                "position": "吧台前",
                "condition": [],
            },
            "npcs": [
                {
                    "name": "米莉",
                    "condition": ["疲惫"],
                    "attitude_to_player": "友好",
                    "current_intent": "擦杯子",
                },
                {
                    "name": "不在场的人",
                    "condition": ["受伤"],
                },
            ],
        })

        assert state.scene.participant_names == ["主角", "米莉"]
        assert state.scene.participants[1].attitude_to_player == "友好"
        assert not hasattr(state.scene, "atmosphere")
        assert not hasattr(state.scene, "scene_condition")
        assert not hasattr(state.scene.participants[1], "npc_conditions")
        assert not hasattr(state.player, "condition")
        assert not hasattr(state.player, "position")
        assert not hasattr(state, "npcs")

    def test_store_migration_persistently_removes_legacy_conditions(self, tmp_path):
        db_path = tmp_path / "current_state.db"
        with open_current_state_store(db_path):
            pass
        legacy_state = {
            "scene": {
                "scene_id": "scene_003",
                "location": "酒馆",
                "participants": [
                    {
                        "name": "米莉",
                        "npc_conditions": [
                            {
                                "condition": "疲惫",
                                "expires_after_phases": 2,
                            }
                        ],
                    }
                ],
                "scene_condition": [
                    {
                        "condition": "烟雾弥漫",
                        "expires_after_phases": 1,
                    }
                ],
            },
            "player": {
                "name": "主角",
                "condition": ["轻伤"],
            },
        }
        connection = sqlite3.connect(db_path)
        connection.execute(
            """
            INSERT INTO current_state(id, state_json, updated_at)
            VALUES (1, ?, 'legacy')
            """,
            (json.dumps(legacy_state, ensure_ascii=False),),
        )
        connection.execute(
            """
            UPDATE schema_versions
            SET version = 3
            WHERE component = 'current_state'
            """
        )
        connection.commit()
        connection.close()

        with open_current_state_store(db_path) as store:
            state = store.load()

        persisted = sqlite3.connect(db_path).execute(
            "SELECT state_json FROM current_state WHERE id = 1"
        ).fetchone()[0]
        persisted_data = json.loads(persisted)
        assert state is not None
        assert not hasattr(state.player, "condition")
        assert "condition" not in persisted_data["player"]
        assert "scene_condition" not in persisted_data["scene"]
        assert "npc_conditions" not in persisted_data["scene"]["participants"][0]