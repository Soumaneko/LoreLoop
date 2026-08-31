from app.runtime.stores import open_continuity_store
from app.runtime.stores import open_confirmed_history_store
from app.runtime.stores import open_npc_profile_store
from app.runtime.stores import open_world_setting_store
import json
from types import SimpleNamespace

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
from app.domain.context_facts.diversity import (
    AdmissionPreview,
    SelectionProposal,
    select_diverse_candidates,
)
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













def test_history_retriever_caps_entity_owned_hits_independently_of_fact_limit():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="顾晚棠")],
        )
    ])
    with open_confirmed_history_store() as store:
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

def test_history_diversity_balances_intent_coverage_then_fills():
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
    with open_confirmed_history_store() as store:
        _upsert_scenes(store, scenes)
        result = ContextHistoryRetriever(
            store,
            diversity_policy=HistoryDiversityPolicy(max_hits=6),
        ).retrieve(plan, current_turn_index=10)

    assert len(result.hits) == 6
    assert sum("艾琳" in hit.text for hit in result.hits) == 3
    assert sum("黑塔" in hit.text for hit in result.hits) == 2
    assert sum("河湾旅店" in hit.text for hit in result.hits) == 1














def test_history_payload_budget_drops_optional_context_before_main_text():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="i1",
            focus="entity",
            entities=[ContextSearchEntity(name="艾琳")],
        ),
    ])
    with open_confirmed_history_store() as store:
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










def test_context_facts_service_retrieves_persistent_history_and_calls_one_llm():
    class SpyGateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
                kwargs["on_attempt_start"](1)
            result = kwargs["output_schema"].model_validate({
                "turn_focus": "追问盐帮动向。",
                "facts": [],
            })
            if "output_validator" in kwargs and kwargs["output_validator"] is not None:
                kwargs["output_validator"](result)
            return result

    gateway = SpyGateway()
    with (
        open_confirmed_history_store() as store,
        open_continuity_store() as continuity_store,
        open_world_setting_store() as setting_store,
        open_npc_profile_store(":memory:") as npc_store,
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
            gateway, store, continuity_store, setting_store, npc_store
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

    assert "房梁暗格里藏着盐路账册摹本" not in gateway.calls[0][
        "input_data"
    ]["settings_json"]




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
    with open_world_setting_store() as store:
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

def _context_extractor_input(*, evidence_ids=("e1",)):
    hits = [
        ContextHistoryHit(
            evidence_id="e1",
            source_type="scene_paragraph",
            turn_index=1,
            scene_id="scene_001",
            paragraph_index=1,
            text="顾晚棠说明账册不在身上。",
            matched_intent_ids=["i1"],
            score=0.5,
        )
    ]
    return ContextExtractorInput(
        user_input="问顾晚棠账册下落。",
        core_memory=_core_memory(),
        current_state=_state(),
        scene_summaries=[],
        history_evidence=ContextHistoryEvidenceSet(hits=hits),
        continuity=ContinuitySelection(),
        settings=SettingSelection(),
        npc_profiles=[],
    )

def test_context_extractor_provider_failure_propagates():
    from app.core.llm.errors import LLMProviderError

    class FailingGateway:
        def run(self, **kwargs):
            raise LLMProviderError("provider down")

    extractor = ContextExtractor(FailingGateway())
    with pytest.raises(LLMProviderError):
        extractor.extract(_context_extractor_input())
