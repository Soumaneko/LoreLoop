import pytest

import json
from types import SimpleNamespace
from pydantic import ValidationError
from app.domain.core_memory import CoreMemory
from app.domain.current_state import WorldClock
from app.domain.npc_profile.schemas import NPCExtractionResult
from app.domain.npc_profile.schemas import NPCMention
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.director.confirmation import run_confirmation_director_agent
from app.graph import confirmation_nodes
from app.runtime.paths import CampaignPaths
from app.runtime.stores import StoreBundle
from app.tests.factories.director import ScriptedDirectorGateway
from app.tests.factories.director import make_current_state
from app.tests.factories.director import make_director_npc


def test_confirmation_agent_observation_prepends_current_turn_gen_entry(
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.director_log() as log_store:
        log_store.save_entries([{
            "turn_index": 1,
            "phase": "confirmation",
            "reasoning": "上一回合确认决策。",
            "measures": "补全NPC「顾晚棠」",
        }])
    gen_entry = {
        "turn_index": 3,
        "phase": "generation",
        "reasoning": "本回合引入林澈。",
        "measures": "引入NPC「林澈」(face_to_face)",
    }
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
        director_log_entry=gen_entry,
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    log_payload = json.loads(
        gateway.input_data[0]["recent_director_log_json"]
    )
    assert log_payload[0] == gen_entry
    assert log_payload[1]["turn_index"] == 1


def test_confirmation_agent_rejects_npc_creation_without_setting(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "错误地在没有任何 Setting 依托的情况下创建人物。",
            "requests": [
                {
                    "request_id": "create_1",
                    "skill": "create_npc",
                    "arguments": {},
                    "guidance": "准备一个长期社区人物。",
                }
            ],
        },
        npc_output={
            "creation_reason": "社区日常需要一个可复用医生。",
            "core_motivation": "保护社区居民。",
            "hidden_secret": "曾目击一宗未结案件。",
            "conflict_style": "先协商后行动。",
            "social_style": "克制而友善。",
            "role": "社区医生",
            "faction": None,
            "personality_traits": ["谨慎", "耐心"],
            "known_abilities": ["急救"],
            "appearance": "常穿深色风衣。",
            "name": "林澈",
            "encounter_potentials": ["诊所求助", "街区冲突", "旧案线索"],
        },
    )

    with pytest.raises(ValidationError, match="setting_requirements"):
        run_confirmation_director_agent(
            gateway=gateway,
            stores=stores,
            core_memory=CoreMemory(items=[]),
            current_state=make_current_state(),
            user_input="询问昨夜发生的事。",
            scene_facts={"scene_focus": "主角走访社区。", "facts": []},
            recent_summaries=[],
            selection_summaries=[],
            turn_index=3,
            trace_id="confirmation-director",
        )

    with stores.director_npc() as store:
        assert store.list_prepared() == []
    with stores.world_setting() as store:
        assert store.list_all() == []


def test_confirmation_agent_passes_existing_setting_to_npc_creator(tmp_path):
    from app.domain.world_setting import WorldSetting

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    setting = WorldSetting(
        kind="location",
        name="盐雾港",
        summary="依靠潮汐盐田与近海货运生存的港镇。",
        public_facts=["港民熟悉潮汐航道"],
        hidden_facts=["旧盐仓下有走私暗渠"],
        created_turn=1,
    )
    unrelated = setting.model_copy(update={
        "setting_id": "setting_unrelated",
        "name": "山邮驿",
        "summary": "负责内陆山路信件转运的驿站。",
    })
    with stores.world_setting() as store:
        store.save(setting)
        store.save(unrelated)
    gateway = ScriptedDirectorGateway(
        decision={
            "reasoning": "港镇需要一名长期人物。",
            "requests": [{
                "request_id": "npc_1",
                "skill": "create_npc",
                "arguments": {
                    "setting_requirements": [{
                        "kind": "location",
                        "setting_description": "人物以本地航运为生。",
                        "matched_setting_id": setting.setting_id,
                    }],
                },
                "guidance": "创建一名以本地航运为生的人物。",
            }],
        },
        npc_output={
            "creation_reason": "港镇日常需要熟悉潮路的船工。",
            "core_motivation": "保住家族赖以生存的小船。",
            "hidden_secret": "偶然发现旧盐仓暗渠仍有人使用。",
            "conflict_style": "先避让，退无可退时正面交涉。",
            "social_style": "寡言但会直接指出航路风险。",
            "role": "潮路船工",
            "faction": None,
            "personality_traits": ["谨慎", "务实"],
            "known_abilities": ["辨潮", "操舟"],
            "appearance": "皮肤被海风晒得黝黑。",
            "name": "周汐",
            "encounter_potentials": ["雇船", "避风", "港口纠纷"],
        },
    )

    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    npc_input = gateway.input_data[gateway.tasks.index("director_npc_create")]
    assert setting.name in npc_input["settings_json"]
    assert setting.hidden_facts[0] in npc_input["settings_json"]
    assert unrelated.name not in npc_input["settings_json"]
    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    observed_settings = observation["resources"]["settings"]
    assert {
        item["setting_id"] for item in observed_settings
    } == {setting.setting_id, unrelated.setting_id}
    assert all("public_facts" not in item for item in observed_settings)
    assert all("hidden_facts" not in item for item in observed_settings)
    with stores.director_npc() as store:
        [saved] = store.list_prepared()
    assert saved.source_setting_ids == [setting.setting_id]


def test_confirmation_agent_observes_only_prepared_director_npcs(tmp_path):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    prepared = make_director_npc().model_copy(
        update={"draft_id": "director_npc_conf_prepared"}
    )
    introduced = make_director_npc().model_copy(
        update={
            "draft_id": "director_npc_conf_introduced",
            "name": "顾晚棠",
            "status": "introduced",
            "introduced_turn": 2,
        }
    )
    with stores.director_npc() as store:
        store.save(prepared)
        store.save(introduced)
    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []},
    )

    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={"scene_focus": "主角走访社区。", "facts": []},
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    observed_drafts = [
        item
        for item in observation["resources"]["drafts"]
        if item.get("source") == "director_npc"
    ]
    assert [item["npc_id"] for item in observed_drafts] == [
        "director_npc_conf_prepared"
    ]
    assert "director_npc_conf_introduced" not in {
        item["npc_id"] for item in observation["resources"]["drafts"]
    }


def test_npc_update_node_keeps_new_profiles_temporary_when_director_runs(
    monkeypatch,
):
    from app.domain.npc_profile.store import NPCProfileStore

    class _Gateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["output_schema"].model_validate(
                {
                    "results": [
                        {
                            "candidate_index": 0,
                            "resolution": "new",
                            "matched_npc_index": None,
                            "persist_alias": False,
                            "turn_observation": None,
                            "profile_patch": None,
                            "new_profile": {
                                "name": "练气巅峰采药女",
                                "aliases": [],
                                "public_summary": "山间采药为生的练气期女子。",
                                "role": "采药女",
                                "appearance": "背一只竹篓，裙角沾着泥。",
                                "personality_traits": ["谨慎", "坚韧"],
                                "known_abilities": ["采药", "辨识药草"],
                                "faction": None,
                                "description": None,
                                "current_location": "药山",
                                "attitude_to_player": None,
                                "current_intent": None,
                            },
                        }
                    ]
                }
            )

    gateway = _Gateway()
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: gateway)
    monkeypatch.setattr(
        confirmation_nodes,
        "_store_factory",
        lambda _state, name: lambda: NPCProfileStore(":memory:"),
    )
    state = {
        "write_db": True,
        "npc_extraction": NPCExtractionResult(
            mentions=[
                NPCMention(
                    name="练气巅峰采药女",
                    context_span="药山上有个练气巅峰的采药女。",
                )
            ]
        ),
        "pipeline_input": SimpleNamespace(
            turn_index=3,
            run_director_agent=True,
        ),
        "trace_id": "trace-npc",
        "cache": {},
        "scene_facts_json": '{"scene_focus": "药山。", "facts": []}',
        "updated_state": make_current_state(),
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "recent_summaries": [],
    }

    result = confirmation_nodes.npc_profile_update_node(state)

    assert result["updated_npc_profile_ids"] == []
    assert len(result["new_npc_profiles"]) == 1
    assert result["new_npc_profiles"][0].name == "练气巅峰采药女"
    assert state["cache"]["new_npc_profiles"][0].name == "练气巅峰采药女"


def test_director_confirm_node_receives_stashed_temporary_profiles(
    monkeypatch,
    tmp_path,
):
    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    new_profile = NPCProfile(
        name="练气巅峰采药女",
        role="采药女",
        public_summary="山间采药为生的练气期女子。",
    )
    captured = {}

    class _FakeExecutor:
        def __init__(self, **kwargs):
            self._kwargs = kwargs

        def run(self):
            captured["new_npc_profiles"] = self._kwargs.get("new_npc_profiles")
            return object(), object()

    monkeypatch.setattr(
        confirmation_nodes,
        "DirectorConfirmationExecutor",
        _FakeExecutor,
    )
    monkeypatch.setattr(
        confirmation_nodes,
        "_gateway_for",
        lambda _state: object(),
    )
    state = {
        "write_db": True,
        "pipeline_input": SimpleNamespace(
            run_director_agent=True,
            user_input="我在药山。",
            turn_index=3,
        ),
        "cache": {},
        "trace_id": "trace-director",
        "runtime_services": SimpleNamespace(stores=stores),
        "core_memory": CoreMemory(items=[]),
        "updated_state": make_current_state(),
        "scene_facts": SceneFactSet(scene_focus="药山。"),
        "effective_scene_text": "药山上的采药女在采摘药草。",
        "recent_summaries": [],
        "selection_summaries": [],
        "npc_extraction": None,
        "existing_npc_profiles": [],
        "new_npc_profiles": [new_profile],
        "node_errors": [],
        "events": [],
    }

    confirmation_nodes.director_agent_confirm_node(state)

    assert captured["new_npc_profiles"] == [new_profile]


def test_confirmation_observation_two_tier_npcs(tmp_path):
    from app.domain.current_state import WorldClock
    from app.domain.director import DirectorResourceRef
    from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent

    stores = StoreBundle(CampaignPaths.from_data_dir(tmp_path))
    with stores.npc_profile() as store:
        fact_hit_id = store.upsert(NPCProfile(
            name="顾晚棠",
            role="阁主",
            core_motivation="查明失窃真相。",
            hidden_secret="暗中资助商会。",
        ))
        event_actor_id = store.upsert(NPCProfile(
            name="柴行简",
            role="商队执事",
            core_motivation="保住商路。",
            hidden_secret="与边境私军有染。",
        ))
        unrelated_id = store.upsert(NPCProfile(
            name="无关路人",
            role="路人",
            hidden_secret="没有秘密。",
        ))
    prepared_draft = make_director_npc().model_copy(
        update={
            "draft_id": "director_npc_conf_prepared_2tier",
            "name": "林澈",
        }
    )
    with stores.director_npc() as store:
        store.save(prepared_draft)
    event = DirectorEvent(
        role="ambient",
        title="旧桥封锁",
        subject="河务署",
        intent="控制渡河通道",
        method="封锁旧桥",
        macro_intent="改变商路格局",
        deadline_clock=WorldClock().advance_phase(3),
        created_turn=1,
        updated_turn=1,
        actors=[
            DirectorActorRef(
                name="柴行简",
                ref=DirectorResourceRef(kind="npc", resource_id=event_actor_id),
            ),
        ],
    )
    with stores.director_event() as store:
        store.save(event)

    gateway = ScriptedDirectorGateway(
        decision={"reasoning": "本轮无需导演介入。", "requests": []}
    )
    run_confirmation_director_agent(
        gateway=gateway,
        stores=stores,
        core_memory=CoreMemory(items=[]),
        current_state=make_current_state(),
        user_input="询问昨夜发生的事。",
        scene_facts={
            "scene_focus": "顾晚棠在客栈露面。",
            "facts": [{"fact_id": "f1", "fact": "顾晚棠向主角提及商路失窃。"}],
        },
        recent_summaries=[],
        selection_summaries=[],
        turn_index=3,
        trace_id="confirmation-director",
    )

    observation = json.loads(
        gateway.input_data[0]["director_observation_json"]
    )
    resources = observation["resources"]
    index = {
        item["npc_id"]: item
        for item in resources["npcs"]
        if item.get("source") == "npc_profile"
    }
    assert fact_hit_id in index
    assert event_actor_id in index
    assert unrelated_id in index
    assert "hidden_secret" not in index[fact_hit_id]
    assert "core_motivation" not in index[fact_hit_id]
    assert "director_npc" not in {
        item.get("source") for item in resources["npcs"]
    }

    drafts_by_id = {
        item["npc_id"]: item
        for item in resources["drafts"]
        if item.get("source") == "director_npc"
    }
    assert "director_npc_conf_prepared_2tier" in drafts_by_id
    assert drafts_by_id[
        "director_npc_conf_prepared_2tier"
    ]["hidden_secret"]

    detail_ids = {
        item["npc_id"] for item in resources["npc_details"]
    }
    assert fact_hit_id in detail_ids
    assert event_actor_id in detail_ids
    assert unrelated_id not in detail_ids
    details_by_id = {
        item["npc_id"]: item for item in resources["npc_details"]
    }
    assert details_by_id[fact_hit_id]["hidden_secret"] == "暗中资助商会。"
