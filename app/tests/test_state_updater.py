import json
from concurrent.futures import CancelledError

import pytest

from app.core.llm import MockLLM
from app.core.llm.errors import LLMProviderError
from app.core.llm.gateway import LLMGateway
from app.core.llm.providers import MockProvider
from app.core.llm.tracing import NullTraceLogger
from app.domain.core_memory import CoreMemory
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    PlayerState,
    ResourcesState,
    SceneState,
    StateUpdater,
    WorldClock,
)
from app.domain.current_state.changes import (
    calculate_phase_advance,
    compile_frame_operations,
    StateChangeSet,
    StateResolutionOutput,
)
from app.domain.current_state.schemas import FinalSceneFrame


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_009",
            location="酒馆",
            participants=[
                "主角",
                {"name": "米莉", "attitude_to_player": "谨慎"},
            ],
        ),
        world_clock=WorldClock(day=1, phase="深夜"),
        player=PlayerState(
            name="主角",
            visible_status="衣袍沾灰",
        ),
        resources=ResourcesState(
            inventory=[InventoryItem(name="火把", quantity=2)],
            money=20,
            money_unit="金币",
            fixed_assets=["葡萄园"],
            temporary_assets=["临时通行证"],
        ),
    )


def _facts() -> str:
    return json.dumps(
        {
            "scene_focus": "测试变更",
            "facts": [
                {"fact_id": f"f{i}", "fact": f"测试事实{i}"}
                for i in range(1, 13)
            ],
        },
        ensure_ascii=False,
    )


def _operation(operation_id: str, operation_type: str, **fields) -> dict:
    return {
        "operation_id": operation_id,
        "type": operation_type,
        "reason": "Scene Facts 明确支持该变化。",
        "evidence_fact_ids": ["f1"],
        **fields,
    }


def _frame(**overrides) -> dict:
    frame = {
        "final_location": None,
        "day_offset": 0,
        "final_phase": None,
        "active_present_npcs": [],
        "departed_npcs": [],
        "evidence_fact_ids": [],
        "evidence_paragraph_ids": [],
    }
    frame.update(overrides)
    return frame


def _update(response: dict, *, state: CurrentState | None = None):
    response = {
        "decision": "Scene Facts 支持以下状态变化。",
        **response,
    }
    response.setdefault("frame", _frame())
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())
    result = updater.update(
        state or _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )
    return result, updater.last_change_set


def test_no_operations_preserve_the_complete_state():
    state = _state()

    result, changes = _update({"operations": []}, state=state)

    assert result == state
    assert changes.decision == "Scene Facts 支持以下状态变化。"
    assert changes.operations == []


def test_frame_relocation_evicts_unlisted_participants():
    result, changes = _update({
        "frame": _frame(
            final_location="城门",
            evidence_fact_ids=["f1"],
        ),
    })

    assert result.scene.location == "城门"
    assert result.scene.scene_id == "scene_010"
    assert result.scene.participant_names == ["主角"]
    assert result.world_clock == WorldClock(day=1, phase="深夜")
    assert "frame_location" in [
        op.operation_id for op in changes.operations
    ]


























def test_unknown_operation_evidence_is_stripped_without_retry():
    provider = _CountingProvider([
        json.dumps(
            {
                "decision": "资源变动。",
                "frame": _frame(),
                "operations": [
                    {
                        **_operation("op1", "money_delta", delta=5),
                        "evidence_fact_ids": ["f1", "missing"],
                    }
                ],
            },
            ensure_ascii=False,
        )
    ])
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.resources.money == 25
    assert provider.calls == 1
    assert any(
        warning.code == "state_update_evidence_unknown_stripped"
        for warning in updater.last_warnings
    )
















def test_player_visible_status_change():
    result, _ = _update(
        {
            "operations": [
                _operation(
                    "op1",
                    "player_visible_status_change",
                    visible_status=None,
                ),
            ]
        }
    )

    assert result.player.visible_status is None


def test_resource_deltas_and_asset_operations_are_deterministic():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "inventory_delta", item="火把", delta=-1),
                _operation("op2", "inventory_delta", item="银钥匙", delta=1),
                _operation("op3", "money_delta", delta=-5),
                _operation("op4", "fixed_asset_remove", asset="葡萄园"),
                _operation("op5", "fixed_asset_add", asset="旧宅"),
                _operation(
                    "op6",
                    "temporary_asset_remove",
                    asset="临时通行证",
                ),
                _operation(
                    "op7",
                    "temporary_asset_add",
                    asset="仓库钥匙使用权",
                ),
            ]
        }
    )

    assert [(item.name, item.quantity) for item in result.resources.inventory] == [
        ("火把", 1),
        ("银钥匙", 1),
    ]
    assert result.resources.money == 15
    assert result.resources.fixed_assets == ["旧宅"]
    assert result.resources.temporary_assets == ["仓库钥匙使用权"]




class _CancellingGateway:
    def run(self, **_kwargs):
        raise CancelledError("user cancelled state update")


class _FailingGateway:
    def run(self, **_kwargs):
        raise LLMProviderError("provider down")


def test_cancellation_is_never_swallowed():
    updater = StateUpdater(_CancellingGateway())

    with pytest.raises(CancelledError):
        updater.update(
            _state(),
            _facts(),
            core_memory=CoreMemory(items=[]),
        )

    assert updater.last_warnings == []


def test_provider_error_propagates():
    updater = StateUpdater(_FailingGateway())

    with pytest.raises(LLMProviderError):
        updater.update(
            _state(),
            _facts(),
            core_memory=CoreMemory(items=[]),
        )












def test_frame_paragraph_evidence_applies_location():
    state = _state()
    response = {
        "decision": "正文段落证据支持地点变更。",
        "frame": _frame(
            final_location="城门",
            evidence_paragraph_ids=["turn_1_p001"],
        ),
        "operations": [],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())
    result = updater.update(
        state,
        json.dumps({"scene_focus": "降级", "facts": []}, ensure_ascii=False),
        core_memory=CoreMemory(items=[]),
        paragraph_texts={"turn_1_p001": "主角离开酒馆前往城门。"},
    )

    assert result.scene.location == "城门"
    assert result.scene.scene_id == "scene_010"




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


def _structural_error_operation(operation_id: str) -> dict:
    return {
        "operation_id": operation_id,
        "reason": "缺少 type，属于结构错误。",
        "evidence_fact_ids": ["f1"],
    }


def test_salvage_uses_second_attempt_valid_subset_only():
    first = {
        "decision": "第一次有合法与非法操作。",
        "frame": _frame(),
        "operations": [
            _operation("op1", "money_delta", delta=1),
            _structural_error_operation("op2"),
        ],
    }
    second = {
        "decision": "第二次只保留合法操作。",
        "frame": _frame(),
        "operations": [
            _operation("op3", "money_delta", delta=5),
            _structural_error_operation("op4"),
        ],
    }
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.scene.location == "酒馆"
    assert result.resources.money == 25
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op3"]
    assert updater.last_warnings[-1].code == "state_update_operation_dropped"
    assert updater.last_warnings[-1].operation_id == "op4"
























def test_over_limit_operations_preserve_first_thirty_valid_ops():
    response = {
        "decision": "超过 30 项。",
        "operations": [
            _operation(f"op{i}", "money_delta", delta=1)
            for i in range(1, 32)
        ],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.resources.money == 50
    assert len(updater.last_change_set.operations) == 30
    assert any(
        warning.code == "state_update_operations_truncated"
        and warning.operation_id == "operations"
        for warning in updater.last_warnings
    )


def test_frame_and_llm_operations_have_no_combined_cap():
    result, changes = _update(
        {
            "frame": _frame(
                active_present_npcs=[f"npc{i:02d}" for i in range(1, 32)],
                evidence_fact_ids=["f1"],
            ),
            "operations": [
                _operation(f"op{i}", "money_delta", delta=1)
                for i in range(1, 31)
            ],
        }
    )

    assert len(changes.operations) == 61
    assert len(result.scene.participants) == 33
    assert result.resources.money == 50
