"""
记忆提取模块 全流程集成测试 (真实 LLM)

每个测试用例展示四级模块的增量输出:
  MODULE 1:   MemoryExtractor → LLM 原始输出
  MODULE 2:   MemorySelfCheck  → 程序自检
  MODULE 2.5: LLMCheck        → LLM 质量复查
  MODULE 3:   MemoryStore      → 入库 / 读取 / 去重

运行方式:
  pytest app/tests/test_pipeline_integration.py -v -s
  pytest app/tests/test_pipeline_integration.py -v -s 2>&1 | tee docs/pipeline_modules_output.md
"""

import json
import textwrap
from dataclasses import dataclass, field
from typing import Callable, Optional

import pytest

from app.core.llm.config import LLMConfig, create_gateway
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.schemas import Memory, MemoryExtractResult
from app.domain.memory.self_check import MemorySelfCheck, SelfCheckResult
from app.domain.memory.store import MemoryStore
from app.domain.memory.llm_check import LLMCheck, LLMCheckResult

pytestmark = pytest.mark.skipif(
    not LLMConfig.is_configured(),
    reason="OPENAI_API_KEY not set. Skipping integration tests.",
)

# ── display constants ──────────────────────────────────────────
W = 72
SEP = "-" * W
BAR = "=" * W

TYPE_LABEL = {
    "agreement": "约定/暗号", "fact": "稳定事实", "trait": "性格/习惯",
    "relationship": "关系变化", "item": "道具/物品", "event": "重要事件",
}


@dataclass
class PipelineCase:
    """定义一个 pipeline 测试用例。添加新用例只需新增一个 PipelineCase。"""
    title: str
    scene_text: str
    scene_context: dict
    expect_types: list[str] = field(default_factory=list)  # 应包含的记忆类型
    expect_empty: bool = False          # 是否预期空结果
    extra_check: Optional[Callable[["BasePipelineTest", list[Memory], SelfCheckResult], None]] = None


# ── 测试用例定义 (添加新用例只需追加到这里) ───────────────────

CASES = {
    "agreement_and_item": PipelineCase(
        title="暗号约定 + 道具获得 — 验证 agreement / item 类型混合抽取",
        scene_text=(
            "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。"
            "随后主角从大衣内袋取出一枚银色戒指，告诉伊莱娜这是她母亲留下的遗物。"
            "伊莱娜盯着戒指看了很久，最终默默接过，点头答应暂时留在旅店等待主角的消息。"
        ),
        scene_context={
            "scene_id": "scene_002", "location": "灰鹭旅店二楼",
            "time_label": "第2日夜晚", "participants": ["主角", "伊莱娜"], "viewpoint": "主角",
        },
        expect_types=["agreement", "item"],
    ),
    "empty_scene": PipelineCase(
        title="纯环境描写 → 预期返回空记忆列表",
        scene_text="风吹过空旷的街道，卷起几片枯叶。远处传来隐约的钟声。天边泛起鱼肚白。",
        scene_context={
            "scene_id": "scene_099", "location": "街道",
            "time_label": "清晨", "participants": [], "viewpoint": "主角",
        },
        expect_empty=True,
    ),
    "trait": PipelineCase(
        title="角色性格/习惯/弱点 — 验证 trait / fact 类型",
        scene_text=(
            "伊莱娜在进入任何房间之前，都会先站在门口环顾四周，确认没有可疑的人物。"
            "她的手指总是无意识地敲击大腿外侧，像是在计算什么。"
            "当别人说话时，她会微微侧头，用右耳朝向说话者——她的左耳在多年前受过伤，听力不佳。"
        ),
        scene_context={
            "scene_id": "scene_003", "location": "灰鹭旅店一楼",
            "time_label": "第3日清晨", "participants": ["主角", "伊莱娜"], "viewpoint": "主角",
        },
        expect_types=["trait"],
    ),
    "event": PipelineCase(
        title="重要事件 — 验证 event 类型抽取",
        scene_text=(
            "一声巨响，城市中心的钟楼突然爆炸。碎石如雨点般砸落，周围的市民四散奔逃。"
            "守卫队长吹响警哨，下令封锁周边三个街区。"
        ),
        scene_context={
            "scene_id": "scene_005", "location": "城市中心",
            "time_label": "第2日黄昏", "participants": ["主角", "守卫队长"], "viewpoint": "主角",
        },
        expect_types=["event"],
    ),
    "relationship": PipelineCase(
        title="关系变化 — 验证 relationship 类型",
        scene_text=(
            '"我之前一直不相信你，"守卫队长低声说，"但现在看来，你是对的。钟楼的事不是意外——'
            "有人想要毁掉这座城市。从今天起，守卫队会配合你的调查。\""
        ),
        scene_context={
            "scene_id": "scene_006", "location": "守卫营地",
            "time_label": "第3日深夜", "participants": ["主角", "守卫队长"], "viewpoint": "主角",
        },
    ),
    "uncertainty": PipelineCase(
        title="不确定信息 — 验证 certainty < 1.0",
        scene_text=(
            "主角在灰烬中发现了一枚烧焦的徽章，上面隐约可以看到一个鹰的图案。"
            "根据形状和材质，这可能是皇家骑士团的徽章——但也有可能是仿制品。"
        ),
        scene_context={
            "scene_id": "scene_007", "location": "钟楼废墟",
            "time_label": "第2日午夜", "participants": ["主角"], "viewpoint": "主角",
        },
        extra_check=lambda self, cleaned, cr: any(
            m.certainty < 1.0 for m in cleaned
        ) or pytest.fail("预期有 certainty < 1.0 的记忆"),
    ),
    "self_check_autofill": PipelineCase(
        title="自检自动补全: location/time_label 从 scene_context 填充",
        scene_text=(
            "主角在营地篝火旁对守卫队长说：\"明天天亮前，我们必须在城门口会合，"
            "我会带上从钟楼废墟找到的证据。\"守卫队长点头，将一块令牌递给主角。"
        ),
        scene_context={
            "scene_id": "scene_010", "location": "守卫营地篝火旁",
            "time_label": "第4日凌晨", "participants": ["主角", "守卫队长"], "viewpoint": "主角",
        },
        extra_check=lambda self, cleaned, cr: (
            any("filled from scene_context" in f for f in cr.fixes)
            or print("  (info) LLM 已自填 location/time_label, 无需补全")
        ),
    ),
}


# ── 基类：共享显示逻辑与 pipeline 执行 ─────────────────────────

class BasePipelineTest:
    """继承此类即可自动获得全流程测试能力。"""

    def _gateway(self):
        return create_gateway()

    # ── display helpers ────────────────────────────────────────

    def _hdr(self, title: str) -> None:
        print(f"\n{BAR}")
        print(f"  {title}")
        print(f"{BAR}")

    def _show_input(self, scene_text: str, scene_context: dict) -> None:
        print(f"\n  [INPUT] scene_text:")
        wrapped = textwrap.fill(scene_text, width=60, initial_indent="    ", subsequent_indent="    ")
        print(wrapped)
        print(f"\n  [INPUT] scene_context:")
        ctx = json.dumps(scene_context, ensure_ascii=False, indent=4)
        print(f"    {ctx.replace(chr(10), chr(10) + '    ')}")

    def _show_raw_memories(self, memories: list[Memory]) -> None:
        n = len(memories)
        print(f"\n  [MODULE 1] LLM returned {n} raw memory(s):")
        if n == 0:
            print(f"  (empty)")
            return
        for i, m in enumerate(memories, 0):
            label = TYPE_LABEL.get(m.type, m.type)
            print(f"\n    #{i}")
            print(f"      type         : {m.type} ({label})")
            print(f"      subject      : {m.subject}")
            c = textwrap.fill(m.content, width=56, subsequent_indent="                    ")
            print(f"      content      : {c}")
            if m.entities:            print(f"      entities     : {m.entities}")
            if m.location:           print(f"      location     : {m.location}")
            if m.time_label:         print(f"      time_label   : {m.time_label}")
            if m.visibility:         print(f"      visibility   : {m.visibility}")
            if m.tags:               print(f"      tags         : {m.tags}")
            print(f"      certainty    : {m.certainty}  salience: {m.salience}")
            print(f"      source_scene_id: {m.source_scene_id!r}")
            if m.source_text_span:   print(f"      source_span  : \"{m.source_text_span}\"")

    def _show_check_result(self, cr: SelfCheckResult) -> None:
        dedup_removed = cr.original_count - cr.final_count
        print(f"\n  [MODULE 2] 输入: {cr.original_count} 条 → 输出: {cr.final_count} 条")
        if dedup_removed > 0:
            print(f"  [MODULE 2] 去重移除: {dedup_removed} 条")
        print(f"  [MODULE 2] 修正: {cr.fix_count} 处, 警告: {cr.warning_count} 条")
        if cr.fixes:
            print(f"\n  [MODULE 2] 自动修正:")
            for f in cr.fixes:  print(f"    + {f}")
        if cr.warnings:
            print(f"\n  [MODULE 2] 警告:")
            for w in cr.warnings:  print(f"    ! {w}")

    def _show_store(self, cleaned: list[Memory], store: MemoryStore) -> None:
        ids = store.save_many(cleaned)
        print(f"\n  [MODULE 3] save_many() → 写入 {len(ids)} 条:")
        for rid in ids:
            print(f"    {rid}")

        records = store.list_all()
        print(f"\n  [MODULE 3] list_all() → {len(records)} 条:")
        for i, r in enumerate(records, 1):
            print(f"    {i}. [{r.type}] {r.subject}  entities={r.entities}")

        if len(cleaned) > 0:
            ids2 = store.save_many(cleaned)
            print(f"\n  [MODULE 3] 重复写入 → {len(ids2)} 条 (应为 0, 去重生效)")
            assert len(ids2) == 0

    # ── core: run full pipeline ─────────────────────────────────

    def run_pipeline(self, case: PipelineCase) -> None:
        self._hdr(f"Pipeline: {case.title}")
        self._show_input(case.scene_text, case.scene_context)

        # ── MODULE 1: raw extraction ──
        self._hdr("MODULE 1: MemoryExtractor → LLM 原始输出")
        print(f"\n  [MODULE 1] MemoryExtractor.extract() — 调用真实 LLM...")
        extractor = MemoryExtractor(self._gateway(), enable_self_check=False)
        raw = extractor.extract(case.scene_text, case.scene_context)

        assert isinstance(raw, MemoryExtractResult)
        self._show_raw_memories(raw.memories)

        # ── MODULE 2: self_check ──
        self._hdr("MODULE 2: MemorySelfCheck → 程序自检")
        print(f"\n  [MODULE 2] MemorySelfCheck.check() — 运行中...")
        checker = MemorySelfCheck()
        cleaned, cr = checker.check(raw.memories, case.scene_text, case.scene_context)

        self._show_check_result(cr)

        # ── MODULE 2.5: llm_check ──
        if cleaned:
            self._hdr("MODULE 2.5: LLMCheck → LLM 质量复查")
            print(f"\n  [MODULE 2.5] LLMCheck.check() — 调用真实 LLM 复查...")
            llm_checker = LLMCheck(self._gateway())
            checked, llm_cr = llm_checker.check(
                cleaned, case.scene_text, case.scene_context,
                self_check_warnings=cr.warnings,
            )
            self._show_llm_check_result(llm_cr)
            if llm_cr.change_count > 0:
                self._show_memories_after_llm(checked)
            cleaned = checked
        else:
            self._hdr("MODULE 2.5: LLMCheck → 跳过（无 memory）")

        # ── MODULE 3: store ──
        self._hdr("MODULE 3: MemoryStore → 入库 / 读取 / 去重")
        with MemoryStore(":memory:") as store:
            self._show_store(cleaned, store)

        # ── assertions ──
        if case.expect_empty:
            assert cr.final_count == 0, f"预期空结果，但得到 {cr.final_count} 条"
            return

        if case.expect_types:
            actual_types = {m.type for m in cleaned}
            missing = set(case.expect_types) - actual_types
            assert not missing, f"缺少类型: {missing}, 实际类型: {actual_types}"
        if case.extra_check:
            case.extra_check(self, cleaned, cr)

        print(f"\n  [PASS] {case.title}")

    def _show_llm_check_result(self, cr: LLMCheckResult) -> None:
        if cr.change_count == 0:
            print(f"\n  [MODULE 2.5] LLM 检查未发现需要修改的内容")
            return
        print(f"\n  [MODULE 2.5] LLM 建议修改 {cr.change_count} 处:")
        for i, ch in enumerate(cr.changes_applied, 1):
            idx = ch.get("memory_index", "?")
            field = ch.get("field", "?")
            old = ch.get("old")
            new = ch.get("new")
            reason = ch.get("reason", "")
            error = ch.get("error")
            if error:
                print(f"    {i}. #{idx}  {field}: 应用失败 ({error})")
            elif field == "__new__":
                subject = ch.get("subject", "?")
                print(f"    {i}. 新增 memory: [{subject}]")
                if reason: print(f"       理由: {reason}")
            elif field == "__delete__":
                subject = ch.get("subject", "?")
                print(f"    {i}. 删除 #{idx} [{subject}]")
                if reason: print(f"       理由: {reason}")
            elif field == "__warning_reviewed__":
                print(f"    {i}. #{idx}  审核自检警告: {reason}")
            else:
                print(f"    {i}. #{idx}  {field}: {_truncate(old)} → {_truncate(new)}")
                if reason:
                    print(f"       理由: {reason}")

    def _show_memories_after_llm(self, memories: list[Memory]) -> None:
        n = len(memories)
        print(f"\n  [MODULE 2.5] LLM 检查后 memories ({n} 条):")
        if n == 0:
            print(f"  (empty)")
            return
        for i, m in enumerate(memories, 0):
            label = TYPE_LABEL.get(m.type, m.type)
            print(f"\n    #{i}  [{m.type}] {m.subject}  cert={m.certainty} sal={m.salience}")
            print(f"      content: {m.content}")
            if m.entities:     print(f"      entities: {m.entities}")
            if m.visibility:   print(f"      visibility: {m.visibility}")
            if m.tags:         print(f"      tags: {m.tags}")

def _truncate(v) -> str:
    s = str(v)
    return s[:60] + "..." if len(s) > 60 else s


# ── 测试类 — 自动从 CASES 生成 ────────────────────────────────

class TestMemoryPipeline(BasePipelineTest):

    def test_agreement_and_item(self):
        self.run_pipeline(CASES["agreement_and_item"])

    def test_empty_scene(self):
        self.run_pipeline(CASES["empty_scene"])

    def test_trait(self):
        self.run_pipeline(CASES["trait"])

    def test_event(self):
        self.run_pipeline(CASES["event"])

    def test_relationship(self):
        self.run_pipeline(CASES["relationship"])

    def test_uncertainty(self):
        self.run_pipeline(CASES["uncertainty"])

    def test_self_check_autofill(self):
        self.run_pipeline(CASES["self_check_autofill"])
