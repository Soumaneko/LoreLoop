import sqlite3

from app.domain.core_memory import CoreMemory, CoreMemoryItem
from app.domain.director_npc import (
    DirectorNPCCreator,
    DirectorNPCDraft,
    DirectorNPCStore,
    NPCCreationContext,
    draw_director_npc_creative_brief,
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
        personality_traits=["谨慎", "耐心"],
        known_abilities=["急救"],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        encounter_potentials=["诊所求助", "街区冲突", "旧案线索"],
        creation_anchors=["河岸社区"],
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
            "core_motivation": "保护社区居民。",
            "hidden_secret": "曾目击一宗未结案件。",
            "conflict_style": "先协商后行动。",
            "social_style": "克制而友善。",
            "role": "社区医生",
            "faction": None,
            "personality_traits": ["谨慎", "耐心"],
            "known_abilities": ["急救"],
            "appearance": "常穿深色风衣。",
            "name": "林澈",
            "encounter_potentials": ["诊所求助", "街区冲突", "旧案线索"],
        }
    )

    draft = DirectorNPCCreator(gateway).create(
        core_memory=_core_memory(),
        existing_npcs=[],
        creation_context=NPCCreationContext(
            purpose="补充社区层面的长期人物。",
            anchors=["河岸社区"],
        ),
        creative_brief=draw_director_npc_creative_brief(),
        protagonist_name="周明",
        turn_index=3,
    )

    assert draft.status == "prepared"
    assert draft.creation_anchors == ["河岸社区"]
    trace = gateway.calls[0]["input_data"]
    assert "creation_context_json" in trace
    assert "setting_json" not in trace


def test_store_uses_prepared_introduced_retired_lifecycle():
    store = DirectorNPCStore()
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
        DirectorNPCStore(str(db_path))
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
