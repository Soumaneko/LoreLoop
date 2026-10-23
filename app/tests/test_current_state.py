
from app.domain.current_state import SceneState, WorldClock


class TestSceneState:
    def test_required_scene_fields(self):
        state = SceneState(
            scene_id="scene_001",
            location="修道院门口",
            participants=["主角", "米莉"],
        )

        assert state.participant_names == ["主角", "米莉"]


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
