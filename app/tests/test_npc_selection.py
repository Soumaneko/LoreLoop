import pytest

from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.current_state import WorldClock
from app.domain.director import DirectorResourceRef
from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.selection import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_context_facts,
)
from app.tests.factories.stores import open_npc_profile_store


@pytest.mark.parametrize("facts", [
    ContextFactSet(turn_focus="观察", facts=[ContextFact(fact_id="f1", fact="顾晚棠在城门等候。")]),
    ContextFactSet(turn_focus="主角向顾阁主交付旧书。"),
    ContextFactSet(turn_focus="观察", facts=[ContextFact(fact_id="f1", fact="顾阁主在城门等候。")]),
], ids=["canonical-name", "focus-alias", "fact-alias"])
def test_context_selects_named_profiles_and_aliases(facts):
    with open_npc_profile_store() as store:
        profile = NPCProfile(name="顾晚棠", aliases=["顾阁主"])
        store.upsert(profile)
        store.upsert(NPCProfile(name="无关路人"))
        result = select_profiles_by_context_facts(context_facts=facts, npc_store=store)
    assert [p.npc_id for p in result] == [profile.npc_id]


def test_active_event_actors_resolve_ids_names_and_promoted_drafts():
    profiles = [
        NPCProfile(name="柴行简"), NPCProfile(name="林澈"),
        NPCProfile(name="江蓠", source_director_npc_id="draft-1"), NPCProfile(name="无关路人"),
    ]
    active = DirectorEvent(
        title="商路封锁", subject="河务署", intent="控制渡河通道", method="封锁旧桥",
        macro_intent="改变商路格局", deadline_clock=WorldClock(), created_turn=1, updated_turn=1,
        actors=[
            DirectorActorRef(name=profiles[0].name, ref=DirectorResourceRef(kind="npc", resource_id=profiles[0].npc_id)),
            DirectorActorRef(name=profiles[1].name),
            DirectorActorRef(name=profiles[2].name, ref=DirectorResourceRef(kind="npc", resource_id="draft-1")),
        ],
    )
    resolved = active.model_copy(update={
        "stage": "resolved", "resolved_turn": 2,
        "actors": [DirectorActorRef(name=profiles[3].name)],
    })
    with open_npc_profile_store() as store:
        for profile in profiles:
            store.upsert(profile)
        result = select_profiles_by_active_events(events=[active, resolved], npc_store=store)
    assert {p.npc_id for p in result} == {p.npc_id for p in profiles[:3]}


def test_profile_union_keeps_later_sources_within_limit_and_deduplicates():
    first = [NPCProfile(name=f"甲{i}") for i in range(20)]
    context_hit, event_hit = NPCProfile(name="事实命中"), NPCProfile(name="事件演员")
    result = merge_profile_selections(first, [first[0], context_hit], [event_hit])
    ids = [p.npc_id for p in result]
    assert len(ids) == 20
    assert len(set(ids)) == len(ids)
    assert {context_hit.npc_id, event_hit.npc_id} <= set(ids)
    limited = merge_profile_selections(first, [context_hit], [event_hit], limit=3)
    assert {p.npc_id for p in limited} == {first[0].npc_id, context_hit.npc_id, event_hit.npc_id}
