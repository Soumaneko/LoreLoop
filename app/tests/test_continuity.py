from app.tests.factories.stores import open_continuity_store
import hashlib
import json
import sqlite3

import pytest
from pydantic import ValidationError

from app.core.llm import MockLLM
from app.core.llm.errors import LLMProviderError
from app.domain.action.schemas import Anchor
from app.domain.confirmed_history import (
    build_turn_documents,
    history_document_text_hash,
)
from app.domain.continuity import (
    ContinuityItem,
    ContinuityItemDraft,
    ContinuityProjection,
    ContinuityProjectionOutput,
    ContinuityProjectionOutputLoose,
    ContinuityProjector,
    ContinuityRetriever,
    ContinuityService,
    ContinuityStore,
    ContinuityUpdateDraft,
)
from app.domain.current_state import CurrentState, PlayerState, SceneState, WorldClock
from app.core.llm.schema import schema_json

def _draft(
    *,
    kind="open_thread",
    subject="追查失踪账册",
    premise="查明失踪账册的去向。",
    entities=None,
    location="临江客栈",
    deadline=None,
):
    return ContinuityItemDraft(
        kind=kind,
        subject=subject,
        premise=premise,
        entities=entities or ["顾晚棠"],
        location=location,
        deadline=deadline,
    )

def _item(item_id: str, *, turn=1, current_state=None, **kwargs):
    return ContinuityItem(
        id=item_id,
        **_draft(**kwargs).model_dump(),
        current_state=current_state,
        created_turn=turn,
        updated_turn=turn,
    )

def _update(
    *,
    state_delta,
    new_current_state=None,
    subject="追查失踪账册",
    entities=None,
    location="临江客栈",
    deadline=None,
):
    return ContinuityUpdateDraft(
        subject=subject,
        state_delta=state_delta,
        new_current_state=new_current_state or state_delta,
        entities=entities or ["顾晚棠"],
        location=location,
        deadline=deadline,
    )

def _operation(operation, *, item_id=None, item=None, evidence_id="turn_2_p001"):
    payload = {
        "operation": operation,
        "evidence_ids": [evidence_id],
        "reason": "本轮已确认正文直接支持该变化。",
    }
    if item_id is not None:
        payload["item_id"] = item_id
    if item is not None:
        if operation == "update":
            payload.update(item.model_dump())
        else:
            payload["item"] = item.model_dump()
    return payload

def _evidence_documents(text="顾晚棠检查了账册。"):
    return {
        document.document_id: document
        for document in build_turn_documents(
            scene_id="scene_002",
            turn_index=2,
            user_input="继续调查。",
            scene_text=text,
        )
        if document.source_type == "scene_paragraph"
    }

def test_projector_sends_complete_active_ledger_in_one_llm_call():
    class SpyGateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
                kwargs["on_attempt_start"](1)
            result = kwargs["output_schema"].model_validate([])
            if "output_validator" in kwargs and kwargs["output_validator"] is not None:
                kwargs["output_validator"](result)
            return result

    gateway = SpyGateway()
    active = [
        _item(
            f"item-{index}",
            subject=f"事项{index}",
            premise=f"处理事项{index}。",
            current_state="已经开始处理。",
        )
        for index in range(20)
    ]
    documents = build_turn_documents(
        scene_id="scene_002",
        turn_index=2,
        user_input="继续调查。",
        scene_text="顾晚棠检查了账册。",
    )

    result = ContinuityProjector(gateway).project(
        current_documents=documents,
        active_items=active,
        trace_id="trace-continuity",
    )

    assert result.operations == []
    assert len(gateway.calls) == 1
    assert gateway.calls[0]["output_schema"] is ContinuityProjectionOutputLoose
    output_schema = json.loads(
        gateway.calls[0]["input_data"]["output_schema_json"]
    )
    assert output_schema["type"] == "array"
    projected = json.loads(
        gateway.calls[0]["input_data"]["active_continuity_json"]
    )
    assert [item["id"] for item in projected] == [
        f"item-{index}" for index in range(20)
    ]
    assert projected[0]["premise"] == "处理事项0。"
    assert projected[0]["current_state"] == "已经开始处理。"
    confirmed = json.loads(
        gateway.calls[0]["input_data"]["confirmed_turn_json"]
    )
    assert {document["source_type"] for document in confirmed} == {
        "scene_paragraph"
    }


def test_service_applies_operations_and_persists_hashed_evidence_logs():
    with open_continuity_store() as store:
        store.insert(_item("update-me"))
        store.insert(_item("resolve-me", subject="偿还欠款", kind="commitment"))
        store.insert(_item("cancel-me", subject="赴约", kind="commitment"))
        projection = ContinuityProjection.model_validate({"operations": [
            _operation(
                "create",
                item=_draft(subject="阻止伏击", premise="阻止码头伏击。"),
            ),
            _operation(
                "update",
                item_id="update-me",
                item=_update(state_delta="已确认账册被送往盐帮仓库。"),
            ),
            _operation("resolve", item_id="resolve-me"),
            _operation("cancel", item_id="cancel-me"),
        ]})

        result = ContinuityService(None, store).apply(
            projection,
            turn_index=2,
            evidence_documents=_evidence_documents(),
        )

        assert len(result.created_ids) == 1
        assert result.updated_ids == ["update-me"]
        assert result.resolved_ids == ["resolve-me"]
        assert result.cancelled_ids == ["cancel-me"]
        updated = store.get("update-me")
        assert updated.premise == "查明失踪账册的去向。"
        assert updated.current_state == "已确认账册被送往盐帮仓库。"
        assert store.get("resolve-me").status == "resolved"
        assert store.get("cancel-me").status == "cancelled"

        logs = store.logs_for_item("update-me")
        assert len(logs) == 1
        assert logs[0].operation == "update"
        assert logs[0].turn_index == 2
        assert logs[0].state_delta == "已确认账册被送往盐帮仓库。"
        assert logs[0].evidence[0].position == 0
        assert logs[0].evidence[0].evidence_id == "turn_2_p001"
        assert logs[0].evidence[0].evidence_hash == hashlib.sha256(
            "顾晚棠检查了账册。".encode("utf-8")
        ).hexdigest()
        reverse_logs = store.logs_for_evidence_ids(["turn_2_p001"])
        assert {log.item_id for log in reverse_logs} == {
            result.created_ids[0], "update-me", "resolve-me", "cancel-me",
        }


def test_bridge_lifecycle_query_cost_is_constant_for_many_updates():
    with open_continuity_store() as store:
        store.insert(_item("long-lived"))
        store.add_lifecycle_log(
            item_id="long-lived",
            operation="create",
            turn_index=1,
            state_delta=None,
            reason="创建。",
            evidence=[("turn_1_p001", "0" * 64)],
        )
        for turn in range(2, 102):
            store.add_lifecycle_log(
                item_id="long-lived",
                operation="update",
                turn_index=turn,
                state_delta=f"进展{turn}",
                reason="更新。",
                evidence=[(f"turn_{turn}_p001", "0" * 64)],
            )

        statements = []
        store._conn.set_trace_callback(statements.append)
        logs = store.bridge_lifecycle_logs(["long-lived"])
        store._conn.set_trace_callback(None)

    reads = [
        statement for statement in statements
        if statement.lstrip().upper().startswith(("SELECT", "WITH"))
    ]
    assert len(reads) == 2
    assert [(log.operation, log.turn_index) for log in logs] == [
        ("update", 101),
        ("create", 1),
    ]

def test_latest_update_logs_returns_only_newest_update_per_item():
    with open_continuity_store() as store:
        for item_id in ("alpha", "beta"):
            store.insert(_item(item_id))
            store.add_lifecycle_log(
                item_id=item_id,
                operation="create",
                turn_index=1,
                state_delta=None,
                reason="创建。",
                evidence=[(f"{item_id}_create", "0" * 64)],
            )
        store.add_lifecycle_log(
            item_id="alpha",
            operation="update",
            turn_index=3,
            state_delta="第一次更新",
            reason="更新。",
            evidence=[("alpha_update_1", "0" * 64)],
        )
        store.add_lifecycle_log(
            item_id="alpha",
            operation="update",
            turn_index=5,
            state_delta="第二次更新",
            reason="更新。",
            evidence=[("alpha_update_2", "0" * 64)],
        )
        store.add_lifecycle_log(
            item_id="beta",
            operation="resolve",
            turn_index=6,
            state_delta=None,
            reason="了结。",
            evidence=[("beta_resolve", "0" * 64)],
        )

        logs = store.latest_update_logs(["alpha", "beta"])
        assert store.latest_update_logs([]) == []

    assert [(log.item_id, log.operation, log.turn_index) for log in logs] == [
        ("alpha", "update", 5),
    ]
    assert [evidence.evidence_id for evidence in logs[0].evidence] == [
        "alpha_update_2",
    ]

def test_service_rejects_unknown_evidence_and_rolls_back_all_operations():
    with open_continuity_store() as store:
        projection = ContinuityProjection.model_validate({"operations": [
            _operation(
                "create",
                item=_draft(subject="合法事项", premise="先写入的事项。"),
            ),
            _operation(
                "create",
                item=_draft(subject="非法事项", premise="引用未知证据。"),
                evidence_id="unknown",
            ),
        ]})

        with pytest.raises(ValueError, match="unknown continuity evidence"):
            ContinuityService(None, store).apply(
                projection,
                turn_index=2,
                evidence_documents=_evidence_documents(),
            )

        assert store.list_all() == []
        assert store.turn_processed(2) is False


def test_service_is_idempotent_by_confirmed_turn():
    with open_continuity_store() as store:
        projection = ContinuityProjection.model_validate({"operations": [
            _operation("create", item=_draft()),
        ]})
        service = ContinuityService(None, store)

        first = service.apply(
            projection,
            turn_index=2,
            evidence_documents=_evidence_documents(),
        )
        second = service.apply(
            projection,
            turn_index=2,
            evidence_documents=_evidence_documents(),
        )

        assert len(first.created_ids) == 1
        assert second.created_ids == []
        assert store.count() == 1


def test_retriever_selects_only_relevant_active_items():
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_003",
            location="临江客栈",
            participants=["玩家", "顾晚棠"],
        ),
        player=PlayerState(name="玩家"),
        world_clock=WorldClock(day=3, phase="晚上"),
    )
    with open_continuity_store() as store:
        store.insert(_item("npc", entities=["顾晚棠"]))
        store.insert(_item(
            "deadline", entities=["盐帮"], location="码头", deadline="第3日晚上"
        ))
        store.insert(_item("irrelevant", entities=["赵六"], location="北城"))
        store.insert(_item(
            "lexical",
            subject="失踪账册",
            premise="查明失踪账册的下落。",
            entities=["钱庄"],
            location="南城",
        ))
        store.close_item("irrelevant", status="resolved", turn_index=2)

        selection = ContinuityRetriever(store).retrieve(
            user_input="我问顾阁主今晚盐帮会不会行动，并调查失踪账册的线索。",
            current_state=state,
            anchors=[Anchor(
                text="顾阁主",
                normalized="顾晚棠",
                kind="character",
                source="explicit",
                confidence=1,
            )],
        )

        assert selection.active_count == 3
        assert {match.item.id for match in selection.items} == {
            "npc", "deadline", "lexical",
        }
        by_id = {match.item.id: match for match in selection.items}
        assert by_id["npc"].matched_entities == ["顾晚棠"]
        assert by_id["deadline"].matched_deadline is True

def _run_projector(mock, active=None, text="顾晚棠检查了账册。"):
    projector = ContinuityProjector(mock.build_gateway())
    result = projector.project(
        current_documents=list(_evidence_documents(text).values()),
        active_items=active or [_item("item-1")],
        trace_id="trace-continuity",
    )
    return result, projector

def test_projector_provider_failure_propagates():
    mock = MockLLM()
    mock.set_fail_provider(True)

    with pytest.raises(LLMProviderError):
        _run_projector(mock)

def test_projector_retries_invalid_operation_and_uses_second():
    first = [{
        "operation": "resolve",
        "evidence_ids": ["turn_2_p001"],
        "reason": "缺 item_id。",
    }]
    second = [_operation("resolve", item_id="item-1")]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock)

    assert len(result.operations) == 1
    assert result.operations[0].item_id == "item-1"
    assert projector.last_warnings == []
