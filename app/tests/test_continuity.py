import json
from pathlib import Path

import pytest

from app.domain.action.schemas import Anchor
from app.domain.confirmed_history import build_turn_documents
from app.domain.continuity import (
    ContinuityItem,
    ContinuityItemDraft,
    ContinuityOperation,
    ContinuityProjection,
    ContinuityProjector,
    ContinuityRetriever,
    ContinuityService,
    ContinuityStore,
)
from app.domain.current_state import CurrentState, PlayerState, SceneState, WorldClock


def _draft(
    *,
    kind="open_thread",
    subject="追查失踪账册",
    statement="查明失踪账册的去向。",
    entities=None,
    location="临江客栈",
    deadline=None,
):
    return ContinuityItemDraft(
        kind=kind,
        subject=subject,
        statement=statement,
        entities=entities or ["顾晚棠"],
        location=location,
        deadline=deadline,
    )


def _item(item_id: str, *, turn=1, **kwargs):
    return ContinuityItem(
        id=item_id,
        **_draft(**kwargs).model_dump(),
        created_turn=turn,
        updated_turn=turn,
    )


def _operation(operation, *, item_id=None, item=None, evidence_id="turn_2_p001"):
    return ContinuityOperation(
        operation=operation,
        item_id=item_id,
        item=item,
        evidence_ids=[evidence_id],
        reason="本轮已确认正文直接支持该变化。",
    )


def test_projector_sends_complete_active_ledger_in_one_llm_call():
    class SpyGateway:
        def __init__(self):
            self.calls = []

        def run(self, **kwargs):
            self.calls.append(kwargs)
            return kwargs["output_schema"].model_validate({"operations": []})

    gateway = SpyGateway()
    active = [
        _item(
            f"item-{index}",
            subject=f"事项{index}",
            statement=f"处理事项{index}。",
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
    assert gateway.calls[0]["task"] == "continuity_project"
    projected = json.loads(
        gateway.calls[0]["input_data"]["active_continuity_json"]
    )
    assert [item["id"] for item in projected] == [
        f"item-{index}" for index in range(20)
    ]
    confirmed = json.loads(
        gateway.calls[0]["input_data"]["confirmed_turn_json"]
    )
    assert {document["source_type"] for document in confirmed} == {
        "scene_paragraph"
    }


def test_service_applies_create_update_resolve_and_cancel_with_evidence():
    with ContinuityStore() as store:
        store.insert(_item("update-me"))
        store.insert(_item("resolve-me", subject="偿还欠款", kind="commitment"))
        store.insert(_item("cancel-me", subject="赴约", kind="commitment"))
        projection = ContinuityProjection(operations=[
            _operation(
                "create",
                item=_draft(subject="阻止伏击", statement="阻止码头伏击。"),
            ),
            _operation(
                "update",
                item_id="update-me",
                item=_draft(statement="已确认账册被送往盐帮仓库。"),
            ),
            _operation("resolve", item_id="resolve-me"),
            _operation("cancel", item_id="cancel-me"),
        ])

        result = ContinuityService(None, store).apply(
            projection,
            turn_index=2,
            available_evidence_ids={"turn_2_p001"},
        )

        assert len(result.created_ids) == 1
        assert result.updated_ids == ["update-me"]
        assert result.resolved_ids == ["resolve-me"]
        assert result.cancelled_ids == ["cancel-me"]
        assert store.get("update-me").statement == "已确认账册被送往盐帮仓库。"
        assert store.get("resolve-me").status == "resolved"
        assert store.get("cancel-me").status == "cancelled"
        assert store.evidence_for_item("update-me") == [{
            "evidence_id": "turn_2_p001",
            "operation": "update",
            "turn_index": 2,
        }]


def test_service_rejects_unknown_evidence_and_rolls_back_all_operations():
    with ContinuityStore() as store:
        projection = ContinuityProjection(operations=[
            _operation(
                "create",
                item=_draft(subject="合法事项", statement="先写入的事项。"),
            ),
            _operation(
                "create",
                item=_draft(subject="非法事项", statement="引用未知证据。"),
                evidence_id="unknown",
            ),
        ])

        with pytest.raises(ValueError, match="unknown continuity evidence"):
            ContinuityService(None, store).apply(
                projection,
                turn_index=2,
                available_evidence_ids={"turn_2_p001"},
            )

        assert store.list_all() == []
        assert store.turn_processed(2) is False


def test_service_is_idempotent_by_confirmed_turn():
    with ContinuityStore() as store:
        projection = ContinuityProjection(operations=[
            _operation("create", item=_draft()),
        ])
        service = ContinuityService(None, store)

        first = service.apply(
            projection,
            turn_index=2,
            available_evidence_ids={"turn_2_p001"},
        )
        second = service.apply(
            projection,
            turn_index=2,
            available_evidence_ids={"turn_2_p001"},
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
    with ContinuityStore() as store:
        store.insert(_item("npc", entities=["顾晚棠"]))
        store.insert(_item(
            "deadline",
            entities=["盐帮"],
            location="码头",
            deadline="第3日晚上",
        ))
        store.insert(_item(
            "irrelevant",
            entities=["赵六"],
            location="北城",
        ))
        store.insert(_item(
            "lexical",
            subject="失踪账册",
            statement="查明失踪账册的下落。",
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
            "npc",
            "deadline",
            "lexical",
        }
        by_id = {match.item.id: match for match in selection.items}
        assert by_id["npc"].matched_entities == ["顾晚棠"]
        assert by_id["deadline"].matched_deadline is True


def test_continuity_prompt_is_chinese_and_defines_two_item_kinds():
    prompt = Path(
        "app/prompts/confirmation/continuity_project_v1.md"
    ).read_text(encoding="utf-8")

    assert "# 角色" in prompt
    assert "明确的承诺、债务、协议、职责或义务" in prompt
    assert "未完成目标、仍存在的威胁" in prompt
    assert "每个操作必须引用" in prompt
