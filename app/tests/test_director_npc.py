import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from app.core.llm import MockLLM
from app.domain.core_memory.schemas import CoreMemory
from app.domain.current_state.schemas import CurrentState, PlayerState, SceneState
from app.domain.director_event.schemas import DirectorEvent
from app.domain.director_npc.schemas import (
    DirectorNPCDispatchDecision,
    DirectorNPCDraft,
    DirectorNPCWorkflowResult,
)
from app.domain.world_setting.schemas import (
    SettingAnchorPacket,
    SettingSeedCandidate,
    SettingSourceDocument,
)
from app.domain.world_setting.store import WorldSettingStore
from app.domain.director_npc.store import DirectorNPCStore
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.director_npc.workflows import (
    DIRECTOR_NPC_FIRST_FORCED_TURN,
    DIRECTOR_NPC_STANDBY_TARGET,
    _director_npc_trigger_reason,
    create_director_npc_standby,
    director_npc_standby_missing_count,
    maybe_director_npc_workflow,
    restock_director_npc_standby,
)
from app.graph import confirmation_helpers


class TestDirectorNPCDispatchPrompt:
    def test_reason_precedes_dispatch_decision_in_schema(self):
        assert list(DirectorNPCDispatchDecision.model_json_schema()["properties"]) == [
            "reason",
            "action",
            "selected_id",
            "selected_name",
        ]

    def test_location_is_soft_anchor_not_hard_block(self):
        prompt = Path("app/prompts/generation/director_npc_dispatch_v1.md").read_text(encoding="utf-8")

        assert "地理位置不是硬锁定" in prompt
        assert "默认选择 `none`" in prompt
        assert "你不负责设计出场桥段" in prompt
        assert "不得先选人再为选择寻找理由" in prompt


class TestDirectorNPCConfirmationSceneProjection:
    def test_scene_text_is_unchanged_without_director_npc_dispatch(self):
        pending = type("Pending", (), {})()
        pending.director_npc_result = None

        projected = confirmation_helpers.project_scene_text_for_confirmation(
            "巷口有人影一闪。",
            pending=pending,
            confirmed_director_npc_draft=None,
        )

        assert projected == "巷口有人影一闪。"

    def test_scene_text_projection_adds_dispatch_identity_without_gender_assumption(self):
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response("苏念棠")))
        pending = type("Pending", (), {})()
        pending.director_npc_result = DirectorNPCWorkflowResult(
            dispatch=DirectorNPCDispatchDecision(
                action="dispatch_standby",
                selected_id=draft.draft_id,
                selected_name=draft.name,
                reason="test",
            )
        )

        projected = confirmation_helpers.project_scene_text_for_confirmation(
            "巷口的暗处站着一个年轻角色。",
            pending=pending,
            confirmed_director_npc_draft=draft,
        )

        assert "【确认用系统注记】" in projected
        assert "导演 NPC：苏念棠" in projected
        assert "新增角色" in projected
        assert "不代表玩家知道TA的姓名" in projected
        assert "hidden_secret" in projected
        assert "女性" not in projected
        assert projected.endswith("巷口的暗处站着一个年轻角色。")


class SpyGateway:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(json.loads(self.response))


class QueueSpyGateway:
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses.pop(0)
        return kwargs["output_schema"].model_validate(json.loads(response))


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="北郡修道院",
            participants=["米莉"],
        ),
        player=PlayerState(name="主角"),
    )


def _director_event(role: str, title: str) -> DirectorEvent:
    return DirectorEvent(
        role=role,
        title=title,
        premise=f"{title}正在形成独立局势。",
        actors=["外部行动方"],
        objective="推进自身目标。",
        bridge_anchors=["北郡修道院"],
        milestones=["局势形成。", "局势发展。", "局势收束。"],
        milestone_interval_steps=[1, 1],
        created_turn=1,
    )


def _draft_response(name="艾琳娜", *, draft_id: str | None = None) -> str:
    payload = {
        "setting_expansion_note": "这个 NPC 让旧药路从背景运输网络变成具体的人际接口：TA 依靠旧药路谋生，也因旧药路的风险形成秘密与行动边界。",
        "name": name,
        "role": "流浪药剂师",
        "appearance": "背着旧药箱、披着灰绿色旅行斗篷的年轻药剂师。",
        "personality_traits": ["谨慎", "好奇", "嘴硬"],
        "known_abilities": ["辨识药草", "急救"],
        "faction": None,
        "core_motivation": "想证明自己不只是导师的影子。",
        "hidden_secret": "她正在寻找一份被自己弄丢的导师手稿。",
        "conflict_style": "先谈条件，必要时用药粉拖延。",
        "social_style": "礼貌但保持距离，习惯用交易掩饰求助。",
        "encounter_potentials": [
            "沿旧药路交换药材",
            "在渡口行会误认徽记",
            "于山地驿道共同避险",
        ],
        "status": "standby",
        "created_turn": 4,
        "dispatched_turn": None,
    }
    if draft_id is not None:
        payload["draft_id"] = draft_id
    return json.dumps(
        payload,
        ensure_ascii=False,
    )


def _draft_response_with(name: str = "艾琳娜", **updates) -> str:
    payload = json.loads(_draft_response(name))
    payload.update(updates)
    return json.dumps(payload, ensure_ascii=False)


def _setting_anchor(
    setting_id: str,
    label: str = "旧药路",
) -> SettingAnchorPacket:
    return SettingAnchorPacket(
        setting_id=setting_id,
        label=label,
        kind="resource_or_network",
        definition=f"{label}是一条仍被地方药商使用的旧运输网络。",
        setting_facts=[
            f"{label}是一条仍被地方药商使用的旧运输网络。",
            "它只覆盖北郡周边，不是跨国商路。",
        ],
        relation_to_known_world=["连接北郡与周边药材产地。"],
        world_effects=["它改变北郡药材、消息和风险的流动方式。"],
        scope_and_boundaries=["只覆盖北郡周边，不是跨国商路。"],
        player_known_facts=[0],
        director_private_facts=[1],
    )


def _world_setting_store(*labels: str) -> tuple[WorldSettingStore, list[SettingAnchorPacket]]:
    store = WorldSettingStore(":memory:")
    anchors = []
    for index, label in enumerate(labels, start=1):
        document = SettingSourceDocument(
            source_key=f"scene:{index}",
            source_type="scene_text",
            source_ref=f"scene_{index}",
            visibility="player_known",
            content=f"正文提到了{label}。",
        )
        saved = store.apply_extraction(
            [
                SettingSeedCandidate(
                    label=label,
                    kind="resource_or_network",
                    basic_concept=f"{label}是已经出现的地方运输网络。",
                    source_key=document.source_key,
                    source_span=label,
                )
            ],
            source_documents={document.source_key: document},
            turn_index=index,
        )
        anchors.append(_setting_anchor(saved[0], label))
    return store, anchors


def _preparation_response(anchors: list[SettingAnchorPacket]) -> str:
    return json.dumps(
        {
            "preparations": [
                {
                    "setting_id": anchor.setting_id,
                    "decision": "develop",
                    "reason": "基础概念可靠，需要最低限度明确世界影响。",
                    "anchor": anchor.model_dump(mode="json"),
                }
                for anchor in anchors
            ]
        },
        ensure_ascii=False,
    )


def _maintenance_response(anchors: list[SettingAnchorPacket]) -> str:
    return json.dumps(
        {
            "groups": [
                {
                    "seed_ids": [anchor.setting_id],
                    "resolution": "independent",
                    "reason": "没有相同的成熟设定。",
                    "target_setting_id": None,
                    "canonical_seed_id": anchor.setting_id,
                    "canonical_label": anchor.label,
                    "canonical_kind": anchor.kind,
                    "canonical_basic_concept": (
                        f"{anchor.label}是已经出现的地方运输网络。"
                    ),
                    "supplemented_basic_concept": None,
                    "supplemented_definition": None,
                    "fact_additions": [],
                }
                for anchor in anchors
            ]
        },
        ensure_ascii=False,
    )


def _draft_responses(*names: str) -> list[str]:
    return [_draft_response(name) for name in names]


def _dispatch_response(action="dispatch_standby", selected_id="director_npc_x", selected_name="艾琳娜") -> str:
    payload = {
        "action": action,
        "selected_id": selected_id,
        "selected_name": selected_name,
        "reason": "她能用药剂师身份与当前旅途产生交集。",
    }
    if action == "none":
        payload.update({
            "selected_id": None,
            "selected_name": None,
        })
    return json.dumps(payload, ensure_ascii=False)


class TestDirectorNPCTrigger:
    def test_turn_one_forces_first_dispatch_even_without_five_turn_window(self):
        reason = _director_npc_trigger_reason(
            turn_index=1,
            last_dispatch_turn=0,
        )

        assert reason == f"forced director NPC dispatch on turn {DIRECTOR_NPC_FIRST_FORCED_TURN}"

    def test_turn_one_force_does_not_repeat_after_same_turn_dispatch(self):
        reason = _director_npc_trigger_reason(
            turn_index=1,
            last_dispatch_turn=1,
        )

        assert reason is None

    def test_blocks_within_two_turns(self):
        reason = _director_npc_trigger_reason(
            turn_index=10,
            last_dispatch_turn=8,
        )

        assert reason is None

    def test_triggers_after_two_turn_cooldown(self):
        reason = _director_npc_trigger_reason(
            turn_index=11,
            last_dispatch_turn=8,
        )

        assert reason == "director NPC dispatch check after cooldown"


class TestDirectorNPCWorkflow:
    def test_missing_count_reports_standby_gap(self):
        director_store = DirectorNPCStore(":memory:")
        assert director_npc_standby_missing_count(director_store) == 3

        director_store.save(DirectorNPCDraft.model_validate(json.loads(_draft_response())))
        assert director_npc_standby_missing_count(director_store) == 2

    def test_create_standby_creates_one_draft(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store("旧药路")
        creator = MockLLM()
        creator.set_responses([_draft_response()])

        created = create_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            setting_anchor=anchors[0],
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
        )

        assert created.status == "standby"
        assert created.source_setting_id == anchors[0].setting_id
        assert created.creative_brief is not None
        assert len(director_store.list_standby()) == 1
        assert setting_store.get_by_id(anchors[0].setting_id).availability == "consumed"

    def test_create_standby_retries_when_business_fields_do_not_use_exact_setting_label(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store("禁忌魔法：意识转移")
        creator = MockLLM()
        creator.set_responses(
            [
                _draft_response_with(
                    "艾琳娜",
                    setting_expansion_note="禁忌魔法：意识转移会改变她的生活接口。",
                    core_motivation="想研究被教廷审查的禁书。",
                    encounter_potentials=[
                        "交换禁书残页",
                        "躲避教廷审查",
                        "寻找失落手稿",
                    ],
                ),
                _draft_response_with(
                    "薇拉",
                    core_motivation="想证明禁忌魔法：意识转移不是单纯的邪术，而是能解释自我连续性的危险技术。",
                ),
            ]
        )

        created = create_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            setting_anchor=anchors[0],
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
        )

        assert created.name == "薇拉"
        assert "禁忌魔法：意识转移" in created.core_motivation
        assert "禁忌魔法：意识转移" not in created.model_dump_json(
            exclude={"core_motivation"},
        )

    def test_create_standby_assigns_program_fields(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store("旧药路")
        gateway = SpyGateway(_draft_response(draft_id="llm_supplied_id"))

        created = create_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=7,
            director_npc_store=director_store,
            setting_anchor=anchors[0],
            world_setting_store=setting_store,
            creator_gateway=gateway,
        )

        output_schema = json.loads(gateway.calls[0]["input_data"]["output_schema_json"])
        assert "draft_id" not in output_schema["properties"]
        assert "status" not in output_schema["properties"]
        assert "created_turn" not in output_schema["properties"]
        assert created.draft_id.startswith("director_npc_")
        assert created.draft_id != "llm_supplied_id"
        assert created.status == "standby"
        assert created.created_turn == 7
        assert created.dispatched_turn is None

    def test_restock_standby_creates_when_pool_is_empty(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store("旧药路", "渡口行会", "山地驿道")
        creator = MockLLM()
        creator.set_responses([
            _maintenance_response(anchors),
            _preparation_response(anchors),
            *_draft_responses("艾琳娜", "薇拉", "洛珊"),
        ])

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
            setting_maintainer_gateway=creator.build_gateway(),
            setting_preparer_gateway=creator.build_gateway(),
            reservation_owner="test-restock",
        )

        assert len(created) == DIRECTOR_NPC_STANDBY_TARGET
        assert all(draft.status == "standby" for draft in created)
        assert len(director_store.list_standby()) == DIRECTOR_NPC_STANDBY_TARGET

    def test_restock_standby_fills_partial_pool(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store("渡口行会", "山地驿道")
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response()))
        director_store.save(draft)
        creator = MockLLM()
        creator.set_responses([
            _maintenance_response(anchors),
            _preparation_response(anchors),
            *_draft_responses("薇拉", "洛珊"),
        ])

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
            setting_maintainer_gateway=creator.build_gateway(),
            setting_preparer_gateway=creator.build_gateway(),
            reservation_owner="test-partial",
        )

        assert len(created) == 2
        assert len(director_store.list_standby()) == DIRECTOR_NPC_STANDBY_TARGET

    def test_restock_persists_every_successful_preparation(self):
        director_store = DirectorNPCStore(":memory:")
        director_store.save(
            DirectorNPCDraft.model_validate(json.loads(_draft_response("艾琳娜")))
        )
        director_store.save(
            DirectorNPCDraft.model_validate(json.loads(_draft_response("薇拉")))
        )
        setting_store, anchors = _world_setting_store(
            "旧药路",
            "渡口行会",
            "山地驿道",
        )
        gateway = QueueSpyGateway(
            [
                _maintenance_response(anchors),
                _preparation_response(anchors),
                _draft_response("洛珊"),
            ]
        )

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=gateway,
            setting_maintainer_gateway=gateway,
            setting_preparer_gateway=gateway,
            reservation_owner="test-persist-all-preparations",
        )

        settings = setting_store.list_all()
        assert len(created) == 1
        assert sum(
            setting.availability == "consumed" for setting in settings
        ) == 1
        unused = [
            setting
            for setting in settings
            if setting.availability == "available"
        ]
        assert len(unused) == 2
        assert all(setting.maturity == "usable" for setting in unused)
        assert all(setting.prepared_anchor is not None for setting in unused)

    def test_restock_reuses_usable_anchor_without_maintain_or_prepare(self):
        director_store = DirectorNPCStore(":memory:")
        director_store.save(
            DirectorNPCDraft.model_validate(json.loads(_draft_response("艾琳娜")))
        )
        director_store.save(
            DirectorNPCDraft.model_validate(json.loads(_draft_response("薇拉")))
        )
        setting_store, anchors = _world_setting_store("旧药路")
        setting_store.save_preparation(anchors[0].setting_id, anchors[0])
        gateway = QueueSpyGateway([_draft_response("洛珊")])

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=gateway,
            setting_maintainer_gateway=gateway,
            setting_preparer_gateway=gateway,
            reservation_owner="test-reuse-prepared",
        )

        assert len(created) == 1
        assert [call["task"] for call in gateway.calls] == [
            "director_npc_create"
        ]

    def test_restock_standby_does_not_create_when_pool_is_full(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store = WorldSettingStore(":memory:")
        for response in _draft_responses("艾琳娜", "薇拉", "洛珊"):
            director_store.save(DirectorNPCDraft.model_validate(json.loads(response)))
        creator = MockLLM()

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
            setting_maintainer_gateway=creator.build_gateway(),
            setting_preparer_gateway=creator.build_gateway(),
            reservation_owner="test-full",
        )

        assert created == []
        assert len(director_store.list_standby()) == DIRECTOR_NPC_STANDBY_TARGET

    def test_restock_does_not_fall_back_to_unanchored_creation(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store = WorldSettingStore(":memory:")
        creator = MockLLM()

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=creator.build_gateway(),
            setting_maintainer_gateway=creator.build_gateway(),
            setting_preparer_gateway=creator.build_gateway(),
            reservation_owner="test-no-settings",
        )

        assert created == []
        assert director_store.list_standby() == []

    def test_restock_persists_consumer_rejections_without_calling_creator(self):
        director_store = DirectorNPCStore(":memory:")
        setting_store, anchors = _world_setting_store(
            "旧药路",
            "渡口行会",
            "山地驿道",
        )
        gateway = QueueSpyGateway([
            _maintenance_response(anchors),
            json.dumps(
                {
                    "preparations": [
                        {
                            "setting_id": anchor.setting_id,
                            "decision": "reject",
                            "reason": "只是一次性描述，无法形成世界影响。",
                            "anchor": None,
                        }
                        for anchor in anchors
                    ]
                },
                ensure_ascii=False,
            )
        ])

        created = restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=gateway,
            setting_maintainer_gateway=gateway,
            setting_preparer_gateway=gateway,
            reservation_owner="test-rejected-settings",
        )

        assert created == []
        assert [call["task"] for call in gateway.calls] == [
            "setting_maintain",
            "setting_anchor_prepare"
        ]
        assert {
            setting.availability for setting in setting_store.list_all()
        } == {"rejected"}

    def test_restock_passes_current_standby_as_reserved_names(self):
        director_store = DirectorNPCStore(":memory:")
        director_store.save(DirectorNPCDraft.model_validate(json.loads(_draft_response("艾琳娜"))))
        setting_store, anchors = _world_setting_store("渡口行会", "山地驿道")
        gateway = QueueSpyGateway([
            _maintenance_response(anchors),
            _preparation_response(anchors),
            *_draft_responses("薇拉", "洛珊"),
        ])

        restock_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="主角",
            turn_index=4,
            director_npc_store=director_store,
            world_setting_store=setting_store,
            creator_gateway=gateway,
            setting_maintainer_gateway=gateway,
            setting_preparer_gateway=gateway,
            reservation_owner="test-existing",
        )

        create_traces = [
            call
            for call in gateway.calls
            if call["task"] == "director_npc_create"
        ]
        first_call_existing = json.loads(create_traces[0]["input_data"]["existing_npcs_json"])
        second_call_existing = json.loads(create_traces[1]["input_data"]["existing_npcs_json"])
        assert first_call_existing == [
            {
                "name": "艾琳娜",
                "role": "流浪药剂师",
                "core_motivation": "想证明自己不只是导师的影子。",
                "hidden_secret": "她正在寻找一份被自己弄丢的导师手稿。",
            }
        ]
        assert second_call_existing == [
            {
                "name": "艾琳娜",
                "role": "流浪药剂师",
                "core_motivation": "想证明自己不只是导师的影子。",
                "hidden_secret": "她正在寻找一份被自己弄丢的导师手稿。",
            },
            {
                "name": "薇拉",
                "role": "流浪药剂师",
                "core_motivation": "想证明自己不只是导师的影子。",
                "hidden_secret": "她正在寻找一份被自己弄丢的导师手稿。",
            },
        ]

    def test_creator_input_includes_existing_hidden_fields_and_protagonist_name(self):
        director_store = DirectorNPCStore(":memory:")
        npc_profile_store = NPCProfileStore(":memory:")
        npc_profile_store.upsert(
            NPCProfile(
                name="塞琳",
                role="炼金学院学徒",
                public_summary="正在追查莫里斯，已经与主角建立默契。",
                personality_traits=["谨慎"],
                known_abilities=["植物炼金术"],
                faction="北境炼金学院",
                core_motivation="证明自己的炼金术能救人。",
                hidden_secret="她隐瞒了一次失败实验。",
            )
        )
        gateway = SpyGateway(_draft_response())
        setting_store, anchors = _world_setting_store("旧药路")

        create_director_npc_standby(
            core_memory=CoreMemory(items=[]),
            protagonist_name="克莱",
            turn_index=4,
            director_npc_store=director_store,
            setting_anchor=anchors[0],
            world_setting_store=setting_store,
            npc_profile_store=npc_profile_store,
            creator_gateway=gateway,
        )

        input_data = gateway.calls[0]["input_data"]
        existing = json.loads(input_data["existing_npcs_json"])
        assert input_data["protagonist_name"] == "克莱"
        setting_anchor_view = json.loads(input_data["setting_anchor_json"])
        assert setting_anchor_view == {
            "label": anchors[0].label,
            "kind": anchors[0].kind,
            "definition": anchors[0].definition,
            "setting_facts": anchors[0].setting_facts,
            "world_effects": anchors[0].world_effects,
        }
        assert "setting_label" not in input_data
        assert "setting_definition" not in input_data
        assert "setting_facts_json" not in input_data
        assert "setting_world_effects_json" not in input_data
        assert existing == [
            {
                "name": "塞琳",
                "role": "炼金学院学徒",
                "core_motivation": "证明自己的炼金术能救人。",
                "hidden_secret": "她隐瞒了一次失败实验。",
            }
        ]

    def test_uses_existing_standby_and_dispatches(self):
        director_store = DirectorNPCStore(":memory:")
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response()))
        director_store.save(draft)
        for response in _draft_responses("薇拉", "洛珊"):
            director_store.save(DirectorNPCDraft.model_validate(json.loads(response)))
        dispatcher = MockLLM()
        dispatcher.set_responses([_dispatch_response(selected_id=draft.draft_id, selected_name=draft.name)])

        events = [
            _director_event("arc", "修道院旧档"),
            _director_event("ambient", "城外商队"),
        ]
        result = maybe_director_npc_workflow(
            core_memory=CoreMemory(items=[]),
            current_state=_state(),
            recent_summaries=[],
            director_events=events,
            user_input="我环顾四周寻找线索",
            turn_index=4,
            director_npc_store=director_store,
            dispatcher_gateway=dispatcher.build_gateway(),
        )

        assert result.dispatch is not None
        assert result.dispatch.action == "dispatch_standby"
        assert result.plot_injection is not None
        assert result.plot_injection.selected_name == draft.name
        assert result.plot_injection.selected_npc["hidden_secret"] == draft.hidden_secret
        assert director_store.get_meta_int("last_dispatch_turn") == 0

    def test_dispatcher_receives_dispatched_director_private_and_public_profile(self):
        director_store = DirectorNPCStore(":memory:")
        standby = DirectorNPCDraft.model_validate(json.loads(_draft_response("艾琳娜")))
        dispatched = DirectorNPCDraft.model_validate(json.loads(_draft_response("柯莱")))
        director_store.save(standby)
        for response in _draft_responses("薇拉", "洛珊"):
            director_store.save(DirectorNPCDraft.model_validate(json.loads(response)))
        director_store.save(dispatched.model_copy(update={"status": "dispatched", "dispatched_turn": 3}))
        npc_profile_store = NPCProfileStore(":memory:")
        npc_profile_store.upsert(
            NPCProfile(
                name="柯莱",
                role="吟游诗人",
                public_summary="剧情中只公开表现为路过的吟游诗人。",
            )
        )
        dispatcher = SpyGateway(_dispatch_response(action="none"))
        events = [
            _director_event("arc", "修道院旧档"),
            _director_event("ambient", "城外商队"),
        ]

        result = maybe_director_npc_workflow(
            core_memory=CoreMemory(items=[]),
            current_state=_state(),
            recent_summaries=[],
            director_events=events,
            user_input="我走向广场中心",
            turn_index=4,
            director_npc_store=director_store,
            npc_profile_store=npc_profile_store,
            dispatcher_gateway=dispatcher,
        )

        assert result.dispatch is not None
        projected_events = json.loads(
            dispatcher.calls[0]["input_data"]["director_events_json"]
        )
        assert [event["role"] for event in projected_events] == ["arc", "ambient"]
        candidates = json.loads(
            dispatcher.calls[0]["input_data"]["dispatched_director_npc_candidates_json"]
        )
        assert candidates[0]["selected_id"] == dispatched.draft_id
        assert candidates[0]["selected_name"] == "柯莱"
        assert candidates[0]["public_entity_profile"]["public_summary"] == "剧情中只公开表现为路过的吟游诗人。"
        assert candidates[0]["director_private_profile"]["hidden_secret"] == dispatched.hidden_secret

    def test_dispatcher_receives_absent_existing_npcs_from_npc_profiles(self):
        director_store = DirectorNPCStore(":memory:")
        for response in _draft_responses("艾琳娜", "薇拉", "洛珊"):
            director_store.save(DirectorNPCDraft.model_validate(json.loads(response)))
        npc_profile_store = NPCProfileStore(":memory:")
        npc_profile_store.upsert(
            NPCProfile(
                name="周泰",
                role="巡检司都头",
                current_intent="维护城内治安",
                attitude_to_player="公事公办",
                absent_turns=2,
            )
        )
        npc_profile_store.upsert(
            NPCProfile(
                name="裴玉奴",
                role="鸣玉坊琵琶名手",
                absent_turns=0,
            )
        )
        dispatcher = SpyGateway(_dispatch_response(action="none"))

        result = maybe_director_npc_workflow(
            core_memory=CoreMemory(items=[]),
            current_state=_state(),
            recent_summaries=[],
            director_events=[],
            user_input="我走向广场中心",
            turn_index=4,
            director_npc_store=director_store,
            npc_profile_store=npc_profile_store,
            dispatcher_gateway=dispatcher,
        )

        assert result.dispatch is not None
        candidates = json.loads(
            dispatcher.calls[0]["input_data"]["existing_npc_candidates_json"]
        )
        assert candidates == [
            {
                "name": "周泰",
                "role": "巡检司都头",
                "current_intent": "维护城内治安",
                "attitude_to_player": "公事公办",
                "absent_turns": 2,
            }
        ]

    def test_generation_does_not_create_standby_when_pool_is_empty(self):
        director_store = DirectorNPCStore(":memory:")
        director_store.set_meta_int("last_dispatch_turn", 3)
        dispatcher = MockLLM()

        result = maybe_director_npc_workflow(
            core_memory=CoreMemory(items=[]),
            current_state=_state(),
            recent_summaries=[],
            director_events=[],
            user_input="我小心前进",
            turn_index=4,
            director_npc_store=director_store,
            dispatcher_gateway=dispatcher.build_gateway(),
        )

        assert result.dispatch is None
        assert director_store.list_standby() == []

    def test_store_marks_dispatched(self):
        store = DirectorNPCStore(":memory:")
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response()))
        draft_id = store.save(draft)

        updated = store.mark_dispatched(draft_id, turn_index=9)

        assert updated is not None
        assert updated.status == "dispatched"
        assert updated.dispatched_turn == 9
        assert store.list_standby() == []
        assert store.get_meta_int("last_dispatch_turn") == 9

    def test_confirm_promotion_creates_npc_profile(self, tmp_path):
        director_db = tmp_path / "director_npcs.db"
        npc_profile_db = tmp_path / "npc_profiles.db"
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response("柯莱")))
        with DirectorNPCStore(str(director_db)) as store:
            store.save(draft)
        with NPCProfileStore(str(npc_profile_db)) as store:
            store.upsert(NPCProfile(
                name=draft.name,
                role="错误普通档案",
                appearance="未知",
                core_motivation="错误动机",
                hidden_secret="错误秘密",
                conflict_style="错误冲突方式",
                social_style="错误社交方式",
            ))

        @contextmanager
        def director_store_factory():
            store = DirectorNPCStore(str(director_db))
            try:
                yield store
            finally:
                store.close()

        @contextmanager
        def npc_profile_store_factory():
            store = NPCProfileStore(str(npc_profile_db))
            try:
                yield store
            finally:
                store.close()

        pending = type("Pending", (), {})()
        pending.director_npc_result = DirectorNPCWorkflowResult(
            dispatch=DirectorNPCDispatchDecision(
                action="dispatch_standby",
                selected_id=draft.draft_id,
                selected_name=draft.name,
                reason="test",
            )
        )

        confirmation = confirmation_helpers.promote_dispatched_director_npc_profile(
            pending,
            turn_index=9,
            stores=SimpleNamespace(
                director_npc=director_store_factory,
                npc_profile=npc_profile_store_factory,
            ),
        )
        assert confirmation is not None
        assert confirmation.draft.name == "柯莱"

        with NPCProfileStore(str(npc_profile_db)) as store:
            npc_profile = store.get_by_id(confirmation.npc_id)
        assert npc_profile is not None
        assert npc_profile.name == "柯莱"
        assert npc_profile.role == "流浪药剂师"
        assert npc_profile.public_summary == "流浪药剂师"
        assert npc_profile.appearance == draft.appearance
        assert npc_profile.core_motivation == draft.core_motivation
        assert npc_profile.hidden_secret == draft.hidden_secret
        assert npc_profile.source_director_npc_id == draft.draft_id

        with DirectorNPCStore(str(director_db)) as store:
            updated = store.get_by_id(draft.draft_id)
        assert updated is not None
        assert updated.status == "dispatched"
        assert updated.hidden_secret == draft.hidden_secret
        with DirectorNPCStore(str(director_db)) as store:
            assert store.get_meta_int("last_dispatch_turn") == 9

    def test_confirmed_director_npc_is_forced_into_state_before_update(self):
        draft = DirectorNPCDraft.model_validate(json.loads(_draft_response("柯莱")))
        state = CurrentState(
            scene=SceneState(
                scene_id="scene_009",
                location="旧桥",
                participants=["主角"],
            ),
            player=PlayerState(name="主角"),
        )

        updated = confirmation_helpers.state_with_confirmed_director_npc(state, draft)

        assert updated.scene.participant_names == ["主角", "柯莱"]
        assert updated.scene.participants[1].attitude_to_player is None
