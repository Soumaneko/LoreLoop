from app.runtime.stores import open_npc_profile_store
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
    def test_long_field_value_basis_does_not_crash(self):
        long_value = "北门巡检" + "甲" * 508
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [{
                "npc_id": "npc-a",
                "field": "role",
                "new_value": long_value,
                # 长 new_value 不影响 basis：basis 是短 change_basis（≤80）。
                "change_basis": "北门巡检",
                "turn": 1,
                "evidence_text": long_value,
            }])
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
            ]))

        assert len(result.links) == 1
        basis = result.links[0].evidence_basis_primary
        assert basis == "北门巡检"

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

    def test_alias_match_uses_npc_alias_reason(self):
        with open_npc_profile_store(":memory:") as store:
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
        with open_npc_profile_store(":memory:") as store:
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
        with open_npc_profile_store(":memory:") as store:
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
        with open_npc_profile_store(":memory:") as store:
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
        with open_npc_profile_store(":memory:") as store:
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

    def test_entity_intent_without_identity_hit_does_not_topic_scan(self):
        with open_npc_profile_store(":memory:") as store:
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
        with open_npc_profile_store(":memory:") as store:
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
        assert link.match_reasons == ["npc_delta_topic_match"]
        assert link.evidence_basis_primary == (
            "她从沙暴里救回整支商队，成为当地知名救援者。"
        )

    def test_pure_topic_intent_does_not_add_identity_baseline(self):
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
                ("q", "explicit_input", "", (), ("救援",)),
            ]))

        assert result.links == []

    def test_topic_matched_nodes_all_emit(self):
        # 同一 (intent, npc, field) 的 ≥3 个主题命中节点全部进入发射：
        # 每次出场都是一条独立的 appearance -> evidence 映射，不得按新旧
        # 截断为最新两个（issue 16）。
        with open_npc_profile_store(":memory:") as store:
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

        assert len(result.links) == 3
        assert sorted(link.turn_index for link in result.links) == [1, 3, 5]

    def test_identity_plus_topic_unions_and_prefers_identity(self):
        with open_npc_profile_store(":memory:") as store:
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
        assert links[1].match_reasons == ["npc_delta_topic_match"]


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

    def test_legacy_log_without_change_basis_is_incomplete(self):
        # 旧日志（无持久化 change_basis）不得伪装完整 delta：
        # basis_complete=False（incomplete，degraded rescue only）。
        # 写入 API 已拒绝为累积字段制造 NULL basis，因此 legacy 行只能来自
        # v8->v9 迁移读取；这里直接以 SQL 注入模拟迁移后的持久化状态。
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _seed_legacy_log_without_basis(
                store,
                npc_id="npc-a",
                field="role",
                new_value="北门巡检",
                turn=1,
                evidence_id="turn_1_p001",
                evidence_text="她接过铜印，正式成为北门巡检。",
                scene_id="scene_001",
            )
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("巡检",)),
            ]))

        assert len(result.links) == 1
        link = result.links[0]
        assert link.basis_complete is False
        assert link.match_reasons == ["npc_value_topic_match"]
        assert link.evidence_basis_primary == "北门巡检"

    def test_replaced_value_triggers_via_change_basis(self):
        # 替换型变化：change_basis 承载语义，retained 旧值不触发（issue 16 P1）。
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "old_value": "北门巡检",
                    "new_value": "南门巡检",
                    "change_basis": "改任南门巡检",
                    "turn": 3,
                    "evidence_text": "她奉调前往南门，出任南门巡检。",
                    "scene_id": "scene_003",
                },
            ])
            replaced = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("南门",)),
            ]))
            retained = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("北门",)),
            ]))

        assert sorted(link.turn_index for link in replaced.links) == [3]
        assert retained.links == []

    def test_removed_value_triggers_via_change_basis(self):
        # 删除型变化：被移除的职责仍可通过 change_basis 检索到；retained 不触发。
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            _logs(store, [
                {
                    "npc_id": "npc-a",
                    "field": "role",
                    "old_value": "北门巡检，兼管东市",
                    "new_value": "北门巡检",
                    "change_basis": "卸去东市巡务",
                    "turn": 5,
                    "evidence_text": "她卸去东市巡务，专心北门。",
                    "scene_id": "scene_005",
                },
            ])
            removed = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("东市",)),
            ]))
            retained = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("北门",)),
            ]))

        assert sorted(link.turn_index for link in removed.links) == [5]
        assert retained.links == []


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
    def test_identity_and_topic_nodes_all_emit_without_total_cap(self):
        # 无 total_link_cap 截断：身份命中与主题命中节点全部发射（issue 16）。
        with open_npc_profile_store(":memory:") as store:
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
            result = _bridge(store).retrieve(_plan([
                ("t", "explicit_input", "", (), ("佩剑",)),
                ("i", "explicit_input", "柳伯", (), ()),
            ]))

        # 身份命中 npc-liu 优先发射，主题命中 npc-zhang 也发射，无截断。
        assert [link.npc_id for link in result.links] == ["npc-liu", "npc-zhang"]
        assert result.matched_profile_ids == ["npc-liu", "npc-zhang"]

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

    def test_all_triggered_nodes_emit_without_total_link_cap(self):
        with open_npc_profile_store(":memory:") as store:
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
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "顾青", (), ()),
                ("r", "explicit_input", "陆衡", (), ()),
            ]))

        # 身份骨架每 npc 每字段发射最新节点：2 npc × 2 字段 = 4 条，无截断。
        assert len(result.links) == 4

    def test_multi_evidence_node_is_not_partially_emitted(self):
        # issue 16 必测 2：多 evidence 节点不得因桥级总 cap 被部分静默发射。
        with open_npc_profile_store(":memory:") as store:
            _profile(store, "npc-a", "顾青")
            turn = 3
            evidence_ids = [f"turn_3_p00{index}" for index in range(1, 5)]
            documents = [
                HistoryDocument(
                    document_id=evidence_id,
                    source_type="scene_paragraph",
                    turn_index=turn,
                    scene_id="scene_003",
                    paragraph_index=index,
                    text=f"第{index}段：她接过铜印，正式成为北门巡检。",
                )
                for index, evidence_id in enumerate(evidence_ids, start=1)
            ]
            profile = store.get_by_id("npc-a")
            assert profile is not None
            profile.role = "北门巡检"
            store.upsert(profile)
            store.record_accepted_field_changes(
                changes=[AcceptedNPCScalarChange(
                    npc_id="npc-a",
                    canonical_name="顾青",
                    field="role",
                    old_value=None,
                    new_value="北门巡检",
                    change_basis="北门巡检",
                    evidence_ids=tuple(evidence_ids),
                    turn_index=turn,
                    source="proposal",
                )],
                evidence_documents=documents,
                turn_index=turn,
                scene_id="scene_003",
            )
            result = _bridge(store).retrieve(_plan([
                ("q", "explicit_input", "", (), ("北门巡检",)),
            ]))

        assert len(result.links) == 4
        assert sorted(link.evidence_id for link in result.links) == sorted(evidence_ids)


class TestTopicMatcher:
    def test_short_word_matches_present_bigram(self):
        assert support_score("救援", "她从沙暴里救回商队，成为当地救援者。") >= 1.0

    def test_threshold_boundary(self):
        assert support_score("救援者身份", "因从沙暴中救回商队而成为当地知名救援者。") >= NPC_TOPIC_MATCH_THRESHOLD
        assert support_score("救援者身份", "接掌赤沙会并成为新任首领。") < NPC_TOPIC_MATCH_THRESHOLD

    def test_unrelated_topic_scores_zero(self):
        assert support_score("佩剑", "她接过铜印，正式成为北门巡检。") < NPC_TOPIC_MATCH_THRESHOLD