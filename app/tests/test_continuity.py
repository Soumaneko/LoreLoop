from app.runtime.stores import open_continuity_store
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

def test_projector_accepts_operation_array_as_its_wire_contract():
    class ArrayGateway:
        def run(self, **kwargs):
            if "on_attempt_start" in kwargs and kwargs["on_attempt_start"] is not None:
                kwargs["on_attempt_start"](1)
            result = kwargs["output_schema"].model_validate([{
                "operation": "resolve",
                "item_id": "item-1",
                "evidence_ids": ["turn_2_p001"],
                "reason": "正文确认事项已经完成。",
            }])
            if "output_validator" in kwargs and kwargs["output_validator"] is not None:
                kwargs["output_validator"](result)
            return result

    gateway = ArrayGateway()
    documents = build_turn_documents(
        scene_id="scene_002",
        turn_index=2,
        user_input="继续调查。",
        scene_text="顾晚棠确认账册已经找回。",
    )

    result = ContinuityProjector(gateway).project(
        current_documents=documents,
        active_items=[_item("item-1")],
    )

    assert result.operations[0].operation == "resolve"
    assert result.operations[0].item_id == "item-1"

def test_update_schema_cannot_modify_premise():
    with pytest.raises(ValidationError):
        ContinuityProjectionOutput.model_validate([{
            **_operation(
                "update",
                item_id="item-1",
                item=_update(state_delta="找到新线索。"),
            ),
            "premise": "偷偷改写原始事项。",
        }])

    output_schema = json.loads(schema_json(ContinuityProjectionOutput))
    update_properties = output_schema["$defs"][
        "ContinuityUpdateOperationHard"
    ]["properties"]
    assert "premise" not in update_properties
    assert "kind" not in update_properties

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

def test_history_document_hash_uses_exact_utf8_text_without_normalization():
    document = next(iter(_evidence_documents("原文。").values())).model_copy(
        update={"text": "  原文。\n"}
    )
    normalized = document.model_copy(update={"text": "原文。"})

    assert history_document_text_hash(document) == hashlib.sha256(
        "  原文。\n".encode("utf-8")
    ).hexdigest()
    assert history_document_text_hash(document) != (
        history_document_text_hash(normalized)
    )

def test_update_replaces_current_snapshot_while_logs_keep_each_delta():
    with open_continuity_store() as store:
        store.insert(_item("bounded"))
        store.update_active(
            "bounded",
            _update(
                state_delta="怀疑幕后势力是青云宗。",
                new_current_state="已锁定周姓修士，怀疑其受青云宗指使。",
            ),
            turn_index=2,
        )
        store.update_active(
            "bounded",
            _update(
                state_delta="确认青云宗只是中间人。",
                new_current_state=(
                    "已锁定周姓修士；青云宗只是中间人，真正幕后势力未知。"
                ),
            ),
            turn_index=3,
        )
        item = store.get("bounded")

    assert item.current_state == (
        "已锁定周姓修士；青云宗只是中间人，真正幕后势力未知。"
    )

def test_update_merges_entities_and_null_metadata_preserves_existing_values():
    with open_continuity_store() as store:
        store.insert(_item(
            "monotonic-metadata",
            entities=["主角", "合欢宗"],
            location="陷空山",
            deadline="三日内",
        ))

        store.update_active(
            "monotonic-metadata",
            ContinuityUpdateDraft(
                subject="追查宗门探子",
                state_delta="收到黑市回应。",
                new_current_state="已收到黑市回应，准备前往接头。",
                entities=[],
                location=None,
                deadline=None,
            ),
            turn_index=2,
        )
        unchanged = store.get("monotonic-metadata")
        assert unchanged.entities == ["主角", "合欢宗"]
        assert unchanged.location == "陷空山"
        assert unchanged.deadline == "三日内"

        store.update_active(
            "monotonic-metadata",
            ContinuityUpdateDraft(
                subject="追查宗门探子",
                state_delta="姚沉璧在落雁渡现身。",
                new_current_state="已在落雁渡与姚沉璧接头。",
                entities=["合欢宗", "姚沉璧"],
                location="落雁渡",
                deadline=None,
            ),
            turn_index=3,
        )
        changed = store.get("monotonic-metadata")

    assert changed.entities == ["主角", "合欢宗", "姚沉璧"]
    assert changed.location == "落雁渡"
    assert changed.deadline == "三日内"

def test_long_lived_item_can_accumulate_more_than_twelve_entities():
    initial_entities = [f"实体{index}" for index in range(12)]
    with open_continuity_store() as store:
        store.insert(_item("many-entities", entities=initial_entities))

        updated = store.update_active(
            "many-entities",
            ContinuityUpdateDraft(
                subject="追查关联人物",
                state_delta="确认另一名关联人物。",
                new_current_state="调查仍在继续，已确认另一名关联人物。",
                entities=["新增实体"],
                location=None,
                deadline=None,
            ),
            turn_index=2,
        )
        reloaded = store.get("many-entities")

    assert updated.entities == [*initial_entities, "新增实体"]
    assert reloaded is not None
    assert reloaded.entities == updated.entities

def test_manual_metadata_edit_replaces_entities_instead_of_merging():
    with open_continuity_store() as store:
        store.insert(_item("manual-edit", entities=["A", "B"]))

        updated = store.edit_active_metadata(
            "manual-edit",
            subject="追查失踪账册",
            entities=["A"],
            location="临江客栈",
            deadline=None,
            turn_index=2,
        )
        reloaded = store.get("manual-edit")

    assert updated.entities == ["A"]
    assert reloaded is not None
    assert reloaded.entities == ["A"]

def test_manual_metadata_edit_validates_before_writing():
    with open_continuity_store() as store:
        store.insert(_item("invalid-manual-edit", entities=["A", "B"]))

        with pytest.raises(ValidationError):
            store.edit_active_metadata(
                "invalid-manual-edit",
                subject="过长" * 61,
                entities=["A"],
                location="临江客栈",
                deadline=None,
                turn_index=2,
            )
        reloaded = store.get("invalid-manual-edit")

    assert reloaded is not None
    assert reloaded.subject == "追查失踪账册"
    assert reloaded.entities == ["A", "B"]
    assert reloaded.updated_turn == 1

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

def test_service_rejects_evidence_from_another_turn():
    documents = _evidence_documents()
    wrong_turn = documents["turn_2_p001"].model_copy(update={"turn_index": 1})
    with open_continuity_store() as store:
        projection = ContinuityProjection.model_validate({"operations": [
            _operation("create", item=_draft()),
        ]})
        with pytest.raises(ValueError, match="scene paragraph from turn 2"):
            ContinuityService(None, store).apply(
                projection,
                turn_index=2,
                evidence_documents={"turn_2_p001": wrong_turn},
            )

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

def test_v1_store_migrates_statement_and_evidence_relationship(tmp_path):
    db_path = tmp_path / "continuity-v1.db"
    connection = sqlite3.connect(db_path)
    connection.executescript("""
        CREATE TABLE continuity_items (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            subject TEXT NOT NULL, statement TEXT NOT NULL,
            entities_json TEXT NOT NULL, location TEXT, deadline TEXT,
            created_turn INTEGER NOT NULL, updated_turn INTEGER NOT NULL,
            closed_turn INTEGER
        );
        CREATE INDEX idx_continuity_active
        ON continuity_items(status, updated_turn);
        CREATE VIRTUAL TABLE continuity_items_fts USING fts5(
            item_id UNINDEXED, subject, statement, entities, location,
            deadline, tokenize='trigram'
        );
        CREATE TABLE continuity_evidence (
            item_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
            operation TEXT NOT NULL, turn_index INTEGER NOT NULL,
            PRIMARY KEY(item_id, evidence_id, operation)
        );
        CREATE TABLE continuity_processed_turns (turn_index INTEGER PRIMARY KEY);
        CREATE TABLE schema_versions (
            component TEXT PRIMARY KEY, version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO schema_versions(component, version)
        VALUES ('continuity', 1);
        INSERT INTO continuity_items VALUES (
            'legacy', 'open_thread', 'active', '旧事项', '旧版陈述',
            '["顾晚棠", "盐帮"]', NULL, NULL, 1, 1, NULL
        );
        INSERT INTO continuity_evidence VALUES (
            'legacy', 'turn_1_p001', 'create', 1
        );
    """)
    connection.commit()
    connection.close()

    with open_continuity_store(str(db_path)) as store:
        item = store.get("legacy")
        assert item.premise == "旧版陈述"
        assert item.current_state is None
        fts_entities = store._conn.execute(
            "SELECT entities FROM continuity_items_fts WHERE item_id = ?",
            ("legacy",),
        ).fetchone()["entities"]
        log = store.logs_for_item("legacy")[0]
        assert log.migration_created is True
        assert log.evidence[0].position == 0
        assert log.evidence[0].evidence_id == "turn_1_p001"
        assert log.evidence[0].evidence_hash is None
        assert fts_entities == "顾晚棠 盐帮"

def test_v2_store_adds_positions_and_preserves_legacy_state_as_snapshot(tmp_path):
    db_path = tmp_path / "continuity-v2.db"
    entries = [
        {"turn_index": turn, "text": f"进展{turn}"}
        for turn in range(1, 11)
    ]
    connection = sqlite3.connect(db_path)
    connection.executescript("""
        CREATE TABLE continuity_items (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
            subject TEXT NOT NULL, premise TEXT NOT NULL,
            current_state_json TEXT NOT NULL, entities_json TEXT NOT NULL,
            location TEXT, deadline TEXT, created_turn INTEGER NOT NULL,
            updated_turn INTEGER NOT NULL, closed_turn INTEGER
        );
        CREATE INDEX idx_continuity_active
        ON continuity_items(status, updated_turn);
        CREATE VIRTUAL TABLE continuity_items_fts USING fts5(
            item_id UNINDEXED, subject, premise, current_state, entities,
            location, deadline, tokenize='trigram'
        );
        CREATE TABLE continuity_logs (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL,
            operation TEXT NOT NULL, turn_index INTEGER NOT NULL,
            state_delta TEXT, reason TEXT NOT NULL
        );
        CREATE INDEX idx_continuity_logs_item_turn
        ON continuity_logs(item_id, turn_index, log_id);
        CREATE TABLE continuity_log_evidence (
            log_id INTEGER NOT NULL, evidence_id TEXT NOT NULL,
            evidence_hash TEXT, PRIMARY KEY(log_id, evidence_id)
        );
        CREATE INDEX idx_continuity_log_evidence_id
        ON continuity_log_evidence(evidence_id);
        CREATE TABLE continuity_processed_turns (
            turn_index INTEGER PRIMARY KEY
        );
        CREATE TABLE schema_versions (
            component TEXT PRIMARY KEY, version INTEGER NOT NULL,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        INSERT INTO schema_versions(component, version)
        VALUES ('continuity', 2);
    """)
    connection.execute(
        """
        INSERT INTO continuity_items VALUES (
            'v2', 'open_thread', 'active', '旧事项', '旧前提', ?,
            '["顾晚棠"]', '临江客栈', NULL, 1, 10, NULL
        )
        """,
        (json.dumps(entries, ensure_ascii=False),),
    )
    connection.execute(
        """
        INSERT INTO continuity_logs(
            item_id, operation, turn_index, state_delta, reason
        ) VALUES ('v2', 'create', 1, NULL, '创建。')
        """
    )
    connection.executemany(
        """
        INSERT INTO continuity_log_evidence(
            log_id, evidence_id, evidence_hash
        ) VALUES (1, ?, ?)
        """,
        [("turn_1_p002", "2" * 64), ("turn_1_p001", "1" * 64)],
    )
    connection.commit()
    connection.close()

    with open_continuity_store(str(db_path)) as store:
        item = store.get("v2")
        log = store.logs_for_item("v2")[0]
        with store.transaction():
            store.insert(_item(
                "post-migration",
                current_state="迁移后仍可继续写入新的替换快照。",
            ))
        version = store._conn.execute(
            "SELECT version FROM schema_versions WHERE component='continuity'"
        ).fetchone()[0]

    assert version == 3
    assert item.current_state == "\n".join(
        f"进展{turn}" for turn in range(1, 11)
    )
    assert [evidence.position for evidence in log.evidence] == [0, 1]
    assert [evidence.evidence_id for evidence in log.evidence] == [
        "turn_1_p001",
        "turn_1_p002",
    ]
    assert log.migration_created is False
    with open_continuity_store(str(db_path)) as reopened:
        assert reopened.get("post-migration").current_state == (
            "迁移后仍可继续写入新的替换快照。"
        )

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

def test_projector_uses_first_valid_subset_when_second_has_no_valid_ops():
    first = [
        _operation("resolve", item_id="item-1"),
        {
            "operation": "resolve",
            "evidence_ids": ["turn_2_p001"],
            "reason": "缺 item_id。",
        },
    ]
    second = [{
        "operation": "resolve",
        "evidence_ids": ["turn_2_p001"],
        "reason": "仍缺 item_id。",
    }]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock)

    assert [op.item_id for op in result.operations] == ["item-1"]
    assert any(
        warning.code == "continuity_operation_dropped"
        for warning in projector.last_warnings
    )

def test_projector_drops_duplicate_item_id_and_keeps_unrelated_operation():
    active = [_item("item-1"), _item("item-2")]
    first = [
        _operation("resolve", item_id="item-1"),
        _operation("resolve", item_id="item-1"),
    ]
    second = [
        _operation("resolve", item_id="item-1"),
        _operation("resolve", item_id="item-1"),
        _operation("resolve", item_id="item-2"),
    ]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock, active=active)

    assert [op.item_id for op in result.operations] == ["item-2"]
    assert any(
        warning.code == "continuity_operation_dropped"
        for warning in projector.last_warnings
    )

def test_projector_drops_non_active_item_id_and_keeps_active_operation():
    active = [_item("item-1")]
    first = [_operation("resolve", item_id="item-999")]
    second = [
        _operation("resolve", item_id="item-999"),
        _operation("resolve", item_id="item-1"),
    ]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock, active=active)

    assert [op.item_id for op in result.operations] == ["item-1"]
    assert any(
        warning.code == "continuity_operation_dropped"
        for warning in projector.last_warnings
    )

def test_projector_soft_accepts_premise_within_soft_limit_on_second_attempt():
    def create(premise):
        return {
            "operation": "create",
            "evidence_ids": ["turn_2_p001"],
            "reason": "本轮建立了新事项。",
            "item": _draft(premise=premise).model_dump(),
        }

    first = [create("长" * 300)]
    second = [create("长" * 300)]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock)

    assert len(result.operations[0].item.premise) == 300
    assert projector.last_warnings == []

def test_projector_truncates_premise_over_soft_limit_on_second_attempt():
    def create(premise):
        return {
            "operation": "create",
            "evidence_ids": ["turn_2_p001"],
            "reason": "本轮建立了新事项。",
            "item": {
                "kind": "open_thread",
                "subject": "追查失踪账册",
                "premise": premise,
                "entities": ["顾晚棠"],
                "location": "临江客栈",
                "deadline": None,
            },
        }

    first = [create("长" * 400)]
    second = [create("长" * 400)]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock)

    assert len(result.operations[0].item.premise) == 360
    assert any(
        warning.code == "continuity_truncated"
        for warning in projector.last_warnings
    )

def test_projector_drops_operation_with_unknown_evidence_on_second_attempt():
    first = [{
        "operation": "resolve",
        "item_id": "item-1",
        "evidence_ids": ["unknown"],
        "reason": "引用未知证据。",
    }]
    second = [
        {
            "operation": "resolve",
            "item_id": "item-1",
            "evidence_ids": ["unknown"],
            "reason": "仍引用未知证据。",
        },
        _operation("resolve", item_id="item-1"),
    ]
    mock = MockLLM()
    mock.set_responses([
        json.dumps(first, ensure_ascii=False),
        json.dumps(second, ensure_ascii=False),
    ])

    result, projector = _run_projector(mock)

    assert len(result.operations) == 1
    assert any(
        warning.code == "continuity_operation_dropped"
        for warning in projector.last_warnings
    )
