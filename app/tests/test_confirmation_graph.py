from app.tests.factories.domains import make_pending_turn
from app.tests.factories.stores import open_world_setting_store
from concurrent.futures import CancelledError
from types import SimpleNamespace

import pytest
from langgraph.graph import END, START, StateGraph

from app.core.diagnostics import DiagnosticLogger
from app.domain.core_memory import CoreMemory
from app.domain.current_state import CurrentState, PlayerState, SceneState
from app.domain.current_state.changes import StateChangeSet, StateChangeWarning
from app.domain.scene_facts.schemas import SceneFactSet
from app.domain.scene_summary.schemas import SceneSummary
from app.domain.scene_writer.schemas import NextActionDirection, SceneWriterResult
from app.graph import confirmation_nodes
from app.graph.confirmation_graph import capture_confirmation_node_errors
from app.graph.confirmation_helpers import merge_confirmation_node_update
from app.graph.confirmation_helpers import prepare_confirmation_second_wave
from app.graph.confirmation_state import ConfirmationState
from app.runtime.sqlite import connect_sqlite


def test_state_update_warning_is_emitted_and_diagnosed(
    monkeypatch,
    tmp_path,
):
    current_state = CurrentState(
        scene=SceneState(scene_id="scene-1", location="门外"),
        player=PlayerState(name="主角"),
    )
    warning = StateChangeWarning(
        operation_id="op2",
        operation_type="participant_attitude_change",
        target="米莉",
        code="npc_target_not_present",
        message="target missing",
    )
    change_set = StateChangeSet(decision="NPC 已离开。")

    class _Updater:
        def __init__(self, _gateway):
            self.last_change_set = change_set
            self.last_warnings = [warning]

        def update(self, state, *_args, **_kwargs):
            return state

    monkeypatch.setattr(confirmation_nodes, "StateUpdater", _Updater)
    monkeypatch.setattr(confirmation_nodes, "_gateway_for", lambda _state: object())
    diagnostics = DiagnosticLogger(tmp_path / "diagnostics.jsonl")
    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, user_input="推门而入"),
        "trace_id": "trace-1",
        "cache": {},
        "state_before_update": current_state,
        "effective_scene_text": "主角走入门内。",
        "scene_facts_json": "{}",
        "core_memory": CoreMemory(items=[]),
        "existing_npc_profiles": [],
        "events": [],
        "node_errors": [],
        "write_db": True,
        "runtime_services": SimpleNamespace(diagnostics=diagnostics),
    }

    update = confirmation_nodes.state_update_node(state)
    events = merge_confirmation_node_update(state, update)

    assert update["state_update_warnings"] == [warning]
    assert events[0]["data"]["warnings"][0]["code"] == "npc_target_not_present"
    diagnostic = diagnostics.read(trace_id="trace-1")[0]
    assert diagnostic["source"] == "state_update"
    assert diagnostic["code"] == "npc_target_not_present"


def test_only_selection_summaries_append_current_turn_summary():
    historical = [
        SceneSummary(
            id=f"summary-{index}",
            level=1,
            content=f"摘要 {index}",
            turn_start=index,
            turn_end=index,
        )
        for index in range(1, 6)
    ]
    current = SceneSummary(
        id="summary-6",
        level=1,
        content="本轮摘要",
        turn_start=6,
        turn_end=6,
    )

    class _Store:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_active(self):
            return historical

        def get_recent(self, limit):
            assert limit == 5
            return historical

    class _ContinuityStore:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def list_active(self):
            return []

    state = {
        "cache": {},
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_006", location="测试地点"),
            player=PlayerState(name="主角"),
        ),
        "turn_summary": current,
        "pipeline_input": SimpleNamespace(
            clear_continuity_before_save=False,
        ),
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(
                scene_summary=_Store(),
                continuity=_ContinuityStore(),
            )
        ),
        "events": [],
    }

    prepared = prepare_confirmation_second_wave(state)

    assert prepared["recent_summaries"] == historical
    assert prepared["scene_summaries"] == historical
    assert prepared["selection_summaries"] == [*historical, current]
    assert prepared["active_continuity_items"] == []


def test_setting_appearance_node_dry_run_and_prevents_double_count(tmp_path):
    from app.domain.world_setting import WorldSetting

    db_path = tmp_path / "world_settings.db"
    with open_world_setting_store(str(db_path)) as seed_store:
        library = WorldSetting(
            kind="location",
            name="灰烬图书馆",
            summary="一座石砌图书馆。",
            public_facts=["对登记学者开放。"],
            created_turn=1,
        )
        guild = WorldSetting(
            kind="faction",
            name="北陆商盟",
            summary="北境商会。",
            public_facts=["统一收取过路费。"],
            created_turn=1,
        )
        seed_store.save(library)
        seed_store.save(guild)

    connection = connect_sqlite(db_path)
    state = {
        "write_db": True,
        "effective_scene_text": "莱尔走进了灰烬图书馆的借阅室。",
        "cache": {},
        "runtime_services": SimpleNamespace(
            stores=SimpleNamespace(
                world_setting=open_world_setting_store(connection)
            )
        ),
    }

    first = confirmation_nodes.setting_appearance_update_node(state)
    assert first["recorded_setting_ids"] == [library.setting_id]
    merge_confirmation_node_update(state, first)
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1
    connection.close()

    replayed = confirmation_nodes.setting_appearance_update_node(state)
    assert replayed["recorded_setting_ids"] == [library.setting_id]
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1

    dry = confirmation_nodes.setting_appearance_update_node(
        {"write_db": False, "effective_scene_text": "灰烬图书馆", "cache": {}}
    )
    assert dry == {"recorded_setting_ids": []}
    with open_world_setting_store(str(db_path)) as check:
        assert check.get_by_id(library.setting_id).hit_count == 1


def test_node_error_wrapper_reraise_cancellation():
    def cancelled(_state: ConfirmationState) -> ConfirmationState:
        raise CancelledError("user cancelled")

    graph = StateGraph(ConfirmationState)
    graph.add_node(
        "cancelled",
        capture_confirmation_node_errors("cancelled", cancelled),
    )
    graph.add_edge(START, "cancelled")
    graph.add_edge("cancelled", END)

    state = {"events": [], "node_errors": [], "cache": {}, "write_db": True}
    with pytest.raises(CancelledError):
        for update in graph.compile().stream(state, stream_mode="updates"):
            for node_update in update.values():
                merge_confirmation_node_update(state, node_update)

    assert state["node_errors"] == []


def test_edited_scene_preview_drops_original_directions_and_uses_scene_facts(
    monkeypatch,
    configure_runtime_services,
):
    captured = {}
    scene_facts = SceneFactSet(scene_focus="编辑后的场景", facts=[])
    scene_result = SceneWriterResult(
        scene_text="原始正文",
        next_action_directions=[
            NextActionDirection(
                direction="继续原稿中的追逐",
                rationale="原稿保留了追逐。",
            )
        ],
    )

    class _Gateway:
        def set_trace_context(self, **_kwargs):
            return None

    class _PreviewAgent:
        def __init__(self, _gateway):
            pass

        def generate(self, **kwargs):
            captured.update(kwargs)
            return {"previews": []}

    services = configure_runtime_services(gateway_factory=_Gateway)
    monkeypatch.setattr(confirmation_nodes, "ActionPreviewAgent", _PreviewAgent)

    state = {
        "pipeline_input": SimpleNamespace(turn_index=1, scene_result=scene_result),
        "runtime_services": services,
        "trace_id": "trace",
        "cache": {},
        "effective_scene_text": "编辑后的正文",
        "updated_state": CurrentState(
            scene=SceneState(scene_id="scene_001", location="新地点"),
            player=PlayerState(name="主角"),
        ),
        "core_memory": CoreMemory(items=[]),
        "recent_summaries": [],
        "scene_facts": scene_facts,
    }

    confirmation_nodes.action_preview_node(state)

    assert captured["next_action_directions"] == []
    assert captured["scene_facts"] == scene_facts


@pytest.mark.parametrize("material_kind,receipt,presence,expected", [
    ("npc", "used", "appeared", True),
    ("event", "used", "appeared", True),
    ("engaged_event", "used", "mentioned_only", True),
    ("npc", "unused", "appeared", False),
    ("npc", "unknown", "appeared", False),
    ("npc", "legacy", "appeared", False),
    ("npc", "edited", "appeared", False),
    ("npc", "dry_run", "appeared", False),
    ("event", "existing", "appeared", False),
])
def test_staged_director_npc_promotes_without_preadding_participants(
    configure_runtime_services, material_kind, receipt, presence, expected,
):
    from app.domain.director import DirectorStageMaterial, DirectorStageResult
    from app.domain.director_npc import DirectorNPCDraft, KnownAbility
    from app.graph.confirmation_helpers import prepare_confirmation_state
    from app.graph.confirmation_state import ConfirmationPipelineInput
    from app.runtime.session import PendingTurn, get_campaign_runtime

    services = configure_runtime_services()
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_001",
            location="测试地点",
            participants=["主角"],
        ),
        player=PlayerState(name="主角"),
    )
    with services.stores.current_state as store:
        store.save(state)
    services.stores.core_memory.save(CoreMemory(items=[]))

    draft = DirectorNPCDraft(
        name="林澈",
        role="社区医生",
        appearance="常穿深色风衣。",
        personality_trait="见人先问诊，习惯主动掌握对方近况",
        known_abilities=[KnownAbility(name="急救", description="处理外伤与常见急症")],
        core_motivation="保护社区居民。",
        hidden_secret="曾目击一宗未结案件。",
        conflict_style="先协商后行动。",
        social_style="克制而友善。",
        created_turn=1,
    )
    with services.stores.director_npc as store:
        store.save(draft)

    import hashlib
    from app.domain.npc_profile.schemas import NPCProfile

    scene_result = SceneWriterResult(
        scene_text="一名医生走进来。",
        npc_usages=([{"name": "陌生人" if receipt == "unknown" else "林澈",
                     "surfaces": ["医生"], "presence": presence}]
                    if receipt not in {"legacy", "unused"} else []),
        npc_usages_text_hash=(hashlib.sha256("一名医生走进来。".encode()).hexdigest()
                              if receipt != "legacy" else None),
    )
    if receipt == "existing":
        with services.stores.npc_profile as store:
            store.upsert(NPCProfile(name="林澈", role="已确认的职业", disclosure_level="intimate"))
    materials = [DirectorStageMaterial(
        kind=material_kind,
        resource_id=draft.draft_id if material_kind == "npc" else "event-1",
        guidance="让医生参与互动。",
        payload=({"source": "director_npc", "name": "林澈"}
                 if material_kind == "npc" else {"actors": [{
                     "name": "林澈", "ref": {"kind": "npc", "resource_id": draft.draft_id},
                 }]}),
    )]
    pending = make_pending_turn(
        turn_id="turn-staged",
        group_id="turn-staged",
        version_index=1,
        user_input="让林澈进来。",
        difficulty="困难",
        max_chars=1500,
        experience_mode="creator",
        state_before=state,
        scene_result=scene_result,
        director_stage=DirectorStageResult(
            reasoning="引入林澈。",
            materials=materials,
        ),
    )
    prepared = prepare_confirmation_state(
        ConfirmationPipelineInput(
            turn_id="turn-staged",
            pending=pending,
            state_before=state,
            scene_result=scene_result,
            user_input="让林澈进来。",
            turn_index=2,
            stale_message="stale",
        ),
        write_db=receipt != "dry_run",
        scene_text="你独自离开。" if receipt == "edited" else None,
    )

    assert [item.draft_id for item in prepared["confirmed_director_npc_drafts"]] == ([draft.draft_id] if expected else [])
    assert {p.name for p in prepared["state_before_update"].scene.participants} == {
        "主角"
    }
    assert ("林澈" in prepared["confirmation_scene_text"]) == (expected or receipt in {"existing", "dry_run"})
    with services.stores.director_npc as store:
        saved = store.get_by_id(draft.draft_id)
    assert saved.status == ("introduced" if expected else "prepared")
    with services.stores.npc_profile as store:
        profiles = store.list_all()
        assert [profile.name for profile in profiles] == (["林澈"] if expected or receipt == "existing" else [])
        if receipt == "existing":
            assert profiles[0].role == "已确认的职业"
            assert profiles[0].disclosure_level == "intimate"


def test_used_npc_projection_includes_old_profiles_and_omits_unused():
    from app.domain.npc_profile.identity_resolver import project_used_npcs
    from app.domain.npc_profile.schemas import NPCProfile
    from app.domain.scene_writer.schemas import NPCUsage
    profiles = [NPCProfile(name="赵七", role="行商", appearance="灰衣", personality_trait="寡言"),
                NPCProfile(name="苏婉娘", role="掌柜"), NPCProfile(name="未使用人物")]
    usages = [NPCUsage(name=name, surfaces=[surface], presence=presence) for name, surface, presence in [
        ("赵七", "中年汉子", "appeared"), ("苏婉娘", "掌柜", "mentioned_only"),
        ("未匹配人物", "路人", "appeared"),
    ]]
    projected = project_used_npcs(usages, profiles)
    assert [item["name"] for item in projected] == ["赵七", "苏婉娘"]
    assert projected[0]["role"] == "行商"
    assert projected[0]["appearance"] == "灰衣"
    assert projected[0]["personality_trait"] == "寡言"
    assert projected[0]["surfaces"] == ["中年汉子"]
    assert projected[1]["presence"] == "mentioned_only"
