from app.runtime.stores import open_scene_summary_store
import json
import pytest

from app.core.llm import MockLLM
from app.core.llm.errors import LLMOutputValidationError, LLMProviderError
from app.core.llm.gateway import LLMGateway
from app.core.llm.providers import MockProvider
from app.core.llm.tracing import NullTraceLogger
from app.domain.core_memory import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.scene_summary import SceneSummarizer
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_summary.maintenance import run_scene_summary_maintenance


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(scene_id="scene_010", location="旧旅店", participants=["卢西安"]),
        player=PlayerState(name="卢西安"),
    )


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
        with open_scene_summary_store(":memory:") as store:
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



class TestSceneSummarizer:
    def test_summarize_turn_program_fills_metadata(self):
        mock = MockLLM()
        mock.set_responses([json.dumps({"content": "卢西安确认酒窖钥匙只能打开酒窖门。"}, ensure_ascii=False)])

        summary = SceneSummarizer(mock.build_gateway()).summarize_turn(
            core_memory=CoreMemory(items=[]),
            scene_text="老板娘把黄铜钥匙推给卢西安，说这只能开酒窖门。",
            current_state=_state(),
            scene_id="scene_010",
            turn_index=10,
        )

        assert summary.level == 1
        assert summary.content == "卢西安确认酒窖钥匙只能打开酒窖门。"
        assert summary.scene_ids == ["scene_010"]
        assert summary.source_ids == []
        assert summary.turn_start == 10
        assert summary.turn_end == 10





class _CountingProvider(MockProvider):
    def __init__(self, responses=None):
        super().__init__()
        if responses is not None:
            self.set_responses(responses)
        self.calls = 0

    def generate(self, *args, **kwargs):
        self.calls += 1
        return super().generate(*args, **kwargs)


def _gateway(provider):
    return LLMGateway(
        provider=provider,
        prompt_loader=MockLLM().prompt_loader,
        tracer=NullTraceLogger(),
    )


class _AlwaysFailProvider(_CountingProvider):
    def generate(self, *args, **kwargs):
        self.calls += 1
        raise LLMOutputValidationError("validation down")


def test_summarize_turn_second_provider_failure_propagates():
    class FirstThenProviderFailure(_CountingProvider):
        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return MockProvider.generate(self, *args, **kwargs)
            raise LLMProviderError("provider down")

    provider = FirstThenProviderFailure([
        json.dumps({"content": "长" * 376}, ensure_ascii=False),
    ])
    summarizer = SceneSummarizer(_gateway(provider))

    with pytest.raises(LLMProviderError):
        summarizer.summarize_turn(
            core_memory=CoreMemory(items=[]),
            scene_text="正文。",
            current_state=_state(),
            scene_id="scene_010",
            turn_index=10,
        )










def test_blank_scene_text_does_not_call_llm():
    class FailIfCalled(_CountingProvider):
        def generate(self, *args, **kwargs):
            raise AssertionError("should not call LLM")

    provider = FailIfCalled()
    summarizer = SceneSummarizer(_gateway(provider))

    summary = summarizer.summarize_turn(
        core_memory=CoreMemory(items=[]),
        scene_text="   ",
        current_state=_state(),
        scene_id="scene_010",
        turn_index=10,
    )

    assert summary.content == "本轮无正文。"
    assert provider.calls == 0


def test_condense_skip_keeps_sources_active():
    summarizer = SceneSummarizer(_gateway(_AlwaysFailProvider()))
    with open_scene_summary_store(":memory:") as store:
        store.save_one(_summary(id="s1", level=1, turn_start=1, turn_end=1))
        store.save_one(_summary(id="s2", level=1, turn_start=2, turn_end=2))

        result = run_scene_summary_maintenance(
            scene_text="第3轮正文。",
            current_state=_state(),
            core_memory=CoreMemory(items=[]),
            scene_id="scene_003",
            turn_index=3,
            summarizer=summarizer,
            store=store,
            threshold=2,
        )

        assert result.condensed_ids == []
        assert store.count_uncondensed(1) == 3
        assert store.count_uncondensed(2) == 0
