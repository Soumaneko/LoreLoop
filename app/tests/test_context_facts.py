import json
from pathlib import Path

import pytest

from app.domain.action.schemas import Anchor
from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.continuity import ContinuitySelection, ContinuityStore
from app.domain.context_facts import (
    ContextExtractor,
    ContextExtractorInput,
    ContextFactsInput,
    ContextFactsService,
    ContextHistoryEvidenceSet,
    ContextHistoryHit,
    ContextHistoryRetriever,
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
    SettingMatch,
    SettingRetriever,
    SettingSelection,
    build_context_search_plan,
)
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import project_context_facts, project_settings
from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.current_state import (
    CurrentState,
    InventoryItem,
    PlayerState,
    ResourcesState,
    SceneState,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.short_term_memory import ShortTermScene
from app.domain.world_setting import WorldSetting, WorldSettingStore
from app.graph.generation_profile_helpers import select_context_profiles


def _state() -> CurrentState:
    return CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="客栈上房",
            participants=["萧云痕", "顾晚棠"],
        ),
        player=PlayerState(name="萧云痕"),
        resources=ResourcesState(inventory=[InventoryItem(name="麂皮褡裢")]),
    )


def _core_memory() -> CoreMemory:
    return CoreMemory(items=[
        CoreMemoryItem(
            id="core_world",
            kind="world_invariant",
            strength="hard_constraint",
            source="player",
            content="当前世界为武侠原创世界。",
        )
    ])


def _npc_view():
    return NPCProfile(
        name="顾晚棠",
        aliases=["顾阁主"],
        role="万象阁阁主",
        attitude_to_player="部分信任但仍戒备",
    ).director_view()


def _upsert_scenes(
    store: ConfirmedHistoryStore,
    scenes: list[ShortTermScene],
) -> None:
    for scene in scenes:
        store.upsert_turn(
            scene_id=scene.scene_id,
            turn_index=scene.turn_index,
            user_input=scene.user_input,
            scene_text=scene.scene_text,
        )


def test_context_fact_set_assigns_fact_ids_and_keeps_internal_evidence():
    result = ContextFactSet.from_drafts(
        turn_focus="萧云痕询问顾晚棠如何应对盐帮。",
        facts=[
            {
                "holder": "萧云痕",
                "source": "顾晚棠告知",
                "fact": "顾晚棠因账册摹本被盐帮追杀。",
                "evidence_ids": ["turn_2_p003"],
            },
            {
                "holder": "萧云痕、顾晚棠",
                "source": "共同经历",
                "fact": "萧云痕已承诺庇护顾晚棠三日。",
                "evidence_ids": [],
            },
        ],
        known_evidence_ids={"turn_2_p003"},
    )

    assert [fact.fact_id for fact in result.facts] == ["f1", "f2"]
    assert result.facts[0].evidence_ids == ["turn_2_p003"]
    assert [fact.fact for fact in result.facts] == [
        "萧云痕已知(顾晚棠告知): 顾晚棠因账册摹本被盐帮追杀。",
        "萧云痕、顾晚棠已知(共同经历): 萧云痕已承诺庇护顾晚棠三日。",
    ]


def test_context_fact_set_rejects_duplicate_ids_and_unknown_evidence():
    with pytest.raises(ValueError, match="fact_id"):
        ContextFactSet(
            turn_focus="测试",
            facts=[
                {"fact_id": "f1", "fact": "事实一。"},
                {"fact_id": "f1", "fact": "事实二。"},
            ],
        )

    with pytest.raises(ValueError, match="unknown context history evidence"):
        ContextFactSet.from_drafts(
            turn_focus="测试",
            facts=[{
                "holder": "创作者",
                "source": "既有剧情",
                "fact": "一项事实。",
                "evidence_ids": ["invented"],
            }],
            known_evidence_ids=set(),
        )


def test_deterministic_intents_use_location_participants_aliases_and_topics():
    plan = build_context_search_plan(
        user_input="我告诉顾阁主盐运帮的动向，再问她解决方法。",
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[_npc_view()],
    )

    assert any(
        intent.focus == "location"
        and intent.entities[0].name == "客栈上房"
        for intent in plan.intents
    )
    assert any(
        intent.focus == "entity"
        and intent.entities[0].name == "顾晚棠"
        and "顾阁主" in intent.entities[0].aliases
        for intent in plan.intents
    )
    relationship = next(
        intent for intent in plan.intents if intent.focus == "relationship"
    )
    assert {entity.name for entity in relationship.entities} >= {
        "萧云痕",
        "顾晚棠",
    }
    topic = next(intent for intent in plan.intents if intent.focus == "topic")
    assert "解决方法" in topic.topics


def test_deterministic_intents_keep_all_matched_anchors():
    anchors = [
        Anchor(
            text=f"目标{i}",
            normalized=f"目标{i}",
            kind="concept",
            source="explicit",
            confidence=0.9,
        )
        for i in range(18)
    ]

    plan = build_context_search_plan(
        user_input="我继续等待。",
        current_state=_state(),
        matched_anchors=anchors,
        npc_profiles=[],
    )

    entity_names = {
        intent.entities[0].name
        for intent in plan.intents
        if intent.focus == "entity"
    }
    assert {f"目标{i}" for i in range(18)} <= entity_names
    assert len(plan.intents) > 16


def test_history_retriever_allows_single_alias_and_diversifies_turns():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[
                ContextSearchEntity(name="顾晚棠", aliases=["顾阁主"])
            ],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["账册摹本"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                user_input="询问账册摹本。",
                scene_text=(
                    "顾阁主承认账册摹本并不在她身上。\n\n"
                    "她拒绝透露具体藏匿地点。"
                ),
            ),
            ShortTermScene(
                scene_id="scene_004",
                turn_index=4,
                scene_text="顾晚棠提醒萧云痕，盐帮正在客栈外搜查。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=5,
        )

    assert result.hits
    assert {hit.turn_index for hit in result.hits} >= {1, 4}
    assert any("顾晚棠" in hit.matched_entities for hit in result.hits)
    assert any("账册摹本" in hit.matched_topics for hit in result.hits)
    assert all(hit.matched_intent_ids for hit in result.hits)


def test_history_retriever_does_not_cap_evidence_at_fact_limit():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠")],
        )
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn_index:03d}",
                turn_index=turn_index,
                scene_text=f"顾晚棠在第{turn_index}回合留下了一条线索。",
            )
            for turn_index in range(1, 16)
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=16,
        )

    assert len(result.hits) == 15


def test_context_extractor_receives_raw_history_and_cites_it():
    class SpyGateway:
        def __init__(self):
            self.input_data = None

        def run(self, **kwargs):
            self.input_data = kwargs["input_data"]
            return kwargs["output_schema"].model_validate({
                "turn_focus": "萧云痕询问顾晚棠如何应对盐帮。",
                "facts": [{
                    "holder": "萧云痕",
                    "source": "顾晚棠告知",
                    "fact": "顾晚棠曾透露账册摹本不在身上。",
                    "evidence_ids": ["turn_2_p003"],
                }],
            })

    history = ContextHistoryEvidenceSet(hits=[
        ContextHistoryHit(
            evidence_id="turn_2_p003",
            source_type="scene_paragraph",
            turn_index=2,
            scene_id="scene_002",
            paragraph_index=3,
            text="顾晚棠说明账册摹本不在她身上。",
            matched_intent_ids=["i1"],
            matched_entities=["顾晚棠"],
            score=0.9,
        )
    ], indexed_documents=3, indexed_turns=1)
    gateway = SpyGateway()
    result = ContextExtractor(gateway).extract(
        ContextExtractorInput(
            user_input="我问顾晚棠有没有解决方法。",
            core_memory=_core_memory(),
            current_state=_state(),
            scene_summaries=[
                SceneSummary(
                    level=1,
                    content="顾晚棠曾透露账册摹本不在身上。",
                    turn_start=1,
                    turn_end=2,
                )
            ],
                history_evidence=history,
                continuity=ContinuitySelection(),
            settings=SettingSelection(settings=[
                SettingMatch(
                    setting_id="setting_001",
                    kind="faction",
                    name="盐帮",
                    summary="掌控漕运与走私路线的江湖帮派。",
                    public_facts=["盐帮垄断盐路走私。"],
                    matched_entities=["顾晚棠"],
                    score=0.5,
                )
            ]),
            npc_profiles=[_npc_view()],
        )
    )

    assert result.facts[0].evidence_ids == ["turn_2_p003"]
    assert "turn_2_p003" in gateway.input_data["confirmed_history_json"]
    assert "context_search_plan_json" not in gateway.input_data
    assert "scene_001" in gateway.input_data["current_state_json"]
    assert "director_event_json" not in gateway.input_data
    assert "盐帮" in gateway.input_data["settings_json"]
    assert "垄断盐路走私" in gateway.input_data["settings_json"]
    schema = json.loads(gateway.input_data["output_schema_json"])
    draft = next(
        definition
        for name, definition in schema["$defs"].items()
        if name.endswith("ContextFactDraft")
    )
    assert set(draft["properties"]) == {
        "holder",
        "source",
        "fact",
        "evidence_ids",
    }
    rendered_prompt = Path(
        "app/prompts/generation/context_extract_v1.md"
    ).read_text(encoding="utf-8").format(**gateway.input_data)
    assert "已确认历史正文" in rendered_prompt
    assert "活跃连续性事项" in rendered_prompt
    assert "世界设定" in rendered_prompt
    assert "Retrieved Memories" not in rendered_prompt


def test_context_facts_service_retrieves_persistent_history_and_calls_one_llm():
    class SpyGateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["output_schema"].model_validate({
                "turn_focus": "追问盐帮动向。",
                "facts": [],
            })

    gateway = SpyGateway()
    with (
        ConfirmedHistoryStore() as store,
        ContinuityStore() as continuity_store,
        WorldSettingStore() as setting_store,
    ):
        store.upsert_turn(
            scene_id="scene_003",
            turn_index=3,
            user_input="",
            scene_text="顾阁主说盐帮正在客栈外搜查。",
        )
        setting_store.save(WorldSetting(
            kind="location",
            name="客栈上房",
            summary="客栈二层临街的上房，面向盐帮搜查的方向。",
            public_facts=["盐帮暗哨曾在楼下盯防。"],
            hidden_facts=["房梁暗格里藏着盐路账册摹本。"],
            created_turn=1,
        ))
        result = ContextFactsService(
            gateway, store, continuity_store, setting_store
        ).build(
            ContextFactsInput(
                user_input="我问顾阁主盐帮的最新动向。",
                core_memory=_core_memory(),
                current_state=_state(),
                scene_summaries=[],
                matched_anchors=[],
                npc_profiles=[_npc_view()],
                current_turn_index=4,
        )
            )

        assert store.turn_count() == 1
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["task"] == "context_extract"
    assert result.search_plan.intents
    assert result.history_evidence.hits
    assert [match.name for match in result.settings.settings] == ["客栈上房"]
    assert result.settings.active_count == 1
    assert "房梁暗格里藏着盐路账册摹本" not in gateway.calls[0][
        "input_data"
    ]["settings_json"]


def test_context_facts_projection_hides_internal_evidence_ids():
    facts = ContextFactSet(
        turn_focus="判断盐帮威胁。",
        facts=[{
            "fact_id": "f1",
            "fact": "盐帮追兵仍在客栈外搜寻顾晚棠。",
            "evidence_ids": ["turn_4_p001"],
        }],
    )

    assert json.loads(project_context_facts(facts)) == [{
        "fact_id": "f1",
        "fact": "盐帮追兵仍在客栈外搜寻顾晚棠。",
    }]


def test_select_context_profiles_matches_state_and_user_input(tmp_path):
    npc_store = NPCProfileStore(str(tmp_path / "npcs.db"))
    try:
        npc_store.upsert(
            NPCProfile(
                name="顾晚棠",
                aliases=["顾阁主"],
                role="万象阁阁主",
            )
        )

        result = select_context_profiles(
            user_input="外面都是盐帮追兵，我让顾阁主在这里躲一阵。",
            current_state=_state(),
            npc_store=npc_store,
        )
    finally:
        npc_store.close()

    assert [profile.name for profile in result.npc_profiles] == ["顾晚棠"]
    assert {anchor.text for anchor in result.anchors} == {"顾晚棠"}


def test_select_context_profiles_matches_aliases_from_current_state(tmp_path):
    npc_store = NPCProfileStore(str(tmp_path / "npcs.db"))
    try:
        npc_store.upsert(
            NPCProfile(
                name="希尔达·布拉斯科特",
                aliases=["委托人"],
                role="藏书修复师与情报商人",
            )
        )
        state = CurrentState(
            scene=SceneState(
                scene_id="scene_005",
                location="镇西废弃小屋",
                participants=["莱尔", "委托人"],
            ),
            player=PlayerState(name="莱尔"),
            resources=ResourcesState(
                inventory=[InventoryItem(name="失窃旧书", quantity=1)]
            ),
        )

        result = select_context_profiles(
            user_input="我留意四周。",
            current_state=state,
            npc_store=npc_store,
        )
    finally:
        npc_store.close()

    assert [profile.name for profile in result.npc_profiles] == [
        "希尔达·布拉斯科特"
    ]


def test_setting_retriever_selects_relevant_public_settings():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="location",
            entities=[ContextSearchEntity(name="客栈上房")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠", aliases=["顾阁主"])],
        ),
    ])
    with WorldSettingStore() as store:
        store.save_many([
            WorldSetting(
                kind="location",
                name="客栈上房",
                summary="客栈二层临街的上房。",
                public_facts=["入夜后街面有盐帮暗哨。"],
                created_turn=1,
            ),
            WorldSetting(
                kind="faction",
                name="盐帮",
                summary="掌控漕运与走私路线的江湖帮派。",
                public_facts=["盐帮垄断盐路走私。"],
                created_turn=1,
            ),
            WorldSetting(
                kind="lore",
                name="万象阁秘闻",
                summary="与当前剧情无关的遥远传说。",
                public_facts=["万象阁藏有古卷。"],
                created_turn=1,
            ),
        ])
        result = SettingRetriever(store).retrieve(
            user_input="我问顾阁主盐帮的动向。",
            current_state=_state(),
            search_plan=plan,
        )

    assert result.active_count == 3
    assert [match.name for match in result.settings] == ["客栈上房", "盐帮"]
    assert result.settings[0].matched_location is True
    assert result.settings[1].matched_name_in_input is True
    assert all(match.kind in {"faction", "location", "item", "lore"}
               for match in result.settings)


def test_setting_retriever_limits_and_never_exposes_hidden_facts():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["帮派"],
        ),
    ])
    with WorldSettingStore() as store:
        store.save_many([
            WorldSetting(
                kind="faction",
                name=f"帮派{index}",
                summary="与当前剧情相关的江湖帮派。",
                public_facts=[f"帮派{index}垄断盐路走私。"],
                hidden_facts=[f"帮派{index}的幕后主人是顾晚棠的生父。"],
                created_turn=1,
            )
            for index in range(1, 7)
        ])
        result = SettingRetriever(store).retrieve(
            user_input="我问顾晚棠这些帮派的情况。",
            current_state=_state(),
            search_plan=plan,
        )
        projected = project_settings(result)

    assert len(result.settings) == 4
    assert result.active_count == 6
    assert all(match.public_facts for match in result.settings)
    assert "生父" not in projected
    payload = json.loads(projected)
    assert len(payload) == 4
    assert all("hidden_facts" not in item for item in payload)
