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


def test_frame_same_location_keeps_participants():
    result, changes = _update({
        "frame": _frame(
            final_location="酒馆",
            evidence_fact_ids=["f1"],
        ),
    })

    assert result.scene.location == "酒馆"
    assert result.scene.participant_names == ["主角", "米莉"]
    assert "frame_location" not in [
        op.operation_id for op in changes.operations
    ]


def test_frame_day_offset_and_final_phase_advance_clock_by_table():
    result, _ = _update({
        "frame": _frame(
            day_offset=1,
            final_phase="清晨",
            evidence_fact_ids=["f1"],
        ),
    })

    # 深夜(6) + 7*1 + (清晨(1) - 深夜(6)) = 次日清晨。
    assert result.world_clock == WorldClock(day=2, phase="清晨")


def test_frame_time_regression_warning_is_reported():
    response = {
        "decision": "时间回退无法形成非负推进。",
        "frame": _frame(final_phase="下午", evidence_fact_ids=["f1"]),
        "operations": [],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.world_clock == WorldClock(day=1, phase="深夜")
    assert any(
        warning.code == "frame_time_ignored"
        for warning in updater.last_warnings
    )


def test_frame_departed_removes_only_existing_participants():
    result, changes = _update({
        "frame": _frame(
            departed_npcs=["米莉", "莎拉"],
            evidence_fact_ids=["f1"],
        ),
    })

    assert result.scene.participant_names == ["主角"]
    assert [op.operation_id for op in changes.operations] == [
        "frame_departed_1"
    ]


def test_frame_active_present_adds_newcomer_and_keeps_existing():
    result, changes = _update({
        "frame": _frame(
            active_present_npcs=["米莉", "莎拉"],
            evidence_fact_ids=["f1"],
        ),
    })

    assert result.scene.participant_names == ["主角", "米莉", "莎拉"]
    assert [op.operation_id for op in changes.operations] == [
        "frame_present_2"
    ]


def test_frame_resolves_aliases_to_canonical_names():
    response = {
        "decision": "黑袍法师随行抵达。",
        "frame": _frame(
            active_present_npcs=["黑袍法师"],
            evidence_fact_ids=["f1"],
        ),
        "operations": [],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
        npc_aliases={"伊恩": ["黑袍法师"]},
    )

    assert result.scene.participant_names == ["主角", "米莉", "伊恩"]


def test_compile_frame_ignores_player_in_both_lists():
    frame = FinalSceneFrame(
        active_present_npcs=["主角", "米莉"],
        departed_npcs=["主角"],
        evidence_fact_ids=["f1"],
    )
    state = _state()

    operations, warnings = compile_frame_operations(state, frame)

    assert [op.type for op in operations] == []
    assert warnings == []


def test_compile_frame_presence_wins_over_departed_conflict():
    frame = FinalSceneFrame(
        departed_npcs=["米莉"],
        evidence_fact_ids=["f1"],
        active_present_npcs=["米莉"],
    )
    state = _state()

    operations, _warnings = compile_frame_operations(state, frame)

    assert operations == []


def test_compile_frame_deduplicates_alias_variants():
    frame = FinalSceneFrame(
        active_present_npcs=["莎拉", "萨拉"],
        evidence_fact_ids=["f1"],
    )
    state = _state()
    aliases = {"莎拉": ["萨拉"]}

    operations, _warnings = compile_frame_operations(
        state,
        frame,
        npc_aliases=aliases,
    )

    assert [op.name for op in operations] == ["莎拉"]


def test_calculate_phase_advance_edges():
    # Whole days without a target phase keep the same time of day.
    assert calculate_phase_advance("深夜", 1, None) == 7
    # Same phase with no day offset is a no-op.
    assert calculate_phase_advance("清晨", 0, "清晨") == 0
    # Negative advances are rejected.
    assert calculate_phase_advance("深夜", 0, "下午") is None
    # Unknown phases are rejected.
    assert calculate_phase_advance("正午", 0, None) is None


def test_frame_day_only_offset_keeps_time_of_day():
    result, _ = _update({
        "frame": _frame(day_offset=2, evidence_fact_ids=["f1"]),
    })

    assert result.world_clock == WorldClock(day=3, phase="深夜")


def test_frame_without_valid_evidence_is_ignored_with_warning():
    response = {
        "decision": "地点变更但证据无效。",
        "frame": _frame(
            final_location="城门",
            evidence_fact_ids=["bogus"],
        ),
        "operations": [],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())
    state = _state()

    result = updater.update(
        state,
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == state
    assert updater.last_change_set.operations == []
    assert any(
        warning.code == "frame_ignored_no_evidence"
        for warning in updater.last_warnings
    )


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


def test_operation_without_remaining_evidence_is_dropped():
    response = {
        "decision": "证据无效。",
        "frame": _frame(),
        "operations": [
            {
                **_operation("op1", "money_delta", delta=5),
                "evidence_fact_ids": ["missing"],
            }
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

    assert result.resources.money == 20
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op1"
        for warning in updater.last_warnings
    )


def test_forbidden_operation_types_are_dropped_with_warning():
    response = {
        "decision": "模型仍输出 frame 编译类操作。",
        "frame": _frame(),
        "operations": [
            _operation("op1", "location_change", location="城门"),
            _operation("op2", "participant_remove", name="米莉"),
            _operation("op3", "world_clock_advance", phases=2),
        ],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())
    state = _state()

    result = updater.update(
        state,
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == state
    dropped = [
        warning for warning in updater.last_warnings
        if warning.code == "state_update_operation_compiled_by_frame"
    ]
    assert sorted(warning.operation_id for warning in dropped) == [
        "op1",
        "op2",
        "op3",
    ]


def test_attitude_after_frame_removal_is_skipped_with_warning():
    response = {
        "decision": "米莉离开当前场景，后续状态不再挂靠场景参与者。",
        "frame": _frame(departed_npcs=["米莉"], evidence_fact_ids=["f1"]),
        "operations": [
            _operation(
                "op1",
                "participant_attitude_change",
                name="米莉",
                attitude_to_player="信任",
            ),
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

    assert result.scene.participant_names == ["主角"]
    assert len(updater.last_warnings) == 1
    warning = updater.last_warnings[0]
    assert warning.operation_id == "op1"
    assert warning.operation_type == "participant_attitude_change"
    assert warning.target == "米莉"
    assert warning.code == "npc_target_not_present"


def test_attitude_before_frame_add_is_applied_in_two_pass():
    result, _ = _update({
        "frame": _frame(active_present_npcs=["莎拉"], evidence_fact_ids=["f1"]),
        "operations": [
            _operation(
                "op1",
                "participant_attitude_change",
                name="莎拉",
                attitude_to_player="信任",
            ),
        ],
    })

    assert [(p.name, p.attitude_to_player) for p in result.scene.participants] == [
        ("主角", None),
        ("米莉", "谨慎"),
        ("莎拉", "信任"),
    ]


def test_missing_participant_attitude_target_is_skipped():
    operation = _operation(
        "op1",
        "participant_attitude_change",
        name="不在场者",
        attitude_to_player="警惕",
    )
    response = {
        "decision": "目标不属于当前场景参与者。",
        "frame": _frame(),
        "operations": [operation],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert updater.last_warnings[0].operation_type == operation["type"]


def test_participant_attitude_targeting_player_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "错误地把玩家作为 NPC 态度目标。",
            "frame": _frame(),
            "operations": [
                _operation(
                    "op1",
                    "participant_attitude_change",
                    name="主角",
                    attitude_to_player="信任",
                )
            ],
        },
        ensure_ascii=False,
    )
    mock = MockLLM()
    mock.set_responses([response])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert updater.last_warnings[0].code == "attitude_target_player"


def test_participant_attitude_same_value_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "重复设置相同的态度。",
            "frame": _frame(),
            "operations": [
                _operation(
                    "op1",
                    "participant_attitude_change",
                    name="米莉",
                    attitude_to_player="谨慎",
                )
            ],
        },
        ensure_ascii=False,
    )
    mock = MockLLM()
    mock.set_responses([response])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert updater.last_warnings[0].code == "attitude_no_change"


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


def test_illegal_delta_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "尝试移除不存在的物品。",
            "frame": _frame(),
            "operations": [
                _operation("op1", "inventory_delta", item="不存在", delta=-1)
            ]
        },
        ensure_ascii=False,
    )
    mock = MockLLM()
    mock.set_responses([response])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert updater.last_warnings[0].code == "inventory_item_missing"


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


def test_inventory_consume_before_obtain_keeps_net():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "inventory_delta", item="银钥匙", delta=-1),
                _operation("op2", "inventory_delta", item="银钥匙", delta=2),
            ]
        }
    )

    assert [(item.name, item.quantity) for item in result.resources.inventory] == [
        ("火把", 2),
        ("银钥匙", 1),
    ]


def test_money_spend_before_earn_keeps_net():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "money_delta", delta=-30),
                _operation("op2", "money_delta", delta=50),
            ]
        }
    )

    assert result.resources.money == 40


def test_asset_remove_before_add_keeps_sequential_order():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "fixed_asset_remove", asset="旧宅"),
                _operation("op2", "fixed_asset_add", asset="旧宅"),
            ]
        }
    )

    assert result.resources.fixed_assets == ["葡萄园", "旧宅"]


def test_resolution_schema_places_decision_frame_operations_first():
    schema = StateResolutionOutput.model_json_schema()

    assert list(schema["properties"])[:3] == ["decision", "frame", "operations"]


def test_operation_schema_has_no_condition_operations():
    schema_text = json.dumps(
        StateResolutionOutput.model_json_schema(),
        ensure_ascii=False,
    )

    assert "scene_condition" not in schema_text
    assert "npc_condition" not in schema_text
    assert "player_condition" not in schema_text
    assert "expires_after_phases" not in schema_text


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


def test_frame_with_unknown_paragraph_evidence_is_stripped_and_ignored():
    state = _state()
    response = {
        "decision": "使用了未知 paragraph ID。",
        "frame": _frame(
            final_location="城门",
            evidence_paragraph_ids=["turn_99_p999"],
        ),
        "operations": [],
    }
    provider = _CountingProvider([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(_gateway(provider))
    result = updater.update(
        state,
        json.dumps({"scene_focus": "降级", "facts": []}, ensure_ascii=False),
        core_memory=CoreMemory(items=[]),
        paragraph_texts={"turn_1_p001": "主角离开酒馆前往城门。"},
    )

    assert result.scene.location == "酒馆"
    assert provider.calls == 1
    assert any(
        warning.code == "frame_ignored_no_evidence"
        for warning in updater.last_warnings
    )


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


def test_salvage_uses_first_attempt_when_second_has_no_valid_ops():
    first = {
        "decision": "第一次有合法操作。",
        "frame": _frame(),
        "operations": [
            _operation("op1", "money_delta", delta=1),
            _structural_error_operation("op2"),
        ],
    }
    second = {
        "decision": "第二次全部非法。",
        "frame": _frame(),
        "operations": [_structural_error_operation("op3")],
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
    assert result.resources.money == 21
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op1"]
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op2"
        for warning in updater.last_warnings
    )

def test_model_calls_do_not_exceed_two_on_structural_errors():
    invalid = {
        "decision": "结构错误。",
        "frame": _frame(),
        "operations": [_structural_error_operation("op1")],
    }
    provider = _CountingProvider([
        json.dumps(invalid, ensure_ascii=False),
        json.dumps(invalid, ensure_ascii=False),
    ])
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert provider.calls == 2
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op1"
        for warning in updater.last_warnings
    )

def test_transient_provider_error_retries_once_then_propagates():
    class TransientFailureProvider(_CountingProvider):
        def generate(self, *args, **kwargs):
            self.calls += 1
            raise LLMProviderError("Mock provider error")

    provider = TransientFailureProvider()
    updater = StateUpdater(_gateway(provider))

    with pytest.raises(LLMProviderError):
        updater.update(
            _state(),
            _facts(),
            core_memory=CoreMemory(items=[]),
        )

    assert provider.calls == 2


def test_second_provider_failure_propagates():
    first = {
        "decision": "第一次结构非法，触发重试。",
        "frame": _frame(),
        "operations": [_structural_error_operation("op1")],
    }

    class FirstThenProviderFailure(_CountingProvider):
        def generate(self, *args, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return MockProvider.generate(self, *args, **kwargs)
            raise LLMProviderError("provider down")

    provider = FirstThenProviderFailure([
        json.dumps(first, ensure_ascii=False),
    ])
    updater = StateUpdater(_gateway(provider))

    with pytest.raises(LLMProviderError):
        updater.update(
            _state(),
            _facts(),
            core_memory=CoreMemory(items=[]),
        )

    assert provider.calls == 2


def test_apply_state_integrity_error_is_not_swallowed(monkeypatch):
    def boom(*args, **kwargs):
        raise ValueError("final state invalid")

    monkeypatch.setattr(
        "app.domain.current_state.updater.apply_state_changes",
        boom,
    )
    mock = MockLLM()
    mock.set_responses([
        json.dumps(
            {"decision": "合法", "frame": _frame(), "operations": []},
            ensure_ascii=False,
        ),
    ])
    updater = StateUpdater(mock.build_gateway())

    with pytest.raises(ValueError, match="final state invalid"):
        updater.update(
            _state(),
            _facts(),
            core_memory=CoreMemory(items=[]),
        )


def test_missing_decision_uses_fallback_and_applies_valid_ops():
    response = {
        "frame": _frame(),
        "operations": [_operation("op1", "money_delta", delta=5)],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.resources.money == 25
    assert updater.last_change_set.decision == (
        "State update applied without a decision; kept valid operations."
    )


def test_extra_top_level_fields_do_not_block_salvage():
    first = {
        "decision": "第一次带额外顶层字段。",
        "unexpected": True,
        "frame": _frame(),
        "operations": [_operation("op1", "money_delta", delta=1)],
    }
    second = {
        "decision": "第二次仍带额外顶层字段。",
        "unexpected": True,
        "frame": _frame(),
        "operations": [_operation("op2", "money_delta", delta=5)],
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

    assert result.resources.money == 25
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op2"]
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "top_level"
        for warning in updater.last_warnings
    )


def test_operation_without_any_evidence_is_dropped_without_retry():
    response = {
        "decision": "操作完全没有证据。",
        "frame": _frame(),
        "operations": [
            {
                "operation_id": "op1",
                "type": "money_delta",
                "reason": "无任何证据。",
                "delta": 5,
            }
        ],
    }
    provider = _CountingProvider([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.resources.money == 20
    assert provider.calls == 1
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op1"
        for warning in updater.last_warnings
    )


def test_frame_with_non_string_npc_entry_degrades_without_crash():
    response = {
        "decision": "名单含非字符串值。",
        "frame": {
            "final_location": None,
            "day_offset": 0,
            "final_phase": None,
            "active_present_npcs": [1],
            "departed_npcs": [],
            "evidence_fact_ids": ["f1"],
            "evidence_paragraph_ids": [],
        },
        "operations": [],
    }
    mock = MockLLM()
    mock.set_responses([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(mock.build_gateway())
    state = _state()

    result = updater.update(
        state,
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == state
    assert updater.last_warnings[0].code == "state_update_fallback"


def test_llm_operation_id_cannot_use_reserved_frame_namespace():
    response = {
        "decision": "模型占用了保留 ID 前缀。",
        "frame": _frame(
            final_location="城门",
            evidence_fact_ids=["f1"],
        ),
        "operations": [
            {
                "operation_id": "frame_location",
                "type": "money_delta",
                "reason": "与引擎编译 ID 冲突。",
                "delta": 5,
                "evidence_fact_ids": ["f1"],
            }
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

    assert result.scene.location == "城门"
    operation_ids = [
        op.operation_id for op in updater.last_change_set.operations
    ]
    assert operation_ids.count("frame_location") == 1
    assert any(
        warning.code == "state_update_operation_reserved_id"
        and warning.operation_id == "frame_location"
        for warning in updater.last_warnings
    )


def test_frame_operations_are_never_truncated_by_merge():
    response = {
        "decision": "大量参与者的 frame 加上 30 项资源操作。",
        "frame": _frame(
            active_present_npcs=["莎拉", "汤姆", "艾琳"],
            evidence_fact_ids=["f1"],
        ),
        "operations": [
            _operation(f"op{i}", "money_delta", delta=1)
            for i in range(1, 31)
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

    operations = updater.last_change_set.operations
    assert len(operations) == 33
    assert result.resources.money == 50
    assert result.scene.participant_names == [
        "主角",
        "米莉",
        "莎拉",
        "汤姆",
        "艾琳",
    ]
    assert [op.operation_id for op in operations][-30:] == [
        f"op{i}" for i in range(1, 31)
    ]


def test_frame_operations_scale_without_any_total_cap():
    response = {
        "decision": "31 名新进场者加 30 项资源操作，超过任何固定总上限。",
        "frame": _frame(
            active_present_npcs=[f"npc{ i:02d}" for i in range(1, 32)],
            evidence_fact_ids=["f1"],
        ),
        "operations": [
            _operation(f"op{i}", "money_delta", delta=1)
            for i in range(1, 31)
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

    operations = updater.last_change_set.operations
    assert len(operations) == 61
    assert result.resources.money == 50
    assert len(result.scene.participants) == 33


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
