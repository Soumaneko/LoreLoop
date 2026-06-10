"""
State Updater Playground: 修改下方 SCENE_TEXT 后直接运行，测试状态更新效果.

流程: CurrentStateStore.load() → StateUpdater.update() → CurrentStateStore.save()

用法:
    python playground_state.py
    python playground_state.py 2>&1 | tee docs/playground_state_output.md
"""

import json
import textwrap

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.current_state import (
    CurrentState,
    CurrentStateStore,
    NPCState,
    PlayerState,
    ResourcesState,
    SceneState,
    StateUpdater,
)

# ══════════════════════════════════════════════════════════════════
#  修改下面的剧情文本即可测试状态更新
# ══════════════════════════════════════════════════════════════════

SCENE_TEXT = (
    "修道院白色的石墙在月光下泛着柔和的光晕。你没有在那扇大门前停留太久，只是转过身，对着依然站在台阶上目送你们的米莉·奥斯沃斯微微一笑。“好的葡萄酒值得等待，人也一样。”你轻描淡写地说道，手中的折扇指了指刚才战斗的方向，“别忘了那只还会喷火星的绵羊，米莉小姐。当下次葡萄成熟酿成美酒时，我会回来品尝的——希望到时候，你的笑容比酒更甜。”米莉用力地点了点头，双手合十放在胸前，直到你的身影消失在林间小径的尽头。离开修道院的范围后，周围再次安静下来。 深夜的艾尔文森林并不适合赶路，尤其是对于两个刚刚经历过战斗的人来说。你们在离大路不远的一处背风坡找到了一个废弃的营火点。你熟练地用魔法点燃了篝火（虽然你养尊处优，但法师点火总是最方便的）。莎拉·月刃坐在火堆的另一侧，她正在用一块干净的布仔细擦拭匕首上的污渍，银色的长发在火光中跳动。“你很奇怪，人类。” 莎拉突然开口，打破了沉默。她没有抬头，语气平淡，“你明明是个贵族，法术也很……正统。为什么要来这种脏兮兮的地方管闲事？那个女孩，对你来说很重要？”你用魔法制造了两瓶清凉的泉水，递给她一瓶。“重要？或许吧。每一朵在风雨中努力绽放的小花都值得欣赏。”你靠在树干上，尽量让自己坐得舒服些，不弄皱外套，“至于贵族……哈，头衔只是挂在墙上的装饰品，莎拉。在这个世界上，有些故事如果不亲自走出来看，就永远只会是书本上枯燥的文字。”莎拉接过泉水，犹豫了一下，还是喝了一口。她抬起头，那双银色的眼睛在火光下显得格外深邃。“泰达希尔没有这样的贵族。我们的贵族……大多在忙着争论古老的律法。”她顿了顿，似乎在组织语言，“明天进城，我需要注意什么？我听说暴风城的人类很……排外。尤其是对我们这种‘长耳朵’。”"
)

DB_PATH = "data/current_state.db"

# ══════════════════════════════════════════════════════════════════
#  显示辅助
# ══════════════════════════════════════════════════════════════════

W = 72
BAR = "=" * W


def _hdr(title: str) -> None:
    print(f"\n{BAR}")
    print(f"  {title}")
    print(f"{BAR}")


def _show_input() -> None:
    print(f"\n  [INPUT] scene_text:")
    wrapped = textwrap.fill(SCENE_TEXT, width=60, initial_indent="    ", subsequent_indent="    ")
    print(wrapped)


def _show_state(label: str, state: CurrentState) -> None:
    print(f"\n  [{label}]")
    s = state.scene
    p = state.player
    r = state.resources

    print(f"    scene      : {s.scene_id}  [{s.location}] {s.time_label}")
    print(f"                 participants: {s.participants}")
    print(f"                 atmosphere: {s.atmosphere}")
    print(f"    player     : {p.name}  pos={p.position}  status={p.visible_status}")
    print(f"                 intent: {p.intent}")
    if p.condition:
        print(f"                 condition: {p.condition}")
    print(f"    resources  : money={r.money}")
    if r.inventory:
        print(f"                 inventory: {r.inventory}")
    if r.key_items:
        print(f"                 key_items: {r.key_items}")

    if state.npcs:
        print(f"    npcs       :")
        for n in state.npcs:
            parts = [f"{n.name} ({n.role or '-'})"]
            if n.location:
                parts.append(f"loc={n.location}")
            if n.attitude_to_player:
                parts.append(f"attitude={n.attitude_to_player}")
            if n.current_intent:
                parts.append(f"intent={n.current_intent}")
            if n.condition:
                parts.append(f"condition={n.condition}")
            print(f"                 {'  '.join(parts)}")


def _diff_state(before: CurrentState, after: CurrentState) -> list[str]:
    changes: list[str] = []

    if before.scene.location != after.scene.location:
        changes.append(f"scene.location: {before.scene.location} → {after.scene.location}")
    if before.scene.atmosphere != after.scene.atmosphere:
        changes.append(f"scene.atmosphere: {before.scene.atmosphere} → {after.scene.atmosphere}")
    if before.scene.participants != after.scene.participants:
        changes.append(f"scene.participants: {before.scene.participants} → {after.scene.participants}")

    if before.player.position != after.player.position:
        changes.append(f"player.position: {before.player.position} → {after.player.position}")
    if before.player.intent != after.player.intent:
        changes.append(f"player.intent: {before.player.intent} → {after.player.intent}")
    if before.player.condition != after.player.condition:
        changes.append(f"player.condition: {before.player.condition} → {after.player.condition}")
    if before.player.visible_status != after.player.visible_status:
        changes.append(f"player.visible_status: {before.player.visible_status} → {after.player.visible_status}")

    if before.resources.money != after.resources.money:
        changes.append(f"resources.money: {before.resources.money} → {after.resources.money}")
    if before.resources.inventory != after.resources.inventory:
        added = set(after.resources.inventory) - set(before.resources.inventory)
        removed = set(before.resources.inventory) - set(after.resources.inventory)
        if added:
            changes.append(f"resources.inventory +{list(added)}")
        if removed:
            changes.append(f"resources.inventory -{list(removed)}")
    if before.resources.key_items != after.resources.key_items:
        changes.append(f"resources.key_items: {before.resources.key_items} → {after.resources.key_items}")

    before_names = {n.name for n in before.npcs}
    after_names = {n.name for n in after.npcs}
    added_npcs = after_names - before_names
    removed_npcs = before_names - after_names
    if added_npcs:
        changes.append(f"npcs +{added_npcs}")
    if removed_npcs:
        changes.append(f"npcs -{removed_npcs}")

    for before_npc in before.npcs:
        after_npc = next((n for n in after.npcs if n.name == before_npc.name), None)
        if after_npc is None:
            continue
        name = before_npc.name
        if before_npc.attitude_to_player != after_npc.attitude_to_player:
            changes.append(f"npc.{name}.attitude: {before_npc.attitude_to_player} → {after_npc.attitude_to_player}")
        if before_npc.current_intent != after_npc.current_intent:
            changes.append(f"npc.{name}.intent: {before_npc.current_intent} → {after_npc.current_intent}")
        if before_npc.condition != after_npc.condition:
            changes.append(f"npc.{name}.condition: {before_npc.condition} → {after_npc.condition}")
        if before_npc.location != after_npc.location:
            changes.append(f"npc.{name}.location: {before_npc.location} → {after_npc.location}")

    return changes


# ══════════════════════════════════════════════════════════════════
#  主流程
# ══════════════════════════════════════════════════════════════════

def main():
    if not LLMConfig.is_configured():
        print("OPENAI_API_KEY not set. 请在 .env 中配置 OPENAI_API_KEY.")
        return

    _hdr("State Updater Playground: Load → Update → Save")
    _show_input()

    gateway = create_gateway()

    with CurrentStateStore(DB_PATH) as store:
        # MODULE 1: 加载状态
        _hdr("MODULE 1: 从 DB 加载 CurrentState")
        before = store.load_or_default(scene_id="scene_001")
        _show_state("BEFORE", before)
        print(f"\n  [INFO] DB: {DB_PATH}")

        # MODULE 2: LLM 更新
        _hdr("MODULE 2: StateUpdater → 调用 LLM 分析状态变化")
        print(f"\n  [INFO] 正在调用 LLM...")
        updater = StateUpdater(gateway)
        after = updater.update(before, SCENE_TEXT)

        # MODULE 3: 对比差异
        _hdr("MODULE 3: Before / After 对比")
        _show_state("AFTER", after)

        changes = _diff_state(before, after)
        if changes:
            print(f"\n  [CHANGES] {len(changes)} change(s) detected:")
            for c in changes:
                print(f"    {c}")
        else:
            print(f"\n  [CHANGES] no changes detected")

        # MODULE 4: 保存
        _hdr("MODULE 4: 保存更新后的状态到 DB")
        store.save(after)
        print(f"\n  [SAVED] updated state written to {DB_PATH}")


if __name__ == "__main__":
    main()
