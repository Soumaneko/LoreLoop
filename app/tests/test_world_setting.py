import json
import random
from pathlib import Path

from app.core.llm import MockLLM
from app.domain.core_memory.schemas import CoreMemory
from app.domain.director_npc.creative_brief import (
    draw_director_npc_creative_brief,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.world_setting.sampler import reserve_setting_candidates
from app.domain.world_setting.schemas import (
    SettingAnchorPacket,
    SettingSeedCandidate,
    SettingSourceDocument,
)
from app.domain.world_setting.store import WorldSettingStore
from app.domain.world_setting.workflows import (
    extract_setting_seeds,
    maintain_setting_seeds,
)


class SpyGateway:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.payload)


def test_setting_prompts_distinguish_specific_thin_seeds_from_generic_categories():
    extractor = Path(
        "app/prompts/maintenance/setting_seed_extract_v1.md"
    ).read_text(encoding="utf-8")
    maintainer = Path(
        "app/prompts/maintenance/setting_maintain_v1.md"
    ).read_text(encoding="utf-8")
    preparer = Path(
        "app/prompts/maintenance/setting_anchor_prepare_v1.md"
    ).read_text(encoding="utf-8")

    assert "信息稀薄不等于粒度宽泛" in extractor
    assert "通用建筑、商铺、设施、职业场所" in extractor
    assert "必须区分“信息薄弱”和“概念宽泛”" in maintainer
    assert "每个 group 只能是以下四种 resolution" in maintainer
    assert "必须逐字复制“已 Prepare 或已消费的权威 Settings”" in maintainer
    assert "不得填写 CoreMemory item ID" in maintainer
    assert "应使用 `reject`" in maintainer
    assert "薄弱本身不是 reject 理由" in preparer
    assert "develop 的职责正是补足特定但薄弱的概念" in preparer


def test_anchor_boundary_fields_store_fact_indexes():
    anchor = SettingAnchorPacket(
        setting_id="setting_ok",
        label="灰烬图书馆",
        kind="place",
        definition="灰烬图书馆是一处与禁书有关的遗址。",
        setting_facts=[
            "灰烬图书馆是公开目标。",
            "地下铅衬石匣仍未公开。",
        ],
        player_known_facts=[0],
        director_private_facts=[1],
    )
    assert anchor.definition == "灰烬图书馆是一处与禁书有关的遗址。"
    assert anchor.player_known_facts == [0]
    assert anchor.director_private_facts == [1]


def _add_setting(
    store: WorldSettingStore,
    *,
    label: str,
    visibility: str = "player_known",
    turn_index: int = 1,
) -> str:
    document = SettingSourceDocument(
        source_key=f"source:{turn_index}:{label}",
        source_type="scene_text",
        source_ref=f"scene:{turn_index}",
        visibility=visibility,
        content=f"众人提到了{label}。",
    )
    return store.apply_extraction(
        [
            SettingSeedCandidate(
                label=label,
                kind="historical_event",
                basic_concept=f"{label}是一场仍有影响的旧事。",
                source_key=document.source_key,
                source_span=label,
            )
        ],
        source_documents={document.source_key: document},
        turn_index=turn_index,
    )[0]


def test_store_preserves_each_extraction_as_seed_for_later_maintenance():
    store = WorldSettingStore(":memory:")
    setting_id = _add_setting(store, label="知识焚毁事件")
    private_document = SettingSourceDocument(
        source_key="npc:n1:private",
        source_type="npc_profile_private",
        source_ref="n1",
        visibility="director_private",
        content="秘密与知识焚毁事件有关。",
    )

    saved = store.apply_extraction(
        [
            SettingSeedCandidate(
                label="焚书灾难",
                kind="historical_event",
                basic_concept="这段概括不得覆盖原始概念。",
                source_key=private_document.source_key,
                source_span="知识焚毁事件",
            )
        ],
        source_documents={private_document.source_key: private_document},
        turn_index=2,
    )

    assert saved != [setting_id]
    assert len(store.list_all()) == 2
    original = store.get_by_id(setting_id)
    extracted = store.get_by_id(saved[0])
    assert original is not None
    assert extracted is not None
    assert original.basic_concept == "知识焚毁事件是一场仍有影响的旧事。"
    assert extracted.basic_concept == "这段概括不得覆盖原始概念。"
    assert extracted.visibility == "director_private"


def test_maintenance_supplements_consumed_setting_and_removes_duplicate_seeds():
    store = WorldSettingStore(":memory:")
    target_id = _add_setting(store, label="灰烬图书馆")
    anchor = SettingAnchorPacket(
        setting_id=target_id,
        label="灰烬图书馆",
        kind="historical_event",
        definition="灰烬图书馆是知识焚毁事件留下的藏书遗址。",
        setting_facts=["灰烬图书馆在知识焚毁事件中遭到破坏。"],
        player_known_facts=[0],
    )
    store.save_preparation(target_id, anchor)
    store.mark_consumed(target_id, director_npc_id="director_npc_1")
    first_seed_id = _add_setting(
        store,
        label="焚毁后的地下藏书区",
        turn_index=3,
    )
    second_seed_id = _add_setting(
        store,
        label="未登记禁书目录",
        visibility="director_private",
        turn_index=4,
    )
    gateway = MockLLM()
    gateway.set_responses(
        [
            json.dumps(
                {
                    "groups": [
                        {
                            "seed_ids": [first_seed_id, second_seed_id],
                            "resolution": "supplement",
                            "reason": "两个 seed 都是灰烬图书馆的新信息。",
                            "target_setting_id": target_id,
                            "canonical_seed_id": None,
                            "canonical_label": None,
                            "canonical_kind": None,
                            "canonical_basic_concept": None,
                            "supplemented_basic_concept": (
                                "灰烬图书馆是知识焚毁事件留下、地下仍保存"
                                "未登记禁书目录的藏书遗址。"
                            ),
                            "supplemented_definition": (
                                "灰烬图书馆是知识焚毁事件留下的藏书遗址，"
                                "其地下藏书区仍保存未登记禁书目录。"
                            ),
                            "fact_additions": [
                                {
                                    "fact": "灰烬图书馆地下仍保存未登记禁书目录。",
                                    "source_seed_ids": [
                                        first_seed_id,
                                        second_seed_id,
                                    ],
                                }
                            ],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ]
    )

    result = maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=4,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
        trace_id="maintain-consumed",
    )

    maintained = store.get_by_id(target_id)
    assert result.retained_setting_ids == [target_id]
    assert maintained is not None
    assert maintained.availability == "consumed"
    assert maintained.maturity == "usable"
    assert len(maintained.sources) == 3
    assert maintained.prepared_anchor is not None
    assert maintained.prepared_anchor.definition.endswith(
        "其地下藏书区仍保存未登记禁书目录。"
    )
    assert maintained.prepared_anchor.setting_facts[-1] == (
        "灰烬图书馆地下仍保存未登记禁书目录。"
    )
    assert maintained.prepared_anchor.setting_facts.index(
        "灰烬图书馆地下仍保存未登记禁书目录。"
    ) in maintained.prepared_anchor.player_known_facts
    assert maintained.prepared_anchor.setting_facts.index(
        "灰烬图书馆地下仍保存未登记禁书目录。"
    ) in maintained.prepared_anchor.director_private_facts
    assert store.get_by_id(first_seed_id) is None
    assert store.get_by_id(second_seed_id) is None


def test_store_supplement_ignores_blank_fact_additions():
    store = WorldSettingStore(":memory:")
    target_id = _add_setting(store, label="灰烬图书馆")
    anchor = SettingAnchorPacket(
        setting_id=target_id,
        label="灰烬图书馆",
        kind="historical_event",
        definition="灰烬图书馆是知识焚毁事件留下的藏书遗址。",
        setting_facts=["灰烬图书馆在知识焚毁事件中遭到破坏。"],
        player_known_facts=[0],
    )
    store.save_preparation(target_id, anchor)

    store._apply_supplement(
        target_id=target_id,
        basic_concept="灰烬图书馆是一处藏书遗址。",
        definition=anchor.definition,
        fact_additions=[("   ", {"player_known"})],
    )

    maintained = store.get_by_id(target_id)
    assert maintained is not None
    assert maintained.prepared_anchor is not None
    assert maintained.prepared_anchor.setting_facts == anchor.setting_facts
    assert "" not in maintained.prepared_anchor.setting_facts


def test_maintenance_merges_duplicate_seeds_into_one_independent_seed():
    store = WorldSettingStore(":memory:")
    first_id = _add_setting(store, label="灰烬藏书馆", turn_index=2)
    second_id = _add_setting(store, label="焚毁后的藏书遗址", turn_index=3)
    gateway = MockLLM()
    gateway.set_responses(
        [
            json.dumps(
                {
                    "groups": [
                        {
                            "seed_ids": [first_id, second_id],
                            "resolution": "independent",
                            "reason": "两个 seed 指向同一处藏书遗址。",
                            "target_setting_id": None,
                            "canonical_seed_id": first_id,
                            "canonical_label": "灰烬图书馆",
                            "canonical_kind": "historical_event",
                            "canonical_basic_concept": (
                                "灰烬图书馆是一处仍有影响的焚毁藏书遗址。"
                            ),
                            "supplemented_basic_concept": None,
                            "supplemented_definition": None,
                            "fact_additions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ]
    )

    maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=3,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
    )
    maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=3,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
    )

    settings = store.list_all()
    assert gateway.provider._index == 1
    assert len(settings) == 1
    assert settings[0].setting_id == first_id
    assert settings[0].label == "灰烬图书馆"
    assert len(settings[0].sources) == 2


def test_store_absorb_removes_relations_to_every_absorbed_setting():
    store = WorldSettingStore(":memory:")
    target_id = _add_setting(store, label="灰烬图书馆", turn_index=1)
    first_absorbed_id = _add_setting(
        store,
        label="灰烬藏书馆",
        turn_index=2,
    )
    second_absorbed_id = _add_setting(
        store,
        label="焚毁后的藏书遗址",
        turn_index=3,
    )
    store._conn.execute(
        """
        UPDATE world_settings
        SET related_setting_ids_json = ?
        WHERE setting_id = ?
        """,
        (
            json.dumps([second_absorbed_id]),
            first_absorbed_id,
        ),
    )
    store._conn.commit()

    store._absorb_settings(
        target_id=target_id,
        absorbed_ids=[first_absorbed_id, second_absorbed_id],
    )

    maintained = store.get_by_id(target_id)
    assert maintained is not None
    assert maintained.related_setting_ids == []
    assert store.get_by_id(first_absorbed_id) is None
    assert store.get_by_id(second_absorbed_id) is None


def test_maintenance_contained_seed_only_attaches_provenance():
    store = WorldSettingStore(":memory:")
    target_id = _add_setting(store, label="禁书管制令")
    anchor = SettingAnchorPacket(
        setting_id=target_id,
        label="禁书管制令",
        kind="historical_event",
        definition="禁书管制令是限制违禁知识持有和流通的制度。",
        setting_facts=["禁书管制令限制违禁知识的持有和流通。"],
        player_known_facts=[0],
    )
    store.save_preparation(target_id, anchor)
    duplicate_id = _add_setting(
        store,
        label="违禁知识持有限制",
        turn_index=5,
    )
    gateway = MockLLM()
    gateway.set_responses(
        [
            json.dumps(
                {
                    "groups": [
                        {
                            "seed_ids": [duplicate_id],
                            "resolution": "contained",
                            "reason": "成熟设定已经完整包含该限制。",
                            "target_setting_id": target_id,
                            "canonical_seed_id": None,
                            "canonical_label": None,
                            "canonical_kind": None,
                            "canonical_basic_concept": None,
                            "supplemented_basic_concept": None,
                            "supplemented_definition": None,
                            "fact_additions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ]
    )

    maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=5,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
    )

    maintained = store.get_by_id(target_id)
    assert maintained is not None
    assert len(maintained.sources) == 2
    assert maintained.prepared_anchor == anchor
    assert store.get_by_id(duplicate_id) is None


def test_maintenance_reject_marks_seed_without_deleting_provenance():
    store = WorldSettingStore(":memory:")
    seed_id = _add_setting(store, label="普通背景设施", turn_index=5)
    gateway = MockLLM()
    gateway.set_responses(
        [
            json.dumps(
                {
                    "groups": [
                        {
                            "seed_ids": [seed_id],
                            "resolution": "reject",
                            "reason": "仅是场景中的通用类别，没有独立世界身份。",
                            "target_setting_id": None,
                            "canonical_seed_id": None,
                            "canonical_label": None,
                            "canonical_kind": None,
                            "canonical_basic_concept": None,
                            "supplemented_basic_concept": None,
                            "supplemented_definition": None,
                            "fact_additions": [],
                        }
                    ]
                },
                ensure_ascii=False,
            )
        ]
    )

    result = maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=5,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
    )

    rejected = store.get_by_id(seed_id)
    assert result.retained_setting_ids == []
    assert rejected is not None
    assert rejected.availability == "rejected"
    assert rejected.rejection_reason == "仅是场景中的通用类别，没有独立世界身份。"
    assert len(rejected.sources) == 1


def test_maintenance_retries_independent_group_that_matches_mature_label():
    store = WorldSettingStore(":memory:")
    target_id = _add_setting(store, label="禁书管制令")
    anchor = SettingAnchorPacket(
        setting_id=target_id,
        label="禁书管制令",
        kind="historical_event",
        definition="禁书管制令限制违禁知识的持有和流通。",
        setting_facts=["禁书管制令限制违禁知识的持有和流通。"],
        player_known_facts=[0],
    )
    store.save_preparation(target_id, anchor)
    duplicate_id = _add_setting(
        store,
        label="禁书管制令",
        turn_index=6,
    )
    invalid = {
        "groups": [
            {
                "seed_ids": [duplicate_id],
                "resolution": "independent",
                "reason": "错误地当成独立设定。",
                "target_setting_id": None,
                "canonical_seed_id": duplicate_id,
                "canonical_label": "禁书管制令",
                "canonical_kind": "historical_event",
                "canonical_basic_concept": "禁书管制令是一项旧制度。",
                "supplemented_basic_concept": None,
                "supplemented_definition": None,
                "fact_additions": [],
            }
        ]
    }
    valid = {
        "groups": [
            {
                "seed_ids": [duplicate_id],
                "resolution": "contained",
                "reason": "成熟设定已经包含该 seed。",
                "target_setting_id": target_id,
                "canonical_seed_id": None,
                "canonical_label": None,
                "canonical_kind": None,
                "canonical_basic_concept": None,
                "supplemented_basic_concept": None,
                "supplemented_definition": None,
                "fact_additions": [],
            }
        ]
    }
    gateway = MockLLM()
    gateway.set_responses(
        [
            json.dumps(invalid, ensure_ascii=False),
            json.dumps(valid, ensure_ascii=False),
        ]
    )

    maintain_setting_seeds(
        core_memory=CoreMemory(items=[]),
        turn_index=6,
        world_setting_store=store,
        gateway=gateway.build_gateway(),
    )

    assert gateway.provider._index == 2
    assert store.get_by_id(duplicate_id) is None
    assert len(store.get_by_id(target_id).sources) == 2


def test_seed_workflow_batches_scene_and_new_profile_private_sources():
    setting_store = WorldSettingStore(":memory:")
    npc_store = NPCProfileStore(":memory:")
    npc_id = npc_store.upsert(
        NPCProfile(
            name="希尔达",
            public_summary="藏书修复师。",
            core_motivation="找回被毁掉的家族历史。",
            hidden_secret="她的家族秘密与知识焚毁事件有关。",
            conflict_style="谨慎周旋。",
            social_style="冷淡交易。",
        )
    )
    gateway = SpyGateway(
        {
            "candidates": [
                {
                    "label": "灰烬图书馆",
                    "kind": "place",
                    "basic_concept": "正文中出现的一处图书馆遗址。",
                    "source_key": "scene:7",
                    "source_span": "灰烬图书馆",
                },
                {
                    "label": "知识焚毁事件",
                    "kind": "historical_event",
                    "basic_concept": "与希尔达家族秘密有关的知识毁灭事件。",
                    "source_key": f"npc:{npc_id}:private",
                    "source_span": "知识焚毁事件",
                },
            ]
        }
    )

    result = extract_setting_seeds(
        core_memory=CoreMemory(items=[]),
        scene_text="希尔达站在灰烬图书馆的断墙前。",
        scene_facts_json=json.dumps(
            {"scene_focus": "抵达遗址", "facts": []},
            ensure_ascii=False,
        ),
        turn_index=7,
        new_npc_profile_ids=[npc_id],
        world_setting_store=setting_store,
        npc_profile_store=npc_store,
        gateway=gateway,
        trace_id="trace-setting",
    )

    settings = {setting.label: setting for setting in setting_store.list_all()}
    assert len(result.saved_setting_ids) == 2
    assert settings["灰烬图书馆"].visibility == "player_known"
    assert settings["知识焚毁事件"].visibility == "director_private"
    source_documents = json.loads(
        gateway.calls[0]["input_data"]["source_documents_json"]
    )
    output_schema = json.loads(
        gateway.calls[0]["input_data"]["output_schema_json"]
    )
    assert {document["source_key"] for document in source_documents} >= {
        "scene:7",
        f"npc:{npc_id}:private",
    }
    assert "existing_settings_json" not in gateway.calls[0]["input_data"]
    candidate_schema = output_schema["$defs"]["SettingSeedCandidate"]
    assert set(candidate_schema["properties"]) == {
        "label",
        "kind",
        "basic_concept",
        "source_key",
        "source_span",
    }


def test_seed_workflow_discards_candidate_without_literal_source_support():
    setting_store = WorldSettingStore(":memory:")
    npc_store = NPCProfileStore(":memory:")
    gateway = SpyGateway(
        {
            "candidates": [
                {
                    "label": "不存在的组织",
                    "kind": "organization",
                    "basic_concept": "模型自行补写的组织。",
                    "source_key": "scene:2",
                    "source_span": "正文里并没有这句话",
                }
            ]
        }
    )

    result = extract_setting_seeds(
        core_memory=CoreMemory(items=[]),
        scene_text="主角走过空旷广场。",
        scene_facts_json="{}",
        turn_index=2,
        new_npc_profile_ids=[],
        world_setting_store=setting_store,
        npc_profile_store=npc_store,
        gateway=gateway,
    )

    assert result.saved_setting_ids == []
    assert result.ignored_candidate_count == 1
    assert setting_store.list_all() == []


def test_random_reservation_is_persisted_and_idempotent_for_owner():
    store = WorldSettingStore(":memory:")
    for label in ("旧港", "盐税制度", "北境迁徙"):
        _add_setting(store, label=label)

    first = reserve_setting_candidates(
        store,
        owner="operation-1",
        count=2,
        rng=random.Random(9),
    )
    resumed = reserve_setting_candidates(
        store,
        owner="operation-1",
        count=2,
        rng=random.Random(1),
    )

    assert [item.setting_id for item in resumed] == [
        item.setting_id for item in first
    ]
    assert all(item.availability == "reserved" for item in resumed)


def test_creative_brief_is_programmatically_drawn_from_setting_kind():
    first = draw_director_npc_creative_brief(
        "organization",
        rng=random.Random(12),
    )
    second = draw_director_npc_creative_brief(
        "organization",
        rng=random.Random(12),
    )

    assert first == second
    assert first.setting_relation
    assert first.social_position
    assert first.life_phase
    assert first.desire_pattern
    assert first.optional_dimension
    desire_group, _, desire_value = first.desire_pattern.partition("：")
    optional_group, _, optional_value = first.optional_dimension.partition("：")
    assert desire_group in {"DESIRE", "FEAR", "SECRET"}
    assert desire_value
    assert optional_group in {"生计机制", "活动范围", "叙事压力"}
    assert optional_value
