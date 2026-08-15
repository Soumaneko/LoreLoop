import hashlib

from app.domain.confirmed_history import ConfirmedHistoryStore
from app.domain.action.schemas import Anchor
from app.domain.context_facts import (
    ContextFactsInput,
    ContextFactsService,
    ContextHistoryRetriever,
    ContextSearchEntity,
    ContextSearchIntent,
    ContextSearchPlan,
    HistoryDiversityPolicy,
)
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.context_facts.views import project_context_history_payload
from app.domain.continuity import (
    ContinuityHistoryLink,
    ContinuityItem,
    ContinuityStore,
)
from app.domain.context_facts.continuity_bridge import ContinuityHistoryBridge
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.world_setting import WorldSettingStore


def _item(
    item_id: str,
    *,
    status="active",
    subject="调查陷空山探子",
    premise="查明陷空山外围探子的身份与目的。",
    entities=None,
    location="陷空山",
    turn=1,
):
    return ContinuityItem(
        id=item_id,
        kind="open_thread",
        status=status,
        subject=subject,
        premise=premise,
        current_state=None,
        entities=entities or ["苏念薇", "姚沉璧"],
        location=location,
        created_turn=turn,
        updated_turn=turn,
        closed_turn=turn if status != "active" else None,
    )


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _add_log(store, *, item_id, operation, turn, evidence_id, text):
    store.add_lifecycle_log(
        item_id=item_id,
        operation=operation,
        turn_index=turn,
        state_delta="调查取得进展。" if operation == "update" else None,
        reason="测试关系。",
        evidence=[(evidence_id, _hash(text))],
    )


def _explicit_entity_plan(name="苏念薇"):
    return ContextSearchPlan(intents=[ContextSearchIntent(
        intent_id="explicit_npc",
        focus="entity",
        origin="explicit_input",
        entities=[ContextSearchEntity(name=name, aliases=["苏姑娘"])],
    )])


def test_bridge_selects_create_and_latest_update_for_active_item():
    with ContinuityStore() as store:
        store.insert(_item("thread"))
        _add_log(
            store,
            item_id="thread",
            operation="create",
            turn=1,
            evidence_id="turn_1_p001",
            text="最初发现了外围探子。",
        )
        _add_log(
            store,
            item_id="thread",
            operation="update",
            turn=2,
            evidence_id="turn_2_p001",
            text="探子曾在山道出现。",
        )
        _add_log(
            store,
            item_id="thread",
            operation="update",
            turn=3,
            evidence_id="turn_3_p001",
            text="最新确认探子姓周。",
        )

        result = ContinuityHistoryBridge(store).retrieve(
            _explicit_entity_plan()
        )

    assert result.matched_continuity_ids == ["thread"]
    assert result.matched_active_continuities == 1
    assert result.matched_archived_continuities == 0
    assert [(link.operation, link.evidence_id) for link in result.links] == [
        ("update", "turn_3_p001"),
        ("create", "turn_1_p001"),
    ]
    assert result.links[0].triggering_intent_ids == ["explicit_npc"]
    assert "exact_entity_match" in result.links[0].match_reasons


def test_archived_item_requires_strong_trigger_and_uses_terminal_plus_create():
    location_plan = ContextSearchPlan(intents=[ContextSearchIntent(
        intent_id="current_location",
        focus="location",
        origin="current_context",
        entities=[ContextSearchEntity(name="陷空山")],
    )])
    with ContinuityStore() as store:
        store.insert(_item(
            "archived",
            status="resolved",
            entities=["苏念薇"],
            turn=3,
        ))
        _add_log(
            store,
            item_id="archived",
            operation="create",
            turn=1,
            evidence_id="turn_1_p001",
            text="最初发现探子。",
        )
        _add_log(
            store,
            item_id="archived",
            operation="resolve",
            turn=3,
            evidence_id="turn_3_p001",
            text="探子事件已经解决。",
        )

        assert ContinuityHistoryBridge(store).retrieve(
            location_plan
        ).links == []
        explicit = ContinuityHistoryBridge(store).retrieve(
            _explicit_entity_plan()
        )

    assert [(link.operation, link.evidence_id) for link in explicit.links] == [
        ("resolve", "turn_3_p001"),
        ("create", "turn_1_p001"),
    ]
    assert explicit.matched_active_continuities == 0
    assert explicit.matched_archived_continuities == 1


def test_archived_location_repetition_cannot_become_a_strong_trigger():
    plan = ContextSearchPlan(intents=[ContextSearchIntent(
        intent_id="explicit_place",
        focus="topic",
        origin="explicit_input",
        topics=["陷空山"],
    )])
    with ContinuityStore() as store:
        store.insert(_item(
            "archived-place",
            status="resolved",
            subject="陷空山旧事",
            premise="处理陷空山发生的旧事。",
            entities=["无名探子"],
            turn=3,
        ).model_copy(update={
            "current_state": "旧事仍发生在陷空山。",
        }))
        _add_log(
            store,
            item_id="archived-place",
            operation="resolve",
            turn=3,
            evidence_id="turn_3_p001",
            text="旧事结束。",
        )

        result = ContinuityHistoryBridge(store).retrieve(plan)

    assert result.matched_continuity_ids == []
    assert result.links == []


def test_archived_current_state_event_is_a_strong_trigger():
    plan = ContextSearchPlan(intents=[ContextSearchIntent(
        intent_id="explicit_event",
        focus="topic",
        origin="explicit_input",
        topics=["周姓探子刺探"],
    )])
    with ContinuityStore() as store:
        item = _item(
            "archived-event",
            status="resolved",
            subject="外围异动",
            premise="查清山外的异常动向。",
            entities=["无名探子"],
            turn=3,
        ).model_copy(update={
            "current_state": "确认周姓探子刺探宗门情报。",
        })
        store.insert(item)
        _add_log(
            store,
            item_id="archived-event",
            operation="resolve",
            turn=3,
            evidence_id="turn_3_p001",
            text="周姓探子已被处理。",
        )

        result = ContinuityHistoryBridge(store).retrieve(plan)

    assert result.matched_continuity_ids == ["archived-event"]
    assert result.links[0].match_reasons == ["current_state_topic_match"]
    assert result.links[0].eligibility_reasons == [
        "current_state_topic_match"
    ]


def test_location_improves_ranking_without_becoming_eligibility():
    plan = ContextSearchPlan(intents=[
        *_explicit_entity_plan().intents,
        ContextSearchIntent(
            intent_id="current_location",
            focus="location",
            origin="current_context",
            entities=[ContextSearchEntity(name="陷空山")],
        ),
    ])
    with ContinuityStore() as store:
        store.insert(_item(
            "a-without-location",
            entities=["苏念薇"],
            location="落雁渡",
        ))
        store.insert(_item(
            "z-with-location",
            entities=["苏念薇"],
            location="陷空山",
        ))
        for item_id in ("a-without-location", "z-with-location"):
            _add_log(
                store,
                item_id=item_id,
                operation="create",
                turn=1,
                evidence_id=f"{item_id}_evidence",
                text=f"{item_id} 正文。",
            )

        result = ContinuityHistoryBridge(
            store,
            active_limit=1,
        ).retrieve(plan)

    assert result.matched_continuity_ids == ["z-with-location"]
    assert "location_match" in result.links[0].match_reasons
    assert "location_match" not in result.links[0].eligibility_reasons


def test_bridge_emits_every_evidence_seed_in_persisted_position_order():
    texts = ["第一条证据。", "第二条证据。", "第三条证据。"]
    with ContinuityStore() as store:
        store.insert(_item("multi-evidence", entities=["苏念薇"]))
        store.add_lifecycle_log(
            item_id="multi-evidence",
            operation="create",
            turn_index=1,
            state_delta=None,
            reason="多段正文共同建立事项。",
            evidence=[
                (f"turn_1_p00{index}", _hash(text))
                for index, text in enumerate(texts, start=1)
            ],
        )

        result = ContinuityHistoryBridge(store).retrieve(
            _explicit_entity_plan()
        )

    assert [link.evidence_id for link in result.links] == [
        "turn_1_p001",
        "turn_1_p002",
        "turn_1_p003",
    ]
    assert [link.evidence_position for link in result.links] == [0, 1, 2]


def test_relation_hit_competes_with_direct_without_claiming_direct_match():
    relation_text = "那女人提醒他，姓周的看人不像是在看行情。"
    direct_text = "苏念薇决定继续调查山路。"
    with ConfirmedHistoryStore() as history_store:
        history_store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="",
            scene_text=relation_text,
        )
        history_store.upsert_turn(
            scene_id="scene_002",
            turn_index=2,
            user_input="",
            scene_text=direct_text,
        )
        link = ContinuityHistoryLink(
            continuity_id="thread",
            continuity_status="active",
            operation="update",
            evidence_id="turn_1_p001",
            evidence_hash=_hash(relation_text),
            triggering_intent_ids=["explicit_npc"],
            match_reasons=["exact_entity_match"],
            evidence_position=0,
            evidence_basis_primary="确认周姓男子看人不像在看行情。",
            basis_complete=True,
        )
        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(max_hits=2),
        ).retrieve(
            _explicit_entity_plan(),
            current_turn_index=3,
            continuity_links=[link],
        )

    assert [hit.evidence_id for hit in result.hits] == [
        "turn_2_p001",
        "turn_1_p001",
    ]
    direct, relation = result.hits
    assert direct.matched_intent_ids == ["explicit_npc"]
    assert direct.relation_intent_ids == []
    assert relation.matched_intent_ids == []
    assert relation.relation_intent_ids == ["explicit_npc"]
    assert relation.linked_continuity_ids == ["thread"]
    assert result.relation_hits_added == 1
    projected = project_context_history_payload(result)
    assert "relation_intent_ids" not in projected[1]
    assert "linked_continuity_ids" not in projected[1]


def test_degraded_weak_relation_not_rate_limited_by_intent_target():
    # issue 17: weak (degraded) relation evidence competes like any other
    # proposal; the old per-intent rescue quota is gone. Three weak relations
    # for the same intent are each eligible and selected up to max_hits.
    links = []
    with ConfirmedHistoryStore() as history_store:
        for turn in range(1, 4):
            text = f"那女人在第{turn}次会面留下了线索。"
            history_store.upsert_turn(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                user_input="",
                scene_text=text,
            )
            links.append(ContinuityHistoryLink(
                continuity_id=f"thread-{turn}",
                continuity_status="active",
                operation="create",
                evidence_id=f"turn_{turn}_p001",
                evidence_hash=_hash(text),
                triggering_intent_ids=["explicit_npc"],
                match_reasons=["exact_entity_match"],
                evidence_position=0,
                evidence_basis_primary="他们稍后会面。",
                basis_complete=True,
            ))

        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(max_hits=10),
        ).retrieve(
            _explicit_entity_plan(),
            current_turn_index=6,
            continuity_links=links,
        )

    assert result.relation_candidates_considered == 3
    assert result.relation_hits_added == 3
    assert len(result.hits) == 3


def test_strong_link_carries_degraded_attribution_beyond_rescue_cap():
    plan = ContextSearchPlan(intents=[
        ContextSearchIntent(
            intent_id="intent_a",
            focus="entity",
            origin="explicit_input",
            entities=[ContextSearchEntity(name="甲")],
        ),
        ContextSearchIntent(
            intent_id="intent_b",
            focus="entity",
            origin="explicit_input",
            entities=[ContextSearchEntity(name="乙")],
        ),
    ])
    links = []
    with ConfirmedHistoryStore() as history_store:
        for turn in range(1, 4):
            text = f"那女人在第{turn}次会面留下了线索。"
            history_store.upsert_turn(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                user_input="",
                scene_text=text,
            )
            links.append(ContinuityHistoryLink(
                continuity_id=f"item-a-{turn}",
                continuity_status="active",
                operation="create",
                evidence_id=f"turn_{turn}_p001",
                evidence_hash=_hash(text),
                triggering_intent_ids=["intent_a"],
                match_reasons=["exact_entity_match"],
                evidence_position=0,
                evidence_basis_primary=text,
                basis_complete=True,
            ))
            links.append(ContinuityHistoryLink(
                continuity_id=f"item-b-{turn}",
                continuity_status="active",
                operation="create",
                evidence_id=f"turn_{turn}_p001",
                evidence_hash=_hash(text),
                triggering_intent_ids=["intent_b"],
                match_reasons=["exact_entity_match"],
                evidence_position=0,
                evidence_basis_primary="他们稍后会面。",
                basis_complete=True,
            ))

        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(max_hits=10),
        ).retrieve(
            plan,
            current_turn_index=6,
            continuity_links=links,
        )

    assert result.relation_hits_added == 3
    assert len(result.hits) == 3
    assert all(
        any(hit.evidence_id == f"turn_{turn}_p001" for hit in result.hits)
        for turn in range(1, 4)
    )
    for hit in result.hits:
        assert any(
            item_id.startswith("item-a-")
            for item_id in hit.linked_continuity_ids
        )
        assert any(
            item_id.startswith("item-b-")
            for item_id in hit.linked_continuity_ids
        )


def test_relation_only_hits_are_not_globally_capped_and_hash_mismatch_is_skipped():
    links = []
    with ConfirmedHistoryStore() as history_store:
        for turn in range(1, 6):
            text = f"那女人在第{turn}次会面留下了不同线索。"
            history_store.upsert_turn(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                user_input="",
                scene_text=text,
            )
            links.append(ContinuityHistoryLink(
                continuity_id=f"thread-{turn}",
                continuity_status="active",
                operation="create",
                evidence_id=f"turn_{turn}_p001",
                evidence_hash=_hash(text),
                triggering_intent_ids=["explicit_npc"],
                match_reasons=["exact_entity_match"],
                evidence_position=0,
                evidence_basis_primary=text,
                basis_complete=True,
            ))
        links.append(ContinuityHistoryLink(
            continuity_id="stale",
            continuity_status="active",
            operation="create",
            evidence_id="turn_5_p001",
            evidence_hash="0" * 64,
            triggering_intent_ids=["explicit_npc"],
            match_reasons=["exact_entity_match"],
            evidence_position=0,
            evidence_basis_primary="那女人在第五次会面留下了线索。",
            basis_complete=True,
        ))
        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(max_hits=10),
        ).retrieve(
            _explicit_entity_plan(),
            current_turn_index=6,
            continuity_links=links,
        )

    assert len(result.hits) == 5
    assert all(not hit.matched_intent_ids for hit in result.hits)
    assert result.relation_candidates_considered == 5
    assert result.valid_relation_links == 5
    assert result.relation_hits_added == 5
    assert result.stale_relation_links == 1


def test_relation_only_hits_are_capped_per_continuity_item():
    links = []
    with ConfirmedHistoryStore() as history_store:
        for turn in range(1, 6):
            text = f"那女人留下了第{turn}条同一事项的证据。"
            history_store.upsert_turn(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                user_input="",
                scene_text=text,
            )
            links.append(ContinuityHistoryLink(
                continuity_id="one-thread",
                continuity_status="active",
                operation="update",
                evidence_id=f"turn_{turn}_p001",
                evidence_hash=_hash(text),
                triggering_intent_ids=["explicit_npc"],
                match_reasons=["exact_entity_match"],
                evidence_position=turn - 1,
                evidence_basis_primary=text,
                basis_complete=True,
            ))

        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(
                max_hits=10,
                relation_owner_cap=3,
            ),
        ).retrieve(
            _explicit_entity_plan(),
            current_turn_index=6,
            continuity_links=links,
        )

    assert [hit.evidence_id for hit in result.hits] == [
        "turn_5_p001",
        "turn_4_p001",
        "turn_3_p001",
    ]
    assert result.relation_candidates_considered == 5
    assert result.relation_hits_added == 3


def test_shared_relation_evidence_counts_against_every_linked_item_cap():
    # 共享证据（turn_1）以最高 node-evidence support 入选并同时计入两个
    # 事项的 owner cap；质量次一档的各自证据按 recency 决胜（issue 16）。
    texts = {
        "turn_1_p001": "共同证据。",
        "turn_2_p001": "甲事项自己的第一条证据。",
        "turn_3_p001": "甲事项自己的第二条证据。",
        "turn_4_p001": "乙事项自己的第一条证据。",
        "turn_5_p001": "乙事项自己的第二条证据。",
    }
    bases = {
        "turn_1_p001": "共同证据。",
        "turn_2_p001": "甲事项自有证据。",
        "turn_3_p001": "甲事项自有证据。",
        "turn_4_p001": "乙事项自有证据。",
        "turn_5_p001": "乙事项自有证据。",
    }
    with ConfirmedHistoryStore() as history_store:
        for turn, text in enumerate(texts.values(), start=1):
            history_store.upsert_turn(
                scene_id=f"scene_{turn:03d}",
                turn_index=turn,
                user_input="",
                scene_text=text,
            )

        def link(item_id: str, evidence_id: str, position: int):
            return ContinuityHistoryLink(
                continuity_id=item_id,
                continuity_status="active",
                operation="update",
                evidence_id=evidence_id,
                evidence_hash=_hash(texts[evidence_id]),
                triggering_intent_ids=["explicit_npc"],
                match_reasons=["exact_entity_match"],
                evidence_position=position,
                evidence_basis_primary=bases[evidence_id],
                basis_complete=True,
            )

        result = ContextHistoryRetriever(
            history_store,
            diversity_policy=HistoryDiversityPolicy(
                max_hits=10,
                relation_owner_cap=2,
            ),
        ).retrieve(
            _explicit_entity_plan(),
            current_turn_index=6,
            continuity_links=[
                link("thread-a", "turn_1_p001", 0),
                link("thread-b", "turn_1_p001", 0),
                link("thread-a", "turn_2_p001", 1),
                link("thread-a", "turn_3_p001", 2),
                link("thread-b", "turn_4_p001", 1),
                link("thread-b", "turn_5_p001", 2),
            ],
        )

    assert [hit.evidence_id for hit in result.hits] == [
        "turn_1_p001",
        "turn_5_p001",
        "turn_3_p001",
    ]
    assert result.hits[0].linked_continuity_ids == ["thread-a", "thread-b"]
    assert result.relation_candidates_considered == 5
    assert result.relation_hits_added == 3


def test_context_facts_service_wires_forward_bridge_into_history_retrieval():
    class Gateway:
        def run(self, **kwargs):
            return kwargs["output_schema"].model_validate({
                "turn_focus": "追问苏念薇相关旧事。",
                "facts": [],
            })

    text = "那女人提醒他，姓周的看人不像是在看行情。"
    with (
        ConfirmedHistoryStore() as history_store,
        ContinuityStore() as continuity_store,
        WorldSettingStore() as setting_store,
        NPCProfileStore(":memory:") as npc_store,
    ):
        history_store.upsert_turn(
            scene_id="scene_001",
            turn_index=1,
            user_input="",
            scene_text=text,
        )
        continuity_store.insert(_item(
            "thread",
            premise="确认周姓男子看人不像在看行情。",
            entities=["苏念薇"],
        ))
        _add_log(
            continuity_store,
            item_id="thread",
            operation="create",
            turn=1,
            evidence_id="turn_1_p001",
            text=text,
        )

        result = ContextFactsService(
            Gateway(),
            history_store,
            continuity_store,
            setting_store,
            npc_store,
        ).build(ContextFactsInput(
            user_input="回想苏念薇与姓周之人的事情。",
            core_memory=CoreMemory(items=[]),
            current_state=CurrentState(
                scene=SceneState(scene_id="scene_002", location="山脚"),
                player=PlayerState(name="萧欢"),
            ),
            matched_anchors=[Anchor(
                text="苏念薇",
                normalized="苏念薇",
                kind="character",
                source="explicit",
                confidence=1,
            )],
            current_turn_index=2,
        ))

    hit = result.history_evidence.hits[0]
    assert hit.evidence_id == "turn_1_p001"
    assert hit.matched_intent_ids == []
    assert hit.relation_intent_ids
    assert result.history_evidence.matched_active_continuities == 1
    assert result.history_evidence.matched_archived_continuities == 0
    assert result.history_evidence.valid_relation_links == 1
