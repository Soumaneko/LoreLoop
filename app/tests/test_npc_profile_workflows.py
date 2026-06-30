from app.domain.current_state.schemas import CurrentState, NPCState, PlayerState, SceneState
from app.domain.npc_profile.schemas import NPCProfile
from app.domain.npc_profile.store import NPCProfileStore
from app.domain.npc_profile.workflows import sync_npc_profile_absence_from_current_state


def test_sync_npc_profile_absence_resets_current_state_hits_and_increments_misses():
    store = NPCProfileStore(":memory:")
    present_id = store.upsert(
        NPCProfile(name="裴玉奴", aliases=["裴姑娘"], role="琵琶名手", absent_turns=2)
    )
    missing_id = store.upsert(
        NPCProfile(name="周泰", role="巡检司都头", absent_turns=1)
    )
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_005",
            location="鸣玉坊里间",
            participants=["萧云痕", "裴姑娘"],
        ),
        player=PlayerState(name="萧云痕", position="鸣玉坊里间"),
        npcs=[NPCState(name="裴玉奴", role="琵琶名手")],
    )

    saved_ids = sync_npc_profile_absence_from_current_state(
        npc_store=store,
        current_state=state,
    )

    assert set(saved_ids) == {present_id, missing_id}
    assert store.get_by_id(present_id).absent_turns == 0
    assert store.get_by_id(missing_id).absent_turns == 2


def test_sync_npc_profile_absence_ignores_absent_current_state_npcs():
    store = NPCProfileStore(":memory:")
    absent_id = store.upsert(
        NPCProfile(name="裴玉奴", aliases=["裴姑娘"], role="琵琶名手", absent_turns=1)
    )
    present_id = store.upsert(
        NPCProfile(name="花想容", role="胭脂铺掌柜", absent_turns=2)
    )
    state = CurrentState(
        scene=SceneState(
            scene_id="scene_008",
            location="白鹤巷第三进院外墙下",
            participants=["萧云痕", "花想容"],
        ),
        player=PlayerState(name="萧云痕", position="白鹤巷第三进院外墙下"),
        npcs=[
            NPCState(name="裴玉奴", role="琵琶名手", absent_turns=1),
            NPCState(name="花想容", role="胭脂铺掌柜", absent_turns=0),
        ],
    )

    saved_ids = sync_npc_profile_absence_from_current_state(
        npc_store=store,
        current_state=state,
    )

    assert set(saved_ids) == {absent_id, present_id}
    assert store.get_by_id(absent_id).absent_turns == 2
    assert store.get_by_id(present_id).absent_turns == 0
