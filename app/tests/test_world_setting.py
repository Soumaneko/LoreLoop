import json
import random

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
from app.domain.world_setting.workflows import extract_setting_seeds


class SpyGateway:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.payload)


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
        source_key=f"source:{label}",
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
                resolution="new",
            )
        ],
        source_documents={document.source_key: document},
        turn_index=turn_index,
    )[0]


def test_store_merges_exact_identity_without_overwriting_basic_concept():
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
                resolution="existing",
                matched_setting_id=setting_id,
            )
        ],
        source_documents={private_document.source_key: private_document},
        turn_index=2,
    )

    setting = store.get_by_id(setting_id)
    assert saved == [setting_id]
    assert setting is not None
    assert setting.label == "知识焚毁事件"
    assert setting.basic_concept == "知识焚毁事件是一场仍有影响的旧事。"
    assert setting.visibility == "mixed"
    assert len(setting.sources) == 2


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
                    "resolution": "new",
                    "matched_setting_id": None,
                    "related_setting_ids": [],
                },
                {
                    "label": "知识焚毁事件",
                    "kind": "historical_event",
                    "basic_concept": "与希尔达家族秘密有关的知识毁灭事件。",
                    "source_key": f"npc:{npc_id}:private",
                    "source_span": "知识焚毁事件",
                    "resolution": "new",
                    "matched_setting_id": None,
                    "related_setting_ids": [],
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
    assert {document["source_key"] for document in source_documents} >= {
        "scene:7",
        f"npc:{npc_id}:private",
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
                    "resolution": "new",
                    "matched_setting_id": None,
                    "related_setting_ids": [],
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
