import json
from pathlib import Path

import pytest

from app.domain.action.schemas import ActionRetrievalTopic, Anchor
from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.continuity import (
    ContinuityItem,
    ContinuityMatch,
    ContinuitySelection,
    ContinuityStore,
)
from app.domain.context_facts import (
    ContextExtractor,
    ContextExtractorInput,
    ContextFactsInput,
    ContextFactsService,
    ContextHistoryEvidenceSet,
    ContextHistoryHit,
    ContextHistoryRetriever,
    HistoryDiversityPolicy,
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
    SettingMatch,
    SettingRetriever,
    SettingSelection,
    build_context_search_plan,
)
from app.domain.context_facts.schemas import ContextFactSet
from app.domain.context_facts.views import (
    project_context_facts,
    project_context_history,
    project_context_history_payload,
    project_continuity,
    project_continuity_payload,
    project_settings,
    project_settings_payload,
)
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
from app.domain.npc_profile.selection import select_context_profiles


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


def test_context_history_projection_omits_empty_and_internal_fields():
    evidence = ContextHistoryEvidenceSet(hits=[ContextHistoryHit(
        evidence_id="turn_1_p001",
        source_type="scene_paragraph",
        turn_index=1,
        scene_id="scene_001",
        paragraph_index=1,
        text="顾晚棠点头。",
        matched_intent_ids=["i1"],
        score=0.8,
    )])

    payload = project_context_history_payload(evidence)

    assert payload == [{
        "evidence_id": "turn_1_p001",
        "source_type": "scene_paragraph",
        "turn_index": 1,
        "text": "顾晚棠点头。",
    }]
    assert json.loads(project_context_history(evidence)) == payload


def test_context_history_projection_keeps_nonempty_evidence_context():
    evidence = ContextHistoryEvidenceSet(hits=[ContextHistoryHit(
        evidence_id="turn_1_p002",
        source_type="scene_paragraph",
        turn_index=1,
        scene_id="scene_001",
        paragraph_index=2,
        text="……有界摘录……",
        text_truncated=True,
        original_text_chars=500,
        context_before=[{
            "evidence_id": "turn_1_p001",
            "text": "前文。",
        }],
        context_after=[{
            "evidence_id": "turn_1_p003",
            "text": "后文。",
        }],
        matched_intent_ids=["i1", "i2"],
        matched_entities=["顾晚棠"],
        matched_topics=["账册"],
        score=0.8,
        supporting_evidence_ids=["turn_2_p001"],
        supporting_evidence=[{
            "evidence_id": "turn_2_p001",
            "text": "另一处直接证据。",
        }],
    )])

    record = project_context_history_payload(evidence)[0]

    assert record["text_truncated"] is True
    assert record["original_text_chars"] == 500
    assert record["context_before"] == [{
        "evidence_id": "turn_1_p001",
        "text": "前文。",
    }]
    assert record["context_after"] == [{
        "evidence_id": "turn_1_p003",
        "text": "后文。",
    }]
    assert record["matched_entities"] == ["顾晚棠"]
    assert record["matched_topics"] == ["账册"]
    assert record["supporting_evidence"] == [{
        "evidence_id": "turn_2_p001",
        "text": "另一处直接证据。",
    }]
    assert "matched_intent_ids" not in record


def test_continuity_and_setting_projections_omit_empty_optional_fields():
    continuity = ContinuitySelection(items=[ContinuityMatch(
        item=ContinuityItem(
            id="thread_1",
            kind="open_thread",
            subject="外围探子",
            premise="探子威胁仍在。",
            current_state=None,
            created_turn=1,
            updated_turn=2,
        ),
        score=0.9,
    )])
    settings = SettingSelection(settings=[SettingMatch(
        setting_id="setting_1",
        kind="location",
        name="陷空山",
        summary="合欢宗驻地所在灵山。",
        score=0.8,
    )])

    continuity_payload = project_continuity_payload(continuity)
    settings_payload = project_settings_payload(settings)

    assert continuity_payload == [{
        "id": "thread_1",
        "kind": "open_thread",
        "subject": "外围探子",
        "premise": "探子威胁仍在。",
    }]
    assert settings_payload == [{
        "setting_id": "setting_1",
        "kind": "location",
        "name": "陷空山",
        "summary": "合欢宗驻地所在灵山。",
    }]
    assert json.loads(project_continuity(continuity)) == continuity_payload
    assert json.loads(project_settings(settings)) == settings_payload
    assert project_context_history(ContextHistoryEvidenceSet()) == "[]"
    assert project_continuity(ContinuitySelection()) == "[]"
    assert project_settings(SettingSelection()) == "[]"


def test_continuity_and_setting_projections_keep_nonempty_optional_fields():
    continuity = ContinuitySelection(items=[ContinuityMatch(
        item=ContinuityItem(
            id="thread_1",
            kind="commitment",
            subject="拜师",
            premise="明日正式拜师。",
            current_state="已约定明日一早完成拜师。",
            entities=["萧欢", "苏念薇"],
            location="陷空山",
            deadline="明日一早",
            created_turn=10,
            updated_turn=11,
        ),
        score=0.9,
    )])
    settings = SettingSelection(settings=[SettingMatch(
        setting_id="setting_1",
        kind="location",
        name="陷空山",
        summary="合欢宗驻地所在灵山。",
        public_facts=["山中存在灵脉。"],
        score=0.8,
    )])

    continuity_record = project_continuity_payload(continuity)[0]
    assert continuity_record["current_state"] == "已约定明日一早完成拜师。"
    assert continuity_record["entities"] == ["萧欢", "苏念薇"]
    assert continuity_record["location"] == "陷空山"
    assert continuity_record["deadline"] == "明日一早"
    assert project_settings_payload(settings)[0]["public_facts"] == [
        "山中存在灵脉。"
    ]


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
        and intent.origin == "current_context"
        for intent in plan.intents
    )
    assert any(
        intent.focus == "entity"
        and intent.entities[0].name == "顾晚棠"
        and "顾阁主" in intent.entities[0].aliases
        and intent.origin == "explicit_input"
        for intent in plan.intents
    )
    relationship = next(
        intent for intent in plan.intents if intent.focus == "relationship"
    )
    assert relationship.origin == "relationship"
    assert {entity.name for entity in relationship.entities} >= {
        "萧云痕",
        "顾晚棠",
    }
    topic = next(
        intent
        for intent in plan.intents
        if "解决方法" in intent.topics
    )
    assert topic.origin == "explicit_input"


def test_parser_topics_replace_rule_extraction_and_remain_explicit():
    plan = build_context_search_plan(
        user_input=(
            "第一位弟子安顿好了，接下去要处理山脚下的老鼠们了，"
            "我选择离开护山大阵，埋伏在山脚下的必经之路，"
            "查看刚刚的周姓修士是否会前来刺探我宗情报。"
        ),
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[],
        retrieval_topics=[
            ActionRetrievalTopic(
                text="埋伏在山脚下",
            ),
            ActionRetrievalTopic(
                text="刺探我宗情报",
            ),
        ],
    )

    topic_intents = [
        intent
        for intent in plan.intents
        if intent.focus == "topic"
    ]
    assert [intent.topics for intent in topic_intents] == [
        ["埋伏在山脚下"],
        ["刺探我宗情报"],
    ]
    assert all(intent.origin == "explicit_input" for intent in topic_intents)


def test_known_entity_topic_deduplicates_into_explicit_entity():
    plan = build_context_search_plan(
        user_input="我询问顾阁主。",
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[_npc_view()],
        retrieval_topics=[ActionRetrievalTopic(text="顾阁主")],
    )

    entity = next(
        intent
        for intent in plan.intents
        if intent.focus == "entity"
        and intent.entities[0].name == "顾晚棠"
    )
    assert entity.origin == "explicit_input"
    assert not any(
        intent.focus == "topic" and "顾阁主" in intent.topics
        for intent in plan.intents
    )


def test_unknown_explicit_names_remain_topics():
    plan = build_context_search_plan(
        user_input="离开护山大阵，查看周姓修士。",
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[],
        retrieval_topics=[
            ActionRetrievalTopic(text="护山大阵"),
            ActionRetrievalTopic(text="周姓修士"),
        ],
    )

    topics = {
        topic
        for intent in plan.intents
        if intent.focus == "topic"
        for topic in intent.topics
    }
    assert {"护山大阵", "周姓修士"} <= topics


def test_contiguous_parser_topics_retrieve_natural_history_wording():
    plan = build_context_search_plan(
        user_input="埋伏在山脚下，查看是否有人刺探我宗情报。",
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[],
        retrieval_topics=[
            ActionRetrievalTopic(text="埋伏在山脚下"),
            ActionRetrievalTopic(text="刺探我宗情报"),
        ],
    )
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_history_1",
                turn_index=1,
                scene_text="有人在山脚下埋伏，盯着必经之路。",
            ),
            ShortTermScene(
                scene_id="scene_history_2",
                turn_index=2,
                scene_text="周姓修士试图刺探我宗情报。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=3,
        )

    hit_text = "\n".join(hit.text for hit in result.hits)
    assert "山脚下埋伏" in hit_text
    assert "刺探我宗情报" in hit_text


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

    anchor_topics = {
        intent.topics[0]
        for intent in plan.intents
        if intent.focus == "topic" and intent.topics
    }
    assert {f"目标{i}" for i in range(18)} <= anchor_topics
    assert all(
        intent.origin == "explicit_input"
        for intent in plan.intents
        if intent.topics and intent.topics[0].startswith("目标")
    )
    assert len(plan.intents) > 16


def test_explicit_anchor_promotes_existing_context_entity_intent():
    plan = build_context_search_plan(
        user_input="我继续观察。",
        current_state=_state(),
        matched_anchors=[Anchor(
            text="顾阁主",
            normalized="顾晚棠",
            kind="character",
            source="explicit",
            confidence=1.0,
        )],
        npc_profiles=[_npc_view()],
    )

    matching = [
        intent
        for intent in plan.intents
        if intent.entities and intent.entities[0].name == "顾晚棠"
    ]
    assert len(matching) == 1
    assert matching[0].origin == "explicit_input"


def test_implicit_anchor_promotes_context_entity_below_explicit_input():
    plan = build_context_search_plan(
        user_input="我继续观察。",
        current_state=_state(),
        matched_anchors=[Anchor(
            text="顾阁主",
            normalized="顾晚棠",
            kind="character",
            source="implicit_from_scene",
            confidence=0.8,
        )],
        npc_profiles=[_npc_view()],
    )

    matching = next(
        intent
        for intent in plan.intents
        if intent.entities and intent.entities[0].name == "顾晚棠"
    )
    assert matching.origin == "matched_anchor"


def test_explicit_two_character_event_from_player_input_is_retrievable():
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="河湾旅店",
            participants=["萧云痕", "艾琳"],
        ),
        player=PlayerState(name="萧云痕"),
        resources=ResourcesState(),
    )
    ailin = NPCProfile(
        name="艾琳",
        aliases=[],
        role="旅店住客",
        attitude_to_player="谨慎",
    ).director_view()
    plan = build_context_search_plan(
        user_input="问艾琳关于黑塔的事",
        current_state=state,
        matched_anchors=[],
        npc_profiles=[ailin],
    )

    explicit_topic = next(
        intent
        for intent in plan.intents
        if intent.focus == "topic" and "黑塔" in intent.topics
    )
    assert explicit_topic.origin == "explicit_input"

    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_history",
            turn_index=1,
            scene_text="黑塔北门一直处于封闭状态。",
        )])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    assert result.hits[0].matched_topics == ["黑塔"]


def test_anchor_kind_routes_events_away_from_relationship_entities():
    anchors = [
        Anchor(
            text="艾琳",
            normalized="艾琳",
            kind="character",
            source="explicit",
            confidence=1.0,
        ),
        Anchor(
            text="黑塔",
            normalized="黑塔",
            kind="location",
            source="explicit",
            confidence=1.0,
        ),
        Anchor(
            text="灰钥匙",
            normalized="灰钥匙",
            kind="item",
            source="explicit",
            confidence=1.0,
        ),
        Anchor(
            text="黑塔密约",
            normalized="黑塔密约",
            kind="concept",
            source="explicit",
            confidence=1.0,
        ),
        Anchor(
            text="调查失踪案",
            normalized="调查失踪案",
            kind="action_keyword",
            source="explicit",
            confidence=1.0,
        ),
    ]
    plan = build_context_search_plan(
        user_input="我询问艾琳并调查失踪案。",
        current_state=_state(),
        matched_anchors=anchors,
        npc_profiles=[],
    )

    assert any(
        intent.focus == "location"
        and intent.entities[0].name == "黑塔"
        for intent in plan.intents
    )
    assert any(
        intent.focus == "entity"
        and intent.entities[0].name == "灰钥匙"
        for intent in plan.intents
    )
    assert {"黑塔密约", "调查失踪案"} <= {
        topic
        for intent in plan.intents
        if intent.focus == "topic"
        for topic in intent.topics
    }
    relationship = next(
        intent for intent in plan.intents if intent.focus == "relationship"
    )
    relationship_names = {entity.name for entity in relationship.entities}
    assert "艾琳" in relationship_names
    assert relationship_names.isdisjoint({
        "黑塔", "灰钥匙", "黑塔密约", "调查失踪案",
    })


def test_single_character_entity_substrings_are_not_promoted_without_anchor():
    profile = NPCProfile(
        name="安",
        aliases=["白"],
        role="路人",
        attitude_to_player="中立",
    ).director_view()
    plan = build_context_search_plan(
        user_input="我检查安全出口，现在已经明白了。",
        current_state=_state(),
        matched_anchors=[],
        npc_profiles=[profile],
    )

    assert not any(
        intent.entities and intent.entities[0].name == "安"
        for intent in plan.intents
    )

    anchored = build_context_search_plan(
        user_input="我继续检查。",
        current_state=_state(),
        matched_anchors=[Anchor(
            text="安",
            normalized="安",
            kind="character",
            source="explicit",
            confidence=1.0,
        )],
        npc_profiles=[profile],
    )
    explicit = next(
        intent
        for intent in anchored.intents
        if intent.entities and intent.entities[0].name == "安"
    )
    assert explicit.origin == "explicit_input"


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


def test_history_retriever_caps_entity_owned_hits_independently_of_fact_limit():
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

    assert len(result.hits) == 3
    assert [hit.turn_index for hit in result.hits] == [15, 14, 13]


def test_history_diversity_uses_quota_then_global_fill():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_ailin",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="topic_tower",
            focus="topic",
            topics=["黑塔"],
        ),
        ContextSearchIntent(
            intent_id="location_inn",
            focus="location",
            entities=[ContextSearchEntity(name="河湾旅店")],
        ),
    ])
    scenes = [
        ShortTermScene(
            scene_id=f"scene_{turn:03d}",
            turn_index=turn,
            scene_text=f"艾琳整理了第{turn}份无关记录。",
        )
        for turn in range(1, 7)
    ] + [
        ShortTermScene(
            scene_id="scene_007",
            turn_index=7,
            scene_text="黑塔北门仍然封闭。",
        ),
        ShortTermScene(
            scene_id="scene_008",
            turn_index=8,
            scene_text="黑塔顶层昨夜出现灯光。",
        ),
        ShortTermScene(
            scene_id="scene_009",
            turn_index=9,
            scene_text="河湾旅店的后门通向码头。",
        ),
    ]
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, scenes)
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=6),
        ).retrieve(plan, current_turn_index=10)

    assert len(result.hits) == 6
    assert sum("艾琳" in hit.text for hit in result.hits) == 3
    assert sum("黑塔" in hit.text for hit in result.hits) == 2
    assert sum("河湾旅店" in hit.text for hit in result.hits) == 1


def test_explicit_entity_and_event_outrank_higher_scored_context_intent():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="context_npc",
            focus="entity",
            origin="current_context",
            entities=[ContextSearchEntity(name="背景人物")],
        ),
        ContextSearchIntent(
            intent_id="explicit_npc",
            focus="entity",
            origin="explicit_input",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="explicit_event",
            focus="topic",
            origin="explicit_input",
            topics=["黑塔密约"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="艾琳曾经留下口信。",
            ),
            ShortTermScene(
                scene_id="scene_002",
                turn_index=2,
                scene_text="黑塔密约约定在月末执行。",
            ),
            *[
                ShortTermScene(
                    scene_id=f"scene_{turn:03d}",
                    turn_index=turn,
                    scene_text=f"背景人物在第{turn}回合留下高相关记录。",
                )
                for turn in range(3, 8)
            ],
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=2),
        ).retrieve(plan, current_turn_index=8)

    assert [hit.matched_intent_ids for hit in result.hits] == [
        ["explicit_npc"],
        ["explicit_event"],
    ]


def test_explicit_origin_fill_finishes_before_context_quota():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="context_npc",
            focus="entity",
            origin="current_context",
            entities=[ContextSearchEntity(name="背景人物")],
        ),
        ContextSearchIntent(
            intent_id="explicit_event",
            focus="topic",
            origin="explicit_input",
            topics=["显式事件"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            *[
                ShortTermScene(
                    scene_id=f"explicit_{turn}",
                    turn_index=turn,
                    scene_text=f"显式事件留下第{turn}条旧记录。",
                )
                for turn in range(1, 5)
            ],
            *[
                ShortTermScene(
                    scene_id=f"context_{turn}",
                    turn_index=turn,
                    scene_text=f"背景人物留下第{turn}条近期记录。",
                )
                for turn in range(97, 101)
            ],
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=5),
        ).retrieve(plan, current_turn_index=101)

    assert [hit.matched_intent_ids for hit in result.hits] == [
        ["explicit_event"],
        ["explicit_event"],
        ["explicit_event"],
        ["explicit_event"],
        ["context_npc"],
    ]


def test_one_hit_updates_three_intent_coverages_without_duplicate_payload():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_ailin",
            focus="entity",
            origin="explicit_input",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="topic_tower",
            focus="topic",
            origin="explicit_input",
            topics=["黑塔"],
        ),
        ContextSearchIntent(
            intent_id="location_inn",
            focus="location",
            entities=[ContextSearchEntity(name="河湾旅店")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text="艾琳在河湾旅店画出了黑塔的入口。",
        )])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    assert result.hits[0].matched_intent_ids == [
        "entity_ailin",
        "location_inn",
        "topic_tower",
    ]
    assert project_context_history(result).count(result.hits[0].evidence_id) == 1


def test_topic_can_own_hit_after_entity_owner_cap_is_full():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_ailin",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="topic_tower",
            focus="topic",
            topics=["黑塔"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                scene_text=f"艾琳记录了黑塔第{turn}层的门锁。",
            )
            for turn in range(1, 6)
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=5, entity_cap=2),
        ).retrieve(plan, current_turn_index=6)

    assert len(result.hits) == 5
    assert all("艾琳" in hit.text and "黑塔" in hit.text for hit in result.hits)


def test_relationship_intent_cannot_bypass_entity_cap_with_one_entity_match():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_ailin",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="relationship_player_ailin",
            focus="relationship",
            entities=[
                ContextSearchEntity(name="玩家"),
                ContextSearchEntity(name="艾琳"),
            ],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                scene_text=f"艾琳整理了第{turn}份个人记录。",
            )
            for turn in range(1, 7)
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=6, entity_cap=2),
        ).retrieve(plan, current_turn_index=7)

    assert len(result.hits) == 2


def test_genuine_relationship_match_can_own_hits_after_entity_cap():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_ailin",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="relationship_player_ailin",
            focus="relationship",
            entities=[
                ContextSearchEntity(name="玩家"),
                ContextSearchEntity(name="艾琳"),
            ],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                scene_text=f"玩家与艾琳共同处理了第{turn}份记录。",
            )
            for turn in range(1, 5)
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=4, entity_cap=2),
        ).retrieve(plan, current_turn_index=5)

    assert len(result.hits) == 4
    assert all(
        set(hit.matched_entities) == {"玩家", "艾琳"}
        for hit in result.hits
    )


def test_short_exact_topic_receives_direct_match_score():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="topic_tower",
            focus="topic",
            topics=["黑塔"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text="艾琳谈起黑塔北门。",
        )])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    assert result.hits[0].matched_topics == ["黑塔"]
    assert result.hits[0].score > 0.7


def test_high_priority_intents_exhaust_max_hits_deterministically():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="low_location",
            focus="location",
            origin="current_context",
            entities=[ContextSearchEntity(name="河湾旅店")],
        ),
        *[
            ContextSearchIntent(
                intent_id=f"high_entity_{index}",
                focus="entity",
                origin="explicit_input",
                entities=[ContextSearchEntity(name=f"人物{index}")],
            )
            for index in range(1, 4)
        ],
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text="河湾旅店今日照常营业。",
            ),
            *[
                ShortTermScene(
                    scene_id=f"scene_{index + 1:03d}",
                    turn_index=index + 1,
                    scene_text=f"人物{index}留下了自己的线索。",
                )
                for index in range(1, 4)
            ],
        ])
        retriever = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=2),
        )
        first = retriever.retrieve(plan, current_turn_index=5)
        second = retriever.retrieve(plan, current_turn_index=5)

    first_ids = [hit.evidence_id for hit in first.hits]
    assert first_ids == [hit.evidence_id for hit in second.hits]
    assert [hit.matched_intent_ids for hit in first.hits] == [
        ["high_entity_1"],
        ["high_entity_2"],
    ]


def test_history_retriever_merges_multi_intent_alias_and_topic_hits_on_one_evidence():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠", aliases=["顾阁主"])],
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
                scene_text="顾阁主手持账册摹本走入厅堂。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert sorted(hit.matched_intent_ids) == ["i1", "i2"]
    assert hit.matched_entities == ["顾晚棠"]
    assert hit.matched_topics == ["账册摹本"]
    assert hit.supporting_evidence_ids == []


def test_adjacent_paragraph_hit_does_not_create_duplicate_candidate():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠", aliases=["顾阁主"])],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text=(
                    "顾阁主承认账册摹本并不在她身上。\n\n"
                    "她拒绝透露具体藏匿地点。"
                ),
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.evidence_id == "turn_1_p001"
    assert hit.context_after and "藏匿地点" in hit.context_after[0].text
    assert hit.context_after[0].evidence_id == "turn_1_p002"


def test_identical_text_across_adjacent_turns_is_not_merged():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn_index:02d}",
                turn_index=turn_index,
                scene_text="艾琳向玩家点头。",
            )
            for turn_index in (30, 31)
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=32,
        )

    assert len(result.hits) == 2
    assert {hit.turn_index for hit in result.hits} == {30, 31}
    assert all(hit.supporting_evidence_ids == [] for hit in result.hits)


def test_identical_adjacent_paragraphs_in_same_turn_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳向玩家点头。\n\n艾琳向玩家点头。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert len(hit.supporting_evidence_ids) == 1
    assert hit.supporting_evidence_ids == ["turn_30_p001"]


def test_non_adjacent_identical_paragraphs_do_not_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text=(
                    "艾琳向玩家点头。\n\n"
                    "艾琳走出门去。\n\n"
                    "艾琳向玩家点头。"
                ),
            ),
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(turn_cap=3),
        ).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 3
    assert all(hit.supporting_evidence_ids == [] for hit in result.hits)


def test_numeric_and_negation_differences_block_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳在第30回合点头。\n\n艾琳在第31回合点头。",
            ),
        ])
        numeric = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=32,
        )
    assert len(numeric.hits) == 2

    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳已获得钥匙。\n\n艾琳未获得钥匙。",
            ),
        ])
        negation = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=32,
        )
    assert len(negation.hits) == 2


def test_clustered_evidence_ids_remain_valid_for_context_facts():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳向玩家点头。\n\n艾琳向玩家点头。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 1
    supporting = result.hits[0].supporting_evidence_ids[0]
    known = {
        evidence_id
        for hit in result.hits
        for evidence_id in [
            hit.evidence_id,
            *hit.supporting_evidence_ids,
            *(item.evidence_id for item in hit.context_before),
            *(item.evidence_id for item in hit.context_after),
        ]
    }
    facts = ContextFactSet.from_drafts(
        turn_focus="focus",
        facts=[
            {
                "holder": "艾琳",
                "source": "史",
                "fact": "艾琳向玩家点头。",
                "evidence_ids": [supporting],
            },
        ],
        known_evidence_ids=known,
    )
    assert facts.facts[0].evidence_ids == [supporting]


def test_adjacent_context_ids_remain_valid_for_context_facts():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠", aliases=["顾阁主"])],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text=(
                    "顾阁主承认账册摹本并不在她身上。\n\n"
                    "她拒绝透露具体藏匿地点。"
                ),
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=2,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.context_after
    adjacent_id = hit.context_after[0].evidence_id
    known = {
        evidence_id
        for item in hit.context_before + hit.context_after
        for evidence_id in [item.evidence_id]
    } | {hit.evidence_id, *hit.supporting_evidence_ids}
    facts = ContextFactSet.from_drafts(
        turn_focus="focus",
        facts=[
            {
                "holder": "萧云痕",
                "source": "史",
                "fact": "顾晚棠拒绝透露藏匿地点。",
                "evidence_ids": [adjacent_id],
            },
        ],
        known_evidence_ids=known,
    )
    assert facts.facts[0].evidence_ids == [adjacent_id]


def test_cluster_representative_merges_member_metadata_and_max_score():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["点头示意"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳点头示意。\n\n艾琳点头示意。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert sorted(hit.matched_intent_ids) == ["i1", "i2"]
    assert hit.matched_entities == ["艾琳"]
    assert hit.matched_topics == ["点头示意"]
    assert len(hit.supporting_evidence_ids) == 1
    assert hit.score > 0


def test_disjoint_topics_do_not_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["账册", "盐帮"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text=(
                    "艾琳负责处理账册方面的事务。\n\n"
                    "艾琳负责处理盐帮方面的事务。"
                ),
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 2
    assert {hit.paragraph_index for hit in result.hits} == {1, 2}


def test_state_change_and_time_differences_do_not_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    pairs = (
        ("艾琳持有那把钥匙站在门口。", "艾琳交出那把钥匙站在门口。"),
        ("艾琳仍然存活于地牢之中。", "艾琳已然死亡于地牢之中。"),
        ("艾琳清晨来到客栈。", "艾琳黄昏来到客栈。"),
    )
    for first, second in pairs:
        with ConfirmedHistoryStore() as store:
            _upsert_scenes(store, [
                ShortTermScene(
                    scene_id="scene_030",
                    turn_index=30,
                    scene_text=f"{first}\n\n{second}",
                ),
            ])
            result = ContextHistoryRetriever(store).retrieve(
                plan,
                current_turn_index=31,
            )
        assert len(result.hits) == 2, (
            f"expected no merge for {first!r} / {second!r}"
        )


def test_negated_content_difference_blocks_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["盐帮", "万象阁"],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳不是盐帮成员。\n\n艾琳不是万象阁成员。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 2
    assert {hit.paragraph_index for hit in result.hits} == {1, 2}


def test_numeric_quantity_differences_block_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
        ContextSearchIntent(
            intent_id="i2",
            focus="topic",
            topics=["钥匙"],
        ),
    ])
    pairs = (
        ("艾琳携带 2 把钥匙。", "艾琳携带钥匙。"),
        ("艾琳携带两把钥匙。", "艾琳携带三把钥匙。"),
    )
    for first, second in pairs:
        with ConfirmedHistoryStore() as store:
            _upsert_scenes(store, [
                ShortTermScene(
                    scene_id="scene_030",
                    turn_index=30,
                    scene_text=f"{first}\n\n{second}",
                ),
            ])
            result = ContextHistoryRetriever(store).retrieve(
                plan,
                current_turn_index=31,
            )
        assert len(result.hits) == 2, (
            f"expected no merge for {first!r} / {second!r}"
        )


def test_different_subject_entities_do_not_merge():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[
                ContextSearchEntity(name="艾琳"),
                ContextSearchEntity(name="萧云痕"),
            ],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳走向门口。\n\n萧云痕走向门口。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 2
    assert {hit.paragraph_index for hit in result.hits} == {1, 2}


def test_distant_identical_text_is_not_merged():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id=f"scene_{turn_index:03d}",
                turn_index=turn_index,
                scene_text="艾琳向玩家点头。",
            )
            for turn_index in (1, 100)
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=101,
        )

    assert len(result.hits) == 2
    assert {hit.turn_index for hit in result.hits} == {1, 100}
    assert all(hit.supporting_evidence_ids == [] for hit in result.hits)


def test_adjacent_identical_pair_merges_and_non_adjacent_stays_separate():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text=(
                    "艾琳向玩家点头。\n\n"
                    "艾琳向玩家点头。\n\n"
                    "艾琳走出门去。"
                ),
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    merged = [hit for hit in result.hits if hit.supporting_evidence_ids]
    standalone = [hit for hit in result.hits if not hit.supporting_evidence_ids]
    assert len(merged) == 1
    assert len(standalone) == 1
    assert standalone[0].paragraph_index == 3


def test_supporting_evidence_ids_match_provided_texts():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳向玩家点头。\n\n艾琳向玩家点头。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert set(hit.supporting_evidence_ids) == {
        item.evidence_id for item in hit.supporting_evidence
    }


def test_supporting_evidence_carries_member_full_text():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳向玩家点头。\n\n艾琳向玩家点头。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert len(hit.supporting_evidence) == 1
    assert hit.supporting_evidence[0].evidence_id == "turn_30_p001"
    assert hit.supporting_evidence[0].text == "艾琳向玩家点头。"


def test_history_payload_budget_drops_optional_context_before_main_text():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text=(
                "艾琳向玩家点头。\n\n"
                "艾琳向玩家点头。\n\n"
                f"没有检索词的邻接说明：{'很长。' * 300}"
            ),
        )])
        full = ContextHistoryRetriever(store).retrieve(plan, current_turn_index=2)
        assert full.hits[0].supporting_evidence
        assert full.hits[0].context_after
        stripped_hit = full.hits[0].model_copy(update={
            "context_before": [],
            "context_after": [],
            "supporting_evidence_ids": [],
            "supporting_evidence": [],
        })
        stripped_payload = ContextHistoryEvidenceSet(hits=[stripped_hit])
        budget = len(project_context_history(stripped_payload))
        bounded = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(
                max_payload_chars=budget,
            ),
        ).retrieve(plan, current_turn_index=2)

    assert len(project_context_history(bounded)) <= budget
    assert bounded.hits[0].text == "艾琳向玩家点头。"
    assert not bounded.hits[0].text_truncated
    assert bounded.hits[0].context_before == []
    assert bounded.hits[0].context_after == []
    assert bounded.hits[0].supporting_evidence == []


def test_history_payload_budget_retains_explicit_main_before_context_candidate():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="context_npc",
            focus="entity",
            origin="current_context",
            entities=[ContextSearchEntity(name="背景人物")],
        ),
        ContextSearchIntent(
            intent_id="explicit_event",
            focus="topic",
            origin="explicit_input",
            topics=["黑塔密约"],
        ),
    ])
    policy = HistoryDiversityPolicy(
        max_hits=2,
        max_payload_chars=800,
        max_evidence_text_chars=240,
        min_excerpt_chars=80,
    )
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text=(
                "黑塔密约将在月末执行。\n\n"
                f"背景人物记录：{'冗长背景。' * 500}"
            ),
        )])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=policy,
        ).retrieve(plan, current_turn_index=2)

    assert result.hits
    assert result.hits[0].matched_intent_ids == ["explicit_event"]
    assert "黑塔密约" in result.hits[0].text
    assert result.hits[0].context_after == []
    assert len(project_context_history(result)) <= policy.max_payload_chars


def test_history_payload_budget_uses_marked_bounded_excerpt():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    original = f"艾琳开始陈述。{'这是一段很长的历史证据。' * 300}陈述结束。"
    policy = HistoryDiversityPolicy(
        max_payload_chars=900,
        max_evidence_text_chars=240,
        min_excerpt_chars=80,
    )
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text=original,
        )])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=policy,
        ).retrieve(plan, current_turn_index=2)

    assert len(result.hits) == 1
    hit = result.hits[0]
    assert hit.text_truncated
    assert hit.original_text_chars == len(original)
    assert "[bounded excerpt]" in hit.text
    assert len(project_context_history(result)) <= policy.max_payload_chars


def test_history_payload_budget_skips_evidence_when_minimum_excerpt_cannot_fit():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    policy = HistoryDiversityPolicy(
        max_payload_chars=100,
        max_evidence_text_chars=80,
        min_excerpt_chars=40,
    )
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [ShortTermScene(
            scene_id="scene_001",
            turn_index=1,
            scene_text=f"艾琳记录：{'内容。' * 100}",
        )])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=policy,
        ).retrieve(plan, current_turn_index=2)

    assert result.hits == []
    assert project_context_history(result) == "[]"


def test_history_payload_budget_retries_after_global_dedup_frees_space():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="entity_alpha",
            focus="entity",
            entities=[ContextSearchEntity(name="阿尔法")],
        ),
        ContextSearchIntent(
            intent_id="topic_clue",
            focus="topic",
            topics=["线索丙"],
        ),
        ContextSearchIntent(
            intent_id="location_beta",
            focus="location",
            entities=[ContextSearchEntity(name="贝塔")],
        ),
    ])
    # Calibrated against the compact final projection: the clue initially
    # cannot fit beside alpha's large adjacent paragraph, then fits after beta
    # becomes a main hit and global evidence de-duplication removes that copy.
    policy = HistoryDiversityPolicy(max_hits=3, max_payload_chars=6_000)
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                scene_text=(
                    "阿尔法留下短记录。\n\n"
                    f"贝塔{'长内容。' * 1400}"
                ),
            ),
            ShortTermScene(
                scene_id="scene_002",
                turn_index=2,
                scene_text="线索丙是一条短记录。",
            ),
        ])
        result = ContextHistoryRetriever(
            store,
            diversity_policy=policy,
        ).retrieve(plan, current_turn_index=3)

    assert [hit.evidence_id for hit in result.hits] == [
        "turn_1_p001",
        "turn_1_p002",
        "turn_2_p001",
    ]
    assert len(project_context_history(result)) <= policy.max_payload_chars


def test_context_extractor_prompt_explains_truncated_history_semantics():
    prompt = Path("app/prompts/generation/context_extract_v1.md").read_text(
        encoding="utf-8"
    )

    assert "`text_truncated=true`" in prompt
    assert "只能使用摘录里直接可见的内容" in prompt
    assert "下游无法直接得到的增量上下文" in prompt
    assert "不要把其中明确的内容复述为 fact" in prompt
    assert "不得把未展示的中段当作不存在" in prompt


def test_merge_conflict_detects_semantic_differences():
    from app.domain.context_facts.clustering import merge_conflict

    assert merge_conflict("不是盐帮成员。", "不是万象阁成员。")
    assert not merge_conflict("不是盐帮成员。", "不是盐帮成员。")
    assert merge_conflict("携带 2 把钥匙。", "携带钥匙。")
    assert merge_conflict("携带两把钥匙。", "携带三把钥匙。")
    assert not merge_conflict("携带两把钥匙。", "携带两把钥匙。")
    assert merge_conflict("艾琳持有钥匙。", "艾琳交出钥匙。")
    assert merge_conflict("艾琳清晨前来。", "艾琳黄昏前来。")
    assert not merge_conflict("艾琳清晨前来。", "艾琳清晨前来。")


def test_entity_sets_must_be_identical_for_merge():
    from app.domain.context_facts.clustering import cluster_near_duplicates

    class StubDocument:
        def __init__(self, document_id, paragraph_index, text):
            self.document_id = document_id
            self.turn_index = 1
            self.scene_id = "scene_001"
            self.paragraph_index = paragraph_index
            self.text = text

    class StubCandidate:
        def __init__(self, document, entities, topics):
            self.document = document
            self.matched_entities = set(entities)
            self.matched_topics = set(topics)
            self.matched_intent_ids = {"i1"}
            self.score = 1.0

    def make(first_entities, second_entities):
        return [
            StubCandidate(
                StubDocument("turn_1_p001", 1, "艾琳站在客栈门口。"),
                first_entities,
                [],
            ),
            StubCandidate(
                StubDocument("turn_1_p002", 2, "艾琳站在客栈门口。"),
                second_entities,
                [],
            ),
        ]

    assert len(cluster_near_duplicates(
        make(["艾琳", "客栈"], ["贝拉", "客栈"])
    )) == 2
    assert len(cluster_near_duplicates(make(["艾琳"], ["艾琳"]))) == 1
    assert len(cluster_near_duplicates(make([], []))) == 2


def test_cluster_members_are_excluded_from_adjacent_context():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="\n\n".join("艾琳向玩家点头。" for _ in range(3)),
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert any(hit.supporting_evidence_ids for hit in result.hits)
    for hit in result.hits:
        context_ids = {
            item.evidence_id for item in hit.context_before + hit.context_after
        }
        assert not (set(hit.supporting_evidence_ids) & context_ids), (
            "cluster members must not also appear as adjacent context"
        )
        all_ids = [hit.evidence_id, *hit.supporting_evidence_ids, *context_ids]
        assert len(all_ids) == len(set(all_ids)), (
            "evidence IDs must be unique within a hit"
        )


def test_evidence_documents_are_globally_unique_across_hits():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with ConfirmedHistoryStore() as store:
        _upsert_scenes(store, [
            ShortTermScene(
                scene_id="scene_030",
                turn_index=30,
                scene_text="艾琳走向门口。\n\n艾琳检查账册。",
            ),
        ])
        result = ContextHistoryRetriever(store).retrieve(
            plan,
            current_turn_index=31,
        )

    assert {hit.evidence_id for hit in result.hits} == {
        "turn_30_p001",
        "turn_30_p002",
    }
    seen = set()
    for hit in result.hits:
        ids = [
            hit.evidence_id,
            *hit.supporting_evidence_ids,
            *(item.evidence_id for item in hit.context_before),
            *(item.evidence_id for item in hit.context_after),
        ]
        for evidence_id in ids:
            assert evidence_id not in seen, f"duplicate evidence: {evidence_id}"
            seen.add(evidence_id)


def test_context_history_evidence_set_validates_global_uniqueness():
    def make_hit(evidence_id, context_after=None):
        return ContextHistoryHit(
            evidence_id=evidence_id,
            source_type="scene_paragraph",
            turn_index=1,
            scene_id="scene_001",
            paragraph_index=1,
            text="艾琳点头。",
            matched_intent_ids=["i1"],
            score=0.5,
            context_after=context_after or [],
        )

    with pytest.raises(ValueError):
        ContextHistoryEvidenceSet(hits=[
            make_hit(
                "turn_1_p001",
                context_after=[{
                    "evidence_id": "turn_1_p002",
                    "text": "x",
                }],
            ),
            make_hit("turn_1_p002"),
        ])

    ContextHistoryEvidenceSet(hits=[
        make_hit("turn_1_p001"),
        make_hit("turn_1_p002"),
    ])


def test_context_history_hit_validates_evidence_consistency():
    def make_hit(supporting_ids, supporting):
        return ContextHistoryHit(
            evidence_id="turn_1_p001",
            source_type="scene_paragraph",
            turn_index=1,
            scene_id="scene_001",
            paragraph_index=1,
            text="艾琳点头。",
            matched_intent_ids=["i1"],
            score=0.5,
            supporting_evidence_ids=supporting_ids,
            supporting_evidence=supporting,
        )

    valid = make_hit(
        ["turn_1_p002"],
        [{"evidence_id": "turn_1_p002", "text": "艾琳点头。"}],
    )
    assert valid.supporting_evidence[0].text == "艾琳点头。"

    with pytest.raises(ValueError):
        make_hit(["turn_1_p002"], [])
    with pytest.raises(ValueError):
        make_hit(
            ["turn_1_p001"],
            [{"evidence_id": "turn_1_p001", "text": "x"}],
        )
    with pytest.raises(ValueError):
        make_hit(
            ["turn_1_p002", "turn_1_p002"],
            [{"evidence_id": "turn_1_p002", "text": "x"}],
        )


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
