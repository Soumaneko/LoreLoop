import pytest

from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.current_state import WorldClock
from app.domain.director import DirectorResourceRef
from app.domain.director_event.schemas import DirectorActorRef, DirectorEvent
from app.domain.npc_profile.schemas import NPCProfile
from app.graph.generation_profile_helpers import (
    merge_profile_selections,
    select_profiles_by_active_events,
    select_profiles_by_context_facts,
)


def _profile_store(tmp_path, profiles):
    from app.domain.npc_profile.store import NPCProfileStore

    store = NPCProfileStore(str(tmp_path / "npcs.db"))
    for profile in profiles:
        store.upsert(profile)
    return store


def _facts(texts: list[str]) -> ContextFactSet:
    return ContextFactSet(
        turn_focus="围绕昨夜码头的事展开。",
        facts=[
            ContextFact(fact_id=f"f{index}", fact=text)
            for index, text in enumerate(texts, start=1)
        ],
    )


def test_select_profiles_by_context_facts_matches_fact_texts(tmp_path):
    store = _profile_store(tmp_path, [
        NPCProfile(name="顾晚棠", aliases=["顾阁主"], role="万象阁阁主"),
        NPCProfile(name="柴行简", role="商队执事"),
        NPCProfile(name="林澈", role="社区医生"),
    ])
    try:
        result = select_profiles_by_context_facts(
            context_facts=_facts([
                "盐帮追兵仍在客栈外搜寻顾晚棠。",
                "柴行简商队昨夜通过了旧桥。",
            ]),
            npc_store=store,
        )
    finally:
        store.close()

    assert {profile.name for profile in result} == {"顾晚棠", "柴行简"}


def test_select_profiles_by_context_facts_matches_alias_and_turn_focus(tmp_path):
    store = _profile_store(tmp_path, [
        NPCProfile(name="希尔达", aliases=["委托人"], role="情报商人"),
        NPCProfile(name="无关路人", role="路人"),
    ])
    try:
        matched_turn_focus = select_profiles_by_context_facts(
            context_facts=ContextFactSet(
                turn_focus="主角向委托人交付旧书。",
                facts=[],
            ),
            npc_store=store,
        )
        matched_alias = select_profiles_by_context_facts(
            context_facts=_facts(["委托人早已在城门口等候。"]),
            npc_store=store,
        )
    finally:
        store.close()

    assert [profile.name for profile in matched_turn_focus] == ["希尔达"]
    assert [profile.name for profile in matched_alias] == ["希尔达"]


def test_select_profiles_by_context_facts_empty_when_no_facts(tmp_path):
    store = _profile_store(tmp_path, [NPCProfile(name="顾晚棠")])
    try:
        result = select_profiles_by_context_facts(
            context_facts=None,
            npc_store=store,
        )
    finally:
        store.close()

    assert result == []


def test_select_profiles_by_active_events_resolves_actor_refs(tmp_path):
    store = _profile_store(tmp_path, [
        NPCProfile(name="柴行简", role="商队执事"),
        NPCProfile(name="林澈", role="社区医生"),
        NPCProfile(name="无关路人", role="路人"),
    ])
    try:
        ids = {profile.name: profile.npc_id for profile in store.list_all()}
        events = [
            DirectorEvent(
                role="arc",
                title="商路封锁",
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
                        ref=DirectorResourceRef(
                            kind="npc",
                            resource_id=ids["柴行简"],
                        ),
                    ),
                    DirectorActorRef(name="林澈"),
                ],
            ),
            DirectorEvent(
                role="ambient",
                title="已解决事件",
                subject="官府",
                intent="清剿",
                method="围捕",
                macro_intent="恢复秩序",
                stage="resolved",
                deadline_clock=WorldClock().advance_phase(1),
                created_turn=1,
                updated_turn=2,
                resolved_turn=2,
                actors=[
                    DirectorActorRef(
                        name="无关路人",
                        ref=DirectorResourceRef(
                            kind="npc",
                            resource_id=ids["无关路人"],
                        ),
                    ),
                ],
            ),
        ]
        result = select_profiles_by_active_events(
            events=events,
            npc_store=store,
        )
    finally:
        store.close()

    assert {profile.name for profile in result} == {"柴行简", "林澈"}


def test_select_profiles_by_active_events_resolves_director_draft_refs(tmp_path):
    store = _profile_store(tmp_path, [
        NPCProfile(
            name="江蓠",
            role="茶馆老板娘",
            source_director_npc_id="director_npc_7f3a",
        ),
    ])
    try:
        events = [
            DirectorEvent(
                role="ambient",
                title="茶馆密谈",
                subject="江蓠",
                intent="传递消息",
                method="借茶客之名密会",
                macro_intent="推动谍报网络",
                deadline_clock=WorldClock().advance_phase(2),
                created_turn=1,
                updated_turn=1,
                actors=[
                    DirectorActorRef(
                        name="江蓠",
                        ref=DirectorResourceRef(
                            kind="npc",
                            resource_id="director_npc_7f3a",
                        ),
                    ),
                ],
            ),
        ]
        result = select_profiles_by_active_events(
            events=events,
            npc_store=store,
        )
    finally:
        store.close()

    assert [profile.name for profile in result] == ["江蓠"]


def test_merge_profile_selections_dedupes_and_respects_limit():
    a = NPCProfile(name="甲")
    b = NPCProfile(name="乙")
    c = NPCProfile(name="丙")

    merged = merge_profile_selections(
        [a, b],
        [b, c],
    )
    assert [profile.npc_id for profile in merged] == [a.npc_id, b.npc_id, c.npc_id]

    limited = merge_profile_selections(
        [a, b],
        [b, c],
        limit=2,
    )
    assert [profile.npc_id for profile in limited] == [a.npc_id, b.npc_id]


def test_merge_profile_selections_accepts_empty():
    a = NPCProfile(name="甲")
    assert merge_profile_selections([], [], [a]) == [a]
    assert merge_profile_selections() == []


def test_merge_profile_selections_round_robins_across_sources():
    a = NPCProfile(name="甲")
    b = NPCProfile(name="乙")
    c = NPCProfile(name="丙")
    d = NPCProfile(name="丁")
    e = NPCProfile(name="戊")

    merged = merge_profile_selections(
        [a, b, c],
        [d],
        [e],
        limit=3,
    )
    assert [profile.npc_id for profile in merged] == [a.npc_id, d.npc_id, e.npc_id]


def test_merge_profile_selections_does_not_starve_later_sources():
    first = [NPCProfile(name=f"甲{i}") for i in range(20)]
    context_hit = NPCProfile(name="事实命中")
    event_hit = NPCProfile(name="事件演员")

    merged = merge_profile_selections(
        first,
        [context_hit],
        [event_hit],
    )

    ids = {profile.npc_id for profile in merged}
    assert context_hit.npc_id in ids
    assert event_hit.npc_id in ids
