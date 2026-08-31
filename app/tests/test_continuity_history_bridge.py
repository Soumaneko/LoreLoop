from app.tests.factories.stores import open_continuity_store
from app.tests.factories.stores import open_confirmed_history_store
from app.tests.factories.stores import open_npc_profile_store
from app.tests.factories.stores import open_world_setting_store
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
    with open_continuity_store() as store:
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
    with open_continuity_store() as store:
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




def test_relation_hit_competes_with_direct_without_claiming_direct_match():
    relation_text = "那女人提醒他，姓周的看人不像是在看行情。"
    direct_text = "苏念薇决定继续调查山路。"
    with open_confirmed_history_store() as history_store:
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








def test_relation_only_hits_are_capped_per_continuity_item():
    links = []
    with open_confirmed_history_store() as history_store:
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




def test_context_facts_service_wires_forward_bridge_into_history_retrieval():
    class Gateway:
        def run(self, **kwargs):
            if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
                kwargs["on_attempt_start"](1)
            result = kwargs["output_schema"].model_validate({
                "turn_focus": "追问苏念薇相关旧事。",
                "facts": [],
            })
            if "output_validator" in kwargs and kwargs["output_validator"] is not None:
                kwargs["output_validator"](result)
            return result

    text = "那女人提醒他，姓周的看人不像是在看行情。"
    with (
        open_confirmed_history_store() as history_store,
        open_continuity_store() as continuity_store,
        open_world_setting_store() as setting_store,
        open_npc_profile_store(":memory:") as npc_store,
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
