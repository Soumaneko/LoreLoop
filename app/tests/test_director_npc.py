from app.runtime.stores import open_director_npc_store
import sqlite3

import pytest

from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.creative_brief import draw_creative_brief
from app.domain.director_npc import (
    DirectorNPCCreator,
    DirectorNPCDraft,
    DirectorNPCStore,
    KnownAbility,
    NPCCreationContext,
)


def _core_memory() -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(
                id="world",
                kind="world_invariant",
                strength="hard_constraint",
                source="player",
                content="这是一个只有人类的近现代都市。",
            )
        ]
    )


def _draft() -> DirectorNPCDraft:
    return DirectorNPCDraft(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        personality_trait="见人先问诊，习惯主动掌握对方近况",
        known_abilities=[KnownAbility(name="急救", description="处理外伤与常见急症")],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        source_setting_ids=[],
        created_turn=3,
    )


class _CreatorGateway:
    def __init__(self, response: dict):
        self.response = response
        self.calls: list[dict] = []

    def run(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs["output_schema"].model_validate(self.response)


def test_creator_uses_general_context_without_setting_prerequisite():
    gateway = _CreatorGateway(
        {
            "creation_reason": "为社区医疗与旧案线索准备一个可复用人物。",
            "identity": {
                "core_motivation": "保护社区居民。",
                "hidden_secret": "曾目击一宗未结案件。",
                "role": "社区医生",
                "faction": None,
            },
            "behavior": {
                "conflict_style": "先协商后行动。",
                "social_style": "克制而友善。",
                "personality_trait": "见人先问诊，习惯主动掌握对方近况",
            },
            "shell": {
                "known_abilities": [{"name": "急救", "description": "处理外伤与常见急症"}],
                "appearance": "常穿深色风衣。",
                "name": "林澈",
            },
        }
    )

    draft = DirectorNPCCreator(gateway).create(
        core_memory=_core_memory(),
        existing_npcs=[],
        creation_context=NPCCreationContext(setting_ids=[]),
        settings=[],
        creative_brief=draw_creative_brief(),
        protagonist_name="周明",
        turn_index=3,
    )

    assert draft.status == "prepared"
    assert draft.source_setting_ids == []
    assert draft.personality_trait == "见人先问诊，习惯主动掌握对方近况"
    assert draft.known_abilities == [
        KnownAbility(name="急救", description="处理外伤与常见急症")
    ]
    assert draft.name == "林澈"
    trace = gateway.calls[0]["input_data"]
    assert "creation_context_json" in trace
    assert trace["settings_json"] == "[]"


def test_known_ability_rejects_blank_name_and_oversized_label():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KnownAbility(name="   ", description="处理外伤")
    with pytest.raises(ValidationError):
        KnownAbility(name="x" * 21, description="处理外伤")
    with pytest.raises(ValidationError):
        KnownAbility(name="急救", description="x" * 121)


def test_known_ability_allows_empty_description_for_player_edits():
    """Director ability model is LLM-facing and requires a description."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KnownAbility(name="急救", description="   ")


def test_creator_shell_rejects_empty_ability_descriptions():
    from pydantic import ValidationError

    from app.domain.director_npc.schemas import DirectorNPCShell

    with pytest.raises(ValidationError):
        DirectorNPCShell(
            known_abilities=[{"name": "急救", "description": "   "}],
            appearance="风衣。",
            name="林澈",
        )
    ok = DirectorNPCShell(
        known_abilities=[{"name": "急救", "description": "处理外伤"}],
        appearance="风衣。",
        name="林澈",
    )
    assert ok.known_abilities[0].description == "处理外伤"


def test_known_ability_rejects_unknown_fields():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        KnownAbility.model_validate({"name": "急救", "description": "处理外伤", "level": 3})


def test_store_uses_prepared_introduced_retired_lifecycle():
    store = open_director_npc_store()
    draft = _draft()
    store.save(draft)

    assert [item.draft_id for item in store.list_prepared()] == [draft.draft_id]
    introduced = store.mark_introduced(draft.draft_id, turn_index=5)
    assert introduced is not None
    assert introduced.status == "introduced"
    assert introduced.introduced_turn == 5
    assert store.list_prepared() == []

    retired = store.retire(draft.draft_id)
    assert retired is not None
    assert retired.status == "retired"
    store.close()


def test_store_rejects_dispatch_schema_instead_of_migrating_it(tmp_path):
    db_path = tmp_path / "director_npcs.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        CREATE TABLE director_npcs (
            draft_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            draft_json TEXT NOT NULL,
            created_turn INTEGER NOT NULL,
            dispatched_turn INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    try:
        open_director_npc_store(str(db_path))
    except RuntimeError as exc:
        assert "current Director Agent contract" in str(exc)
    else:
        raise AssertionError("dispatch-era DirectorNPC schema must be rejected")


def test_npc_domain_does_not_import_world_setting_or_define_dispatch():
    from pathlib import Path

    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in Path("app/domain/director_npc").glob("*.py")
    )
    assert "world_setting" not in source
    assert "dispatch" not in source.casefold()


def test_creator_includes_recent_creations_in_prompt():
    import json

    gateway = _CreatorGateway(
        {
            "creation_reason": "与同批事件耦合而准备。",
            "identity": {
                "core_motivation": "查明沉船真相。",
                "hidden_secret": "见过账本。",
                "role": "码头把头",
                "faction": None,
            },
            "behavior": {
                "conflict_style": "先观察再出手。",
                "social_style": "寡言。",
                "personality_trait": "开口必先谈货，沉默时盯着对方手里的东西",
            },
            "shell": {
                "known_abilities": [{"name": "盘货", "description": "凭手感辨认船货成色"}],
                "appearance": "左臂旧疤。",
                "name": "沈二爷",
            },
        }
    )
    recent_creations = [
        {"kind": "event", "title": "沉船账本", "role": "arc"},
        {"kind": "npc", "name": "阿贵", "role": "账房先生"},
    ]

    DirectorNPCCreator(gateway).create(
        core_memory=_core_memory(),
        existing_npcs=[],
        creation_context=NPCCreationContext(setting_ids=[]),
        settings=[],
        creative_brief=draw_creative_brief(),
        protagonist_name="周明",
        turn_index=3,
        recent_creations=recent_creations,
    )

    payload = json.loads(gateway.calls[0]["input_data"]["batch_recent_creations_json"])
    assert payload == recent_creations


def test_creator_brief_hides_social_dimensions_from_prompt():
    import json

    gateway = _CreatorGateway(
        {
            "creation_reason": "隐藏社会维度后创建。",
            "identity": {
                "core_motivation": "守住货路。",
                "hidden_secret": "旧账未清。",
                "role": "码头把头",
                "faction": "码头会",
            },
            "behavior": {
                "conflict_style": "先硬扛再周旋。",
                "social_style": "话少利落。",
                "personality_trait": "事到临头先自己扛，不轻易找人商量",
            },
            "shell": {
                "known_abilities": [{"name": "盘货", "description": "凭手感辨认船货成色"}],
                "appearance": "左臂旧疤。",
                "name": "沈二爷",
            },
        }
    )

    DirectorNPCCreator(gateway).create(
        core_memory=_core_memory(),
        existing_npcs=[],
        creation_context=NPCCreationContext(setting_ids=[]),
        settings=[],
        creative_brief=draw_creative_brief(),
        protagonist_name="周明",
        turn_index=3,
    )

    brief = json.loads(gateway.calls[0]["input_data"]["creative_brief_json"])
    assert "social_position" not in brief
    assert "optional_dimension" not in brief
    assert "world_connection" in brief
    assert "life_phase" in brief
    assert "desire_pattern" in brief
    assert brief["baseline_temperament"] in {
        "热情豪爽",
        "古灵精怪",
        "暴躁强势",
        "温婉平和",
        "极度务实",
        "高傲毒舌",
        "娇憨随和",
        "慵懒散漫",
        "冷漠孤僻",
    }