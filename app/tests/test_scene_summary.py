import json
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.scene_summary import SceneSummarizer
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.store import SceneSummaryStore
from app.workflows.scene_summaries import run_scene_summary_maintenance


PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _summary(**overrides) -> SceneSummary:
    defaults = {
        "level": 1,
        "content": "卢西安在旧旅店获得了钥匙线索。",
        "scene_ids": ["scene_001"],
        "source_ids": [],
        "turn_start": 1,
        "turn_end": 1,
    }
    defaults.update(overrides)
    return SceneSummary(**defaults)


class TestSceneSummaryStore:
    def test_save_list_and_mark_condensed(self):
        with SceneSummaryStore(":memory:") as store:
            first = _summary(turn_start=1, turn_end=1, scene_ids=["scene_001"])
            second = _summary(turn_start=2, turn_end=2, scene_ids=["scene_002"])
            target = _summary(level=2, turn_start=1, turn_end=2, scene_ids=["scene_001", "scene_002"])

            store.save_one(first)
            store.save_one(second)
            assert store.should_condense(1, threshold=2)

            store.save_one(target)
            changed = store.mark_condensed([first.id, second.id], condensed_into=target.id)

            assert changed == 2
            assert store.count_uncondensed(1) == 0
            active = store.list_active()
            assert [summary.id for summary in active] == [target.id]
            assert active[0].level == 2

    def test_get_recent_prefers_recent_turn_then_lower_level(self):
        with SceneSummaryStore(":memory:") as store:
            store.save_one(_summary(id="old_l1", level=1, turn_start=1, turn_end=1))
            store.save_one(_summary(id="recent_l2", level=2, turn_start=8, turn_end=10))
            store.save_one(_summary(id="recent_l1", level=1, turn_start=10, turn_end=10))
            store.save_one(_summary(id="newest_l3", level=3, turn_start=11, turn_end=12))

            recent = store.get_recent(limit=3)

            assert [summary.id for summary in recent] == [
                "newest_l3",
                "recent_l1",
                "recent_l2",
            ]


class TestSceneSummarizer:
    def test_summarize_turn_program_fills_metadata(self):
        mock = MockLLM()
        mock.set_responses([json.dumps({"content": "卢西安确认酒窖钥匙只能打开酒窖门。"}, ensure_ascii=False)])

        summary = SceneSummarizer(mock.build_gateway()).summarize_turn(
            scene_text="老板娘把黄铜钥匙推给卢西安，说这只能开酒窖门。",
            scene_id="scene_010",
            turn_index=10,
        )

        assert summary.level == 1
        assert summary.content == "卢西安确认酒窖钥匙只能打开酒窖门。"
        assert summary.scene_ids == ["scene_010"]
        assert summary.source_ids == []
        assert summary.turn_start == 10
        assert summary.turn_end == 10

    def test_condense_program_fills_metadata(self):
        mock = MockLLM()
        mock.set_responses([json.dumps({"content": "旧旅店线索集中指向钟楼和酒窖。"}, ensure_ascii=False)])

        sources = [
            _summary(id="s1", scene_ids=["scene_001"], turn_start=1, turn_end=1),
            _summary(id="s2", scene_ids=["scene_002"], turn_start=2, turn_end=2),
        ]
        summary = SceneSummarizer(mock.build_gateway()).condense(
            source_summaries=sources,
            target_level=2,
        )

        assert summary.level == 2
        assert summary.content == "旧旅店线索集中指向钟楼和酒窖。"
        assert summary.scene_ids == ["scene_001", "scene_002"]
        assert summary.source_ids == ["s1", "s2"]
        assert summary.turn_start == 1
        assert summary.turn_end == 2


class TestSceneSummaryMaintenance:
    def test_maintenance_condenses_up_multiple_levels(self):
        mock = MockLLM()
        mock.set_responses([
            json.dumps({"content": "第2回合摘要。"}, ensure_ascii=False),
            json.dumps({"content": "L2 汇总。"}, ensure_ascii=False),
            json.dumps({"content": "L3 汇总。"}, ensure_ascii=False),
        ])

        with SceneSummaryStore(":memory:") as store:
            store.save_one(_summary(id="l1_existing", level=1, turn_start=1, turn_end=1))
            store.save_one(_summary(id="l2_existing", level=2, turn_start=1, turn_end=1, source_ids=["old_l1"]))

            result = run_scene_summary_maintenance(
                scene_text="第2回合正文。",
                scene_id="scene_002",
                turn_index=2,
                summarizer=SceneSummarizer(mock.build_gateway()),
                store=store,
                threshold=2,
            )

            assert len(result.created_summaries) == 3
            assert len(result.condensed_ids) == 2
            assert store.count_uncondensed(1) == 0
            assert store.count_uncondensed(2) == 0
            assert store.count_uncondensed(3) == 1
            active = store.list_active()
            assert len(active) == 1
            assert active[0].level == 3


class TestSceneSummaryPrompts:
    def test_prompts_define_length_limits(self):
        summarize_prompt = (PROMPT_DIR / "scene_summarize_v1.md").read_text(encoding="utf-8")
        condense_prompt = (PROMPT_DIR / "scene_summary_condense_v1.md").read_text(encoding="utf-8")

        assert "硬性上限 220" in summarize_prompt
        assert "硬性上限 320" in condense_prompt
        assert "不得随 source summaries 数量线性增长" in condense_prompt
