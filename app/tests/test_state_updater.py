import json
from concurrent.futures import CancelledError
from pathlib import Path

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
from app.domain.current_state.changes import StateChangeSet


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


def _update(response: dict, *, state: CurrentState | None = None):
    response = {
        "decision": "Scene Facts 支持以下状态变化。",
        **response,
    }
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


def test_location_and_time_are_applied_by_program():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "location_change", location="城门"),
                _operation("op2", "world_clock_advance", phases=2),
            ]
        }
    )

    assert result.scene.location == "城门"
    assert result.scene.scene_id == "scene_010"
    assert result.world_clock == WorldClock(day=2, phase="清晨")
    assert not hasattr(result.player, "position")


def test_participant_state_is_added_changed_and_removed_in_order():
    result, _ = _update(
        {
            "operations": [
                _operation("op1", "participant_add", name="莎拉"),
                _operation(
                    "op2",
                    "participant_attitude_change",
                    name="莎拉",
                    attitude_to_player="信任",
                ),
                _operation("op3", "participant_remove", name="米莉"),
            ]
        }
    )

    assert result.scene.participant_names == ["主角", "莎拉"]
    sarah = result.scene.participants[1]
    assert sarah.attitude_to_player == "信任"


def test_attitude_after_participant_remove_is_skipped_with_warning():
    response = {
        "decision": "米莉离开当前场景，后续状态不再挂靠场景参与者。",
        "operations": [
            _operation("op1", "participant_remove", name="米莉"),
            _operation(
                "op2",
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
    assert warning.operation_id == "op2"
    assert warning.operation_type == "participant_attitude_change"
    assert warning.target == "米莉"
    assert warning.code == "npc_target_not_present"


def test_missing_participant_attitude_target_is_skipped():
    operation = _operation(
        "op1",
        "participant_attitude_change",
        name="不在场者",
        attitude_to_player="警惕",
    )
    response = {
        "decision": "目标不属于当前场景参与者。",
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


def test_participant_add_already_present_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "重复添加已在场的米莉。",
            "operations": [_operation("op1", "participant_add", name="米莉")],
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
    assert updater.last_warnings[0].code == "participant_already_present"


def test_participant_remove_missing_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "移除不在场的莎拉。",
            "operations": [_operation("op1", "participant_remove", name="莎拉")],
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
    assert updater.last_warnings[0].code == "participant_not_present"


def test_participant_attitude_same_value_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "重复设置相同的态度。",
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


def test_unknown_evidence_retries_with_validation_feedback():
    mock = MockLLM()
    mock.set_responses(
        [
            json.dumps(
                {
                    "decision": "事实引用无效，不应通过校验。",
                    "operations": [
                        {
                            **_operation("op1", "money_delta", delta=1),
                            "evidence_fact_ids": ["missing"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            json.dumps(
                {"decision": "无合法变化。", "operations": []},
                ensure_ascii=False,
            ),
        ]
    )
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()


def test_illegal_delta_is_skipped_with_warning():
    response = json.dumps(
        {
            "decision": "尝试移除不存在的物品。",
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


def test_fallback_keeps_previous_state_after_retries_exhausted():
    invalid = json.dumps(
        {
            "decision": "引用不存在的证据。",
            "operations": [
                {
                    **_operation("op1", "money_delta", delta=-1),
                    "evidence_fact_ids": ["missing"],
                }
            ],
        },
        ensure_ascii=False,
    )
    mock = MockLLM()
    mock.set_responses([invalid, invalid])
    updater = StateUpdater(mock.build_gateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert len(updater.last_warnings) == 1
    assert updater.last_warnings[0].code == "state_update_fallback"
    assert "unknown Scene Fact IDs" in updater.last_warnings[0].message


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


def test_provider_error_degrades_to_previous_state():
    updater = StateUpdater(_FailingGateway())

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert len(updater.last_warnings) == 1
    assert updater.last_warnings[0].code == "state_update_fallback"
    assert "provider down" in updater.last_warnings[0].message


def test_attitude_before_add_is_applied_in_two_pass():
    result, _ = _update(
        {
            "operations": [
                _operation(
                    "op1",
                    "participant_attitude_change",
                    name="莎拉",
                    attitude_to_player="信任",
                ),
                _operation("op2", "participant_add", name="莎拉"),
            ]
        }
    )

    assert [(p.name, p.attitude_to_player) for p in result.scene.participants] == [
        ("主角", None),
        ("米莉", "谨慎"),
        ("莎拉", "信任"),
    ]


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


def test_prompt_defines_incremental_state_contract():
    prompt = Path("app/prompts/confirmation/state_update_v1.md").read_text(
        encoding="utf-8"
    )

    template_sections = [
        "# 输出规范",
        "# 核心规则",
        "# 禁止事项",
        "# 例子",
        "# 上下文",
        "# 输入",
    ]
    section_positions = [prompt.index(section) for section in template_sections]
    assert section_positions == sorted(section_positions)
    assert "不输出完整状态" in prompt
    assert "location_change" in prompt
    assert "world_clock_advance" in prompt
    assert "participant_attitude_change" in prompt
    assert "scene_condition_add" not in prompt
    assert "npc_condition_add" not in prompt
    assert "player_condition_add" not in prompt
    assert "不保存玩家、NPC 或场景 condition" in prompt
    assert "inventory_delta" in prompt
    assert prompt.index("`decision`") < prompt.index("每项操作都必须填写")
    assert "资源数额与单位是否一致" in prompt
    assert prompt.index("- reason：") < prompt.index("- type：")
    assert "判断 Scene Facts / 正文段落证据是否真正支持一项合法、可执行的状态变化" in prompt


def test_operation_schema_places_reason_before_type():
    schema = StateChangeSet.model_json_schema()

    assert list(schema["properties"])[:2] == ["decision", "operations"]
    assert "decision" in schema["required"]
    for operation_name in ("MoneyDelta", "InventoryDelta", "LocationChange"):
        properties = list(schema["$defs"][operation_name]["properties"])
        assert properties.index("reason") < properties.index("type")


def test_operation_schema_has_no_condition_operations():
    schema_text = json.dumps(
        StateChangeSet.model_json_schema(),
        ensure_ascii=False,
    )

    assert "scene_condition" not in schema_text
    assert "npc_condition" not in schema_text
    assert "player_condition" not in schema_text
    assert "expires_after_phases" not in schema_text


def test_paragraph_evidence_applies_state_operation():
    state = _state()
    response = {
        "decision": "正文段落证据支持状态变化。",
        "operations": [
            {
                "operation_id": "op1",
                "type": "location_change",
                "reason": "正文段落证据支持该变化。",
                "evidence_fact_ids": [],
                "evidence_paragraph_ids": ["turn_1_p001"],
                "location": "城门",
            }
        ],
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


def test_retry_feedback_allows_paragraph_evidence_after_bad_paragraph_id():
    state = _state()
    bad = {
        "decision": "首次输出使用了未知 paragraph ID。",
        "operations": [
            {
                "operation_id": "op1",
                "type": "location_change",
                "reason": "正文段落证据支持该变化。",
                "evidence_fact_ids": [],
                "evidence_paragraph_ids": ["turn_99_p999"],
                "location": "城门",
            }
        ],
    }
    good = {
        "decision": "第二次输出使用有效 paragraph ID。",
        "operations": [
            {
                "operation_id": "op1",
                "type": "location_change",
                "reason": "正文段落证据支持该变化。",
                "evidence_fact_ids": [],
                "evidence_paragraph_ids": ["turn_1_p001"],
                "location": "城门",
            }
        ],
    }
    mock = MockLLM()
    mock.set_responses([
        json.dumps(bad, ensure_ascii=False),
        json.dumps(good, ensure_ascii=False),
    ])
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


def test_salvage_uses_second_attempt_valid_subset_only():
    first = {
        "decision": "第一次有合法与非法操作。",
        "operations": [
            _operation("op1", "location_change", location="城门"),
            {
                **_operation("op2", "money_delta", delta=1),
                "evidence_fact_ids": ["missing"],
            },
        ],
    }
    second = {
        "decision": "第二次只保留合法操作。",
        "operations": [
            _operation("op3", "money_delta", delta=5),
            {
                **_operation("op4", "money_delta", delta=10),
                "evidence_fact_ids": ["missing"],
            },
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
    assert updater.last_warnings[0].code == "state_update_operation_dropped"
    assert updater.last_warnings[0].operation_id == "op4"


def test_salvage_uses_first_attempt_when_second_has_no_valid_ops():
    first = {
        "decision": "第一次有合法操作。",
        "operations": [
            _operation("op1", "location_change", location="城门"),
            {
                **_operation("op2", "money_delta", delta=1),
                "evidence_fact_ids": ["missing"],
            },
        ],
    }
    second = {
        "decision": "第二次全部非法。",
        "operations": [
            {
                **_operation("op3", "money_delta", delta=10),
                "evidence_fact_ids": ["missing"],
            }
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

    assert result.scene.location == "城门"
    assert result.resources.money == 20
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op1"]
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op2"
        for warning in updater.last_warnings
    )


def test_single_schema_error_does_not_drop_valid_siblings_on_salvage():
    first = {
        "decision": "首次含 schema 错误。",
        "operations": [
            _operation("op1", "location_change", location="城门"),
            {
                "operation_id": "op2",
                "reason": "缺少 type",
                "evidence_fact_ids": ["f1"],
            },
        ],
    }
    second = {
        "decision": "第二次仍含 schema 错误。",
        "operations": [
            _operation("op3", "money_delta", delta=5),
            {
                "operation_id": "op4",
                "reason": "缺少 type",
                "evidence_fact_ids": ["f1"],
            },
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
    dropped = updater.last_warnings[0]
    assert dropped.code == "state_update_operation_dropped"
    assert dropped.operation_id == "op4"
    assert dropped.operation_type == "unknown"


def test_duplicate_operation_id_keeps_first_valid_and_drops_duplicate():
    first = {
        "decision": "重复 op1。",
        "operations": [
            _operation("op1", "money_delta", delta=1),
            _operation("op1", "money_delta", delta=2),
        ],
    }
    second = {
        "decision": "重复 op2。",
        "operations": [
            _operation("op2", "money_delta", delta=5),
            _operation("op2", "money_delta", delta=6),
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

    assert result.resources.money == 25
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op2"]
    assert [op.delta for op in updater.last_change_set.operations] == [5]
    assert any(
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "op2"
        for warning in updater.last_warnings
    )


def test_model_calls_do_not_exceed_two():
    invalid = {
        "decision": "非法证据。",
        "operations": [
            {
                **_operation("op1", "money_delta", delta=1),
                "evidence_fact_ids": ["missing"],
            }
        ],
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


def test_permanent_provider_error_is_not_retried():
    class PermanentFailureProvider(_CountingProvider):
        def generate(self, *args, **kwargs):
            self.calls += 1
            raise LLMProviderError("Authentication failed: 401")

    provider = PermanentFailureProvider()
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert provider.calls == 1
    assert updater.last_warnings[0].code == "state_update_fallback"


def test_transient_provider_error_retries_once_then_degrades():
    class TransientFailureProvider(_CountingProvider):
        def generate(self, *args, **kwargs):
            self.calls += 1
            raise LLMProviderError("Mock provider error")

    provider = TransientFailureProvider()
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert provider.calls == 2
    assert updater.last_warnings[0].code == "state_update_fallback"


def test_no_effect_does_not_trigger_retry():
    response = {
        "decision": "移除不在场角色。",
        "operations": [_operation("op1", "participant_remove", name="莎拉")],
    }
    provider = _CountingProvider([json.dumps(response, ensure_ascii=False)])
    updater = StateUpdater(_gateway(provider))

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result == _state()
    assert provider.calls == 1
    assert updater.last_warnings[0].code == "participant_not_present"


def test_second_provider_failure_uses_first_valid_subset():
    first = {
        "decision": "第一次有合法操作。",
        "operations": [
            _operation("op1", "location_change", location="城门"),
            {
                **_operation("op2", "money_delta", delta=1),
                "evidence_fact_ids": ["missing"],
            },
        ],
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

    result = updater.update(
        _state(),
        _facts(),
        core_memory=CoreMemory(items=[]),
    )

    assert result.scene.location == "城门"
    assert [op.operation_id for op in updater.last_change_set.operations] == ["op1"]
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
        json.dumps({"decision": "合法", "operations": []}, ensure_ascii=False),
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
        "operations": [_operation("op1", "money_delta", delta=1)],
    }
    second = {
        "decision": "第二次仍带额外顶层字段。",
        "unexpected": True,
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
        warning.code == "state_update_operation_dropped"
        and warning.operation_id == "operations"
        for warning in updater.last_warnings
    )
