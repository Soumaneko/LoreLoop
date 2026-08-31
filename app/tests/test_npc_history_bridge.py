from app.tests.factories.stores import open_npc_profile_store
import pytest
from collections import defaultdict

from app.domain.confirmed_history import history_document_text_hash
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
                # 生产 writer 对累积稳定字段必写 change_basis；fixture 省略时
                # 默认取整值（首设 basis），显式传 None 走 legacy 降级路径。
                change_basis=(
                    log["new_value"]
                    if log["field"] in ("role", "appearance", "faction")
                    else None
                ) if "change_basis" not in log else log["change_basis"],
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


def _seed_legacy_log_without_basis(
    store: NPCProfileStore,
    *,
    npc_id: str,
    field: str,
    new_value: str,
    turn: int,
    evidence_id: str,
    evidence_text: str,
    scene_id: str = "scene_001",
) -> None:
    """直接以 SQL 注入一条无 change_basis 的 legacy 日志行。

    写入 API（record_accepted_field_changes）现在拒绝为累积稳定字段制造 NULL
    basis；legacy NULL 只允许来自 v8->v9 迁移读取。此 helper 模拟迁移后的持久化
    状态，让桥的降级读取路径保持可测。
    """
    profile = store.get_by_id(npc_id)
    assert profile is not None
    cursor = store._db.execute(
        """
        INSERT INTO npc_field_change_logs(
            npc_id, field_name, turn_index, old_value, new_value,
            change_basis, summary_delta, source
        ) VALUES (?, ?, ?, NULL, ?, NULL, NULL, 'proposal')
        """,
        (npc_id, field, turn, new_value),
    )
    log_id = int(cursor.lastrowid)
    document = HistoryDocument(
        document_id=evidence_id,
        source_type="scene_paragraph",
        turn_index=turn,
        scene_id=scene_id,
        paragraph_index=1,
        text=evidence_text,
    )
    store._db.execute(
        """
        INSERT INTO npc_field_change_evidence(
            log_id, position, evidence_id, evidence_hash
        ) VALUES (?, 0, ?, ?)
        """,
        (log_id, evidence_id, history_document_text_hash(document)),
    )


class TestIdentity:

    def test_name_match_emits_latest_field_node(self):
        with open_npc_profile_store(":memory:") as store:
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







class TestTopic:
    def test_entity_topic_intent_is_scoped_to_identity(self):
        with open_npc_profile_store(":memory:") as store:
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







    def test_retained_old_fact_does_not_trigger_later_node(self):
        # issue 16 缺陷 2：累积字段的 retained 旧事实不得触发只改新内容的
        # 后续节点；本次新增事实仍触发并桥接其 evidence（持久化 change_basis）。
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "old_value": None,
                    "new_value": "北门巡检",
                    "change_basis": "出任北门巡检",
                    "turn": 1,
                    "evidence_text": "她接过铜印，正式成为北门巡检。",
                    "scene_id": "scene_001",
                },
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "old_value": "北门巡检",
                    "new_value": "北门巡检，兼管东市",
                    "change_basis": "新增兼管东市职责",
                    "turn": 5,
                    "evidence_text": "她接手东市巡务，兼管东市。",
                    "scene_id": "scene_005",
                },
            ])
            retained = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("北门巡检",)),
            ]))
            new_fact = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("东市",)),
            ]))

        # retained 旧事实只命中建立它的 turn 1 节点，不命中 turn 5；
        # 新事实命中 turn 5 节点。
        assert sorted(link.turn_index for link in retained.links) == [1]
        assert sorted(link.turn_index for link in new_fact.links) == [5]





class TestMultiIntent:
    def test_per_intent_reasons_preserved_on_shared_node(self):
        with open_npc_profile_store(":memory:") as store:
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
            "npc_delta_topic_match",
            "npc_name_match",
        ]
        assert link.intent_match_reasons == {
            "a": ["npc_name_match"],
            "b": ["npc_delta_topic_match"],
        }


class TestEmission:

    def test_deterministic_newest_first_order(self):
        with open_npc_profile_store(":memory:") as store:
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




class TestTopicMatcher:
    def test_short_word_matches_present_bigram(self):
        assert support_score("救援", "她从沙暴里救回商队，成为当地救援者。") >= 1.0
