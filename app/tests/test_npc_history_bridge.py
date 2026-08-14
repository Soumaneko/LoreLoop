import pytest
from collections import defaultdict

from app.domain.confirmed_history.schemas import HistoryDocument
from app.domain.confirmed_history.text import support_score
from app.domain.context_facts.schemas import (
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
)
from app.domain.npc_profile.changes import AcceptedNPCScalarChange
from app.domain.npc_profile.history_bridge import (
    NPCFieldHistoryLink,
    NPCProfileHistoryBridge,
    NPC_TOPIC_MATCH_THRESHOLD,
)
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore


def _profile(
    store: NPCProfileStore,
    npc_id: str,
    name: str,
    *,
    aliases=(),
    transient_aliases=(),
    active=True,
    role=None,
    faction=None,
    appearance=None,
) -> None:
    store.upsert(NPCProfile(
        npc_id=npc_id,
        name=name,
        aliases=list(aliases),
        transient_aliases=dict(transient_aliases),
        active=active,
        role=role,
        faction=faction,
        appearance=appearance,
    ))


def _logs(
    store: NPCProfileStore,
    logs,
) -> None:
    """Record one turn of evidence-backed field logs through the real store."""
    by_turn: dict[int, list[dict]] = defaultdict(list)
    for log in logs:
        by_turn[log["turn"]].append(log)
    for turn in sorted(by_turn):
        turn_logs = by_turn[turn]
        for log in turn_logs:
            profile = store.get_by_id(log["npc_id"])
            assert profile is not None
            setattr(profile, log["field"], log["new_value"])
            if log["field"] == "last_scene_summary":
                profile.last_scene_turn = turn
            store.upsert(profile)
        changes = []
        documents = []
        for index, log in enumerate(turn_logs):
            evidence_id = f"turn_{turn}_p{index + 1:03d}"
            document = HistoryDocument(
                document_id=evidence_id,
                source_type="scene_paragraph",
                turn_index=turn,
                scene_id=log.get("scene_id", "scene_001"),
                paragraph_index=index + 1,
                text=log["evidence_text"],
            )
            documents.append(document)
            profile = store.get_by_id(log["npc_id"])
            assert profile is not None
            changes.append(AcceptedNPCScalarChange(
                npc_id=log["npc_id"],
                canonical_name=profile.name,
                field=log["field"],
                old_value=log.get("old_value"),
                new_value=log["new_value"],
                evidence_ids=(evidence_id,),
                turn_index=turn,
                source="proposal",
            ))
        scene_id = next(
            log.get("scene_id", "scene_001") for log in turn_logs
        )
        store.record_accepted_field_changes(
            changes=changes,
            evidence_documents=documents,
            turn_index=turn,
            scene_id=scene_id,
        )


def _plan(intents):
    built = []
    for intent_id, origin, name, aliases, topics in intents:
        entities = (
            [ContextSearchEntity(name=name, aliases=list(aliases))]
            if name
            else []
        )
        built.append(ContextSearchIntent(
            intent_id=intent_id,
            focus="entity",
            origin=origin,
            entities=entities,
            topics=list(topics),
        ))
    return ContextSearchPlan(intents=built)


def _bridge(store, **kwargs) -> NPCProfileHistoryBridge:
    return NPCProfileHistoryBridge(store, **kwargs)


class TestIdentity:
    def test_long_field_value_basis_does_not_crash(self):
        long_value = "北门巡检" + "甲" * 508
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": long_value,
                "turn": 1,
                "evidence_text": long_value,
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
            ]))

        assert len(result.links) == 1
        basis = result.links[0].evidence_basis_primary
        assert basis == long_value
        assert len(basis or "") == len(long_value)

    def test_name_match_emits_latest_field_node(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": "北门巡检",
                "turn": 1,
                "evidence_text": "她接过铜印，正式成为北门巡检。",
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
            ]))

        assert result.matched_profile_ids == ["npc-a"]
        assert len(result.links) == 1
        link = result.links[0]
        assert link.field_name == "role"
        assert link.match_reasons == ["npc_name_match"]
        assert link.evidence_basis_primary == "北门巡检"
        assert link.basis_complete is True

    def test_alias_match_uses_npc_alias_reason(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青", aliases=("阿青",))
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": "北门巡检",
                "turn": 1,
                "evidence_text": "她接过铜印，正式成为北门巡检。",
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "阿青", (), ()),
            ]))

        assert result.links[0].match_reasons == ["npc_alias_match"]

    def test_transient_alias_is_not_a_history_index(self):
        with NPCProfileStore(":memory:") as store:
            _profile(
                store,
                "npc-a",
                "顾青",
                transient_aliases=(("阿青", 3),),
            )
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": "北门巡检",
                "turn": 1,
                "evidence_text": "她接过铜印，正式成为北门巡检。",
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "阿青", (), ()),
            ]))

        assert result.links == []

    def test_identity_conflict_emits_every_profile_deterministically(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-b", "顾青")
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": "北门巡检",
                    "turn": 1,
                    "evidence_text": "甲正式成为北门巡检。",
                },
                {
                    "npc_id": "npc-b",
                    "field": "role",
                    "new_value": "北门巡检",
                    "turn": 1,
                    "evidence_text": "乙正式成为北门巡检。",
                },
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
            ]))

        assert result.matched_profile_ids == ["npc-a", "npc-b"]
        assert {link.npc_id for link in result.links} == {"npc-a", "npc-b"}

    def test_inactive_profile_requires_explicit_identity(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-liu", "柳伯", active=False)
            _logs(store, [{
                "npc_id": "npc-liu",
                "field": "role",
                "new_value": "旧渡口账房",
                "turn": 1,
                "evidence_text": "他被推举为旧渡口账房。",
            }])
            context_only = _bridge(store).retrieve(_plan([
                ("q", "current_context", "柳伯", (), ()),
            ]))
            explicit = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "柳伯", (), ()),
            ]))

        assert context_only.links == []
        assert len(explicit.links) == 1

    def test_identity_takes_latest_node_per_field(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": "守门人",
                    "turn": 1,
                    "evidence_text": "她当上守门人。",
                    "scene_id": "scene_001",
                },
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": "北门巡检",
                    "turn": 5,
                    "evidence_text": "她升任北门巡检。",
                    "scene_id": "scene_005",
                },
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
            ]))

        assert len(result.links) == 1
        assert result.links[0].turn_index == 5
        assert result.links[0].evidence_basis_primary == "北门巡检"


class TestTopic:
    def test_entity_topic_intent_is_scoped_to_identity(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-liulan", "柳岚")
            _profile(store, "npc-zhang", "张三")
            _logs(store, [
                {
                    "npc_id": "npc-liulan",
                    "field": "faction",
                    "new_value": "白鹿堂",
                    "turn": 1,
                    "evidence_text": "她正式加入白鹿堂。",
                },
                {
                    "npc_id": "npc-zhang",
                    "field": "role",
                    "new_value": "佩剑教习",
                    "turn": 2,
                    "evidence_text": "张三成为佩剑教习。",
                    "scene_id": "scene_002",
                },
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "柳岚", (), ("佩剑",)),
            ]))

        assert {link.npc_id for link in result.links} == {"npc-liulan"}

    def test_entity_intent_without_identity_hit_does_not_topic_scan(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-zhang", "张三")
            _logs(store, [{
                "npc_id": "npc-zhang",
                "field": "role",
                "new_value": "佩剑教习",
                "turn": 1,
                "evidence_text": "张三成为佩剑教习。",
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "刘青", (), ("佩剑",)),
            ]))

        assert result.links == []

    def test_value_topic_match_only_emits_matched_last_scene_node(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-shalan", "沙岚")
            _logs(store, [
                {
                    "npc_id": "npc-shalan",
                    "field": "last_scene_summary",
                    "new_value": "她在盟会推举中接过赤沙会令牌。",
                    "turn": 1,
                    "evidence_text": "她在盟会推举中接过赤沙会令牌。",
                    "scene_id": "scene_001",
                },
                {
                    "npc_id": "npc-shalan",
                    "field": "last_scene_summary",
                    "new_value": "她从沙暴里救回整支商队，成为当地知名救援者。",
                    "turn": 2,
                    "evidence_text": "她从沙暴里救回整支商队。",
                    "scene_id": "scene_002",
                },
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("救援者身份",)),
            ]))

        assert len(result.links) == 1
        link = result.links[0]
        assert link.turn_index == 2
        assert link.match_reasons == ["npc_value_topic_match"]
        assert link.evidence_basis_primary == (
            "她从沙暴里救回整支商队，成为当地知名救援者。"
        )

    def test_pure_topic_intent_does_not_add_identity_baseline(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": "北门巡检",
                "turn": 1,
                "evidence_text": "她接过铜印，正式成为北门巡检。",
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("救援",)),
            ]))

        assert result.links == []

    def test_topic_add_back_bounded_per_field(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": f"守门人{index}",
                    "turn": index,
                    "evidence_text": f"第{index}次守门记录。",
                    "scene_id": f"scene_{index:03d}",
                }
                for index in (1, 3, 5)
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("守门",)),
            ]))

        assert len(result.links) == 2
        assert sorted(link.turn_index for link in result.links) == [3, 5]

    def test_identity_plus_topic_unions_and_prefers_identity(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": "守门人",
                    "turn": 1,
                    "evidence_text": "她当上守门人。",
                    "scene_id": "scene_001",
                },
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "new_value": "北门巡检",
                    "turn": 5,
                    "evidence_text": "她升任北门巡检。",
                    "scene_id": "scene_005",
                },
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ("守门",)),
            ]))

        links = {link.turn_index: link for link in result.links}
        assert set(links) == {1, 5}
        assert links[5].match_reasons == ["npc_name_match"]
        assert links[1].match_reasons == ["npc_value_topic_match"]


class TestMultiIntent:
    def test_per_intent_reasons_preserved_on_shared_node(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": "北门巡检",
                "turn": 5,
                "evidence_text": "她升任北门巡检。",
                "scene_id": "scene_005",
            }])
            result = _bridge(store).retrieve(_plan([
                ("a", "explicit_input", "顾青", (), ()),
                ("b", "explicit_input", "", (), ("巡检",)),
            ]))

        assert len(result.links) == 1
        link = result.links[0]
        assert link.triggering_intent_ids == ["a", "b"]
        assert link.match_reasons == [
            "npc_name_match",
            "npc_value_topic_match",
        ]
        assert link.intent_match_reasons == {
            "a": ["npc_name_match"],
            "b": ["npc_value_topic_match"],
        }


class TestEmission:
    def test_identity_priority_survives_topic_filled_cap(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-zhang", "张三")
            _profile(store, "npc-liu", "柳伯")
            _logs(store, [
                {
                    "npc_id": "npc-zhang",
                    "field": "role",
                    "new_value": "佩剑教习",
                    "turn": 1,
                    "evidence_text": "张三成为佩剑教习。",
                },
                {
                    "npc_id": "npc-liu",
                    "field": "role",
                    "new_value": "旧渡口账房",
                    "turn": 2,
                    "evidence_text": "他成为旧渡口账房。",
                    "scene_id": "scene_002",
                },
            ])
            result = _bridge(store, total_link_cap=1).retrieve(_plan([
                ("t", "explicit_input", "", (), ("佩剑",)),
                ("i", "explicit_input", "柳伯", (), ()),
            ]))

        assert [link.npc_id for link in result.links] == ["npc-liu"]
        assert result.matched_profile_ids == ["npc-liu"]

    def test_deterministic_newest_first_order(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-qin", "秦霜")
            _logs(store, [
                {
                    "npc_id": "npc-qin",
                    "field": field,
                    "new_value": value,
                    "turn": turn,
                    "evidence_text": text,
                    "scene_id": f"scene_{turn:03d}",
                }
                for turn, field, value, text in (
                    (1, "role", "黑羽卫斥候", "她成为黑羽卫斥候。"),
                    (2, "faction", "黑羽卫", "她归属黑羽卫。"),
                    (3, "appearance", "左颊有新月形伤痕", "她左颊有伤痕。"),
                    (4, "last_scene_summary", "她以护送商队闻名。", "她以护送商队闻名。"),
                )
            ])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "秦霜", (), ()),
            ]))

        assert [link.turn_index for link in result.links] == [4, 3, 2, 1]

    def test_total_link_cap_is_applied(self):
        with NPCProfileStore(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _profile(store, "npc-b", "陆衡")
            logs = []
            for npc_id in ("npc-a", "npc-b"):
                for turn, (field, value) in enumerate(
                    (("role", "巡检"), ("faction", "黑羽卫")),
                    start=1,
                ):
                    logs.append({
                        "npc_id": npc_id,
                        "field": field,
                        "new_value": value,
                        "turn": turn,
                        "evidence_text": f"{npc_id}第{turn}次记录。",
                        "scene_id": f"scene_{turn:03d}",
                    })
            _logs(store, logs)
            result = _bridge(
                store,
                total_link_cap=3,
            ).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
                ("r", "explicit_input", "陆衡", (), ()),
            ]))

        assert len(result.links) == 3


class TestTopicMatcher:
    def test_short_word_matches_present_bigram(self):
        assert support_score("救援", "她从沙暴里救回商队，成为当地救援者。") >= 1.0

    def test_threshold_boundary(self):
        assert support_score("救援者身份", "因从沙暴中救回商队而成为当地知名救援者。") >= NPC_TOPIC_MATCH_THRESHOLD
        assert support_score("救援者身份", "接掌赤沙会并成为新任首领。") < NPC_TOPIC_MATCH_THRESHOLD

    def test_unrelated_topic_scores_zero(self):
        assert support_score("佩剑", "她接过铜印，正式成为北门巡检。") < NPC_TOPIC_MATCH_THRESHOLD
