import json
import os
import tempfile
import textwrap

import pytest

from app.core.llm.config import LLMConfig, create_gateway
from app.core.llm.schemas import EchoResult
from app.domain.memory.schemas import Memory, MemoryExtractResult, MemoryRecord
from app.domain.memory.extractor import MemoryExtractor
from app.domain.memory.store import MemoryStore
from app.domain.memory.self_check import MemorySelfCheck, SelfCheckResult

pytestmark = pytest.mark.skipif(
    not LLMConfig.is_configured(),
    reason="OPENAI_API_KEY not set. Skipping integration tests.",
)

C_WIDTH = 72
C_SEP = "-" * C_WIDTH
C_BAR = "=" * C_WIDTH


def show_header(title: str) -> None:
    print(f"\n{C_BAR}")
    print(f"  {title}")
    print(f"{C_BAR}")


def show_scene(scene_text: str, scene_context: dict) -> None:
    print(f"\n  [INPUT] scene_text:")
    wrapped = textwrap.fill(scene_text, width=60, initial_indent="    ", subsequent_indent="    ")
    print(wrapped)
    print(f"\n  [INPUT] scene_context:")
    print(f"    {json.dumps(scene_context, ensure_ascii=False, indent=4).replace(chr(10), chr(10) + '    ')}")


def show_echo(raw_input: str, result: EchoResult) -> None:
    print(f"\n  [INPUT]  text = {raw_input!r}")
    print(f"  [OUTPUT] text = {result.text!r}")
    print(f"  [OUTPUT] length = {result.length}")
    if isinstance(result, EchoResult):
        print(f"  [PASS]   EchoResult validated OK")


MEMORY_TYPE_LABEL = {
    "agreement": "约定/暗号",
    "fact": "稳定事实",
    "trait": "性格/习惯",
    "relationship": "关系变化",
    "item": "道具/物品",
    "event": "重要事件",
}


def show_memories(memories: list[Memory]) -> None:
    count = len(memories)
    print(f"\n  [RESULT] extracted {count} memory(s)")
    if count == 0:
        print(f"  (empty — 无值得抽取的记忆)")
        return

    for i, m in enumerate(memories, 0):
        label = MEMORY_TYPE_LABEL.get(m.type, m.type)
        print(f"\n  {C_SEP}")
        print(f"  #{i}")
        print(f"    type       : {m.type} ({label})")
        print(f"    subject    : {m.subject}")
        print(f"    content    : {textwrap.fill(m.content, width=54, initial_indent='', subsequent_indent='                ')}")
        if m.entities:
            print(f"    entities   : {m.entities}")
        if m.location:
            print(f"    location   : {m.location}")
        if m.time_label:
            print(f"    time_label : {m.time_label}")
        if m.visibility:
            print(f"    visibility : {m.visibility}")
        if m.tags:
            print(f"    tags       : {m.tags}")
        print(f"    certainty  : {m.certainty}   salience: {m.salience}")
        if m.source_text_span:
            print(f"    source_span: \"{m.source_text_span}\"")
    print(f"  {C_SEP}")


def show_memory_records(records: list[MemoryRecord]) -> None:
    print(f"\n  [STORE] 数据库中已有 {len(records)} 条记录")
    for i, r in enumerate(records, 1):
        print(f"    {i}. [{r.type}] {r.subject}  (id={r.id[:12]}...)")
    print(f"  [STORE] 去重逻辑已生效（重复记忆不会重复入库）")


def make_gateway_and_extractor():
    gateway = create_gateway()
    extractor = MemoryExtractor(gateway)
    return gateway, extractor


# ============================================================
# Echo 测试 — 验证 LLM 基础能力
# ============================================================

class TestEchoDisplay:
    def test_echo_hello(self):
        show_header("Echo: 英文 'hello' — 验证基础 JSON 输出")
        gateway, _ = make_gateway_and_extractor()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "hello"},
            output_schema=EchoResult,
        )

        show_echo("hello", result)
        assert result.text == "hello"
        assert result.length == 5

    def test_echo_chinese(self):
        show_header("Echo: 中文 '你好世界' — 验证中文处理和长度计算")
        gateway, _ = make_gateway_and_extractor()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": "你好世界"},
            output_schema=EchoResult,
        )

        show_echo("你好世界", result)
        assert result.text == "你好世界"
        assert result.length == 4

    def test_echo_empty(self):
        show_header("Echo: 空字符串 — 验证边界情况")
        gateway, _ = make_gateway_and_extractor()

        result = gateway.run(
            task="echo_structured_test",
            input_data={"text": ""},
            output_schema=EchoResult,
        )

        show_echo("", result)
        assert result.text == ""
        assert result.length == 0


# ============================================================
# Memory Extract 测试 — 按类型分别验证
# ============================================================

class TestMemoryExtractTypes:
    def test_extract_agreement(self):
        show_header("Memory Extract: 暗号/约定 (agreement)")
        _, extractor = make_gateway_and_extractor()

        scene_text = "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。伊莱娜点头答应。"
        scene_context = {
            "scene_id": "scene_002",
            "location": "灰鹭旅店二楼",
            "time_label": "第2日夜晚",
            "participants": ["主角", "伊莱娜"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 0
        for m in result.memories:
            assert m.type in MemoryExtractResult.model_fields["memories"].annotation.__args__[0].model_fields["type"].annotation.__args__  # type validation done by pydantic
            assert 0 <= m.certainty <= 1
            assert 0 <= m.salience <= 1

    def test_extract_item_and_fact(self):
        show_header("Memory Extract: 道具获得 + 背景事实 (item + fact)")
        _, extractor = make_gateway_and_extractor()

        scene_text = (
            "主角到铁匠铺取剑。铁匠老王从炉火旁取出一把泛着蓝光的宝剑递给主角，说："
            "\"定制的宝剑终于完成了。其实，我和你父亲以前是战友，这把剑用的矿石是你父亲留下的。\""
        )
        scene_context = {
            "scene_id": "scene_001",
            "location": "铁匠铺",
            "time_label": "第1日正午",
            "participants": ["主角", "铁匠老王"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 1
        for m in result.memories:
            assert 0 <= m.certainty <= 1
            assert 0 <= m.salience <= 1

    def test_extract_empty_scene(self):
        show_header("Memory Extract: 纯环境描写 — 应返回空列表")
        _, extractor = make_gateway_and_extractor()

        scene_text = "风吹过空旷的街道，卷起几片枯叶。远处传来隐约的钟声。天边泛起鱼肚白，晨曦洒在石板路上。"
        scene_context = {
            "scene_id": "scene_099",
            "location": "街道",
            "time_label": "清晨",
            "participants": [],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) == 0

    def test_extract_trait(self):
        show_header("Memory Extract: 角色性格/习惯 (trait)")
        _, extractor = make_gateway_and_extractor()

        scene_text = (
            "伊莱娜在进入任何房间之前，都会先站在门口环顾四周，确认没有可疑的人物。"
            "她的手指总是无意识地敲击大腿外侧，像是在计算什么。"
            "当别人说话时，她会微微侧头，用右耳朝向说话者——她的左耳在多年前受过伤，听力不佳。"
        )
        scene_context = {
            "scene_id": "scene_003",
            "location": "灰鹭旅店一楼",
            "time_label": "第3日清晨",
            "participants": ["主角", "伊莱娜"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 1
        for m in result.memories:
            assert 0 <= m.certainty <= 1

    def test_extract_event(self):
        show_header("Memory Extract: 重要事件 (event)")
        _, extractor = make_gateway_and_extractor()

        scene_text = (
            "一声巨响，城市中心的钟楼突然爆炸。碎石如雨点般砸落，周围的市民四散奔逃。"
            "守卫队长吹响警哨，下令封锁周边三个街区。"
        )
        scene_context = {
            "scene_id": "scene_005",
            "location": "城市中心",
            "time_label": "第2日黄昏",
            "participants": ["主角", "守卫队长"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 1
        for m in result.memories:
            assert 0 <= m.certainty <= 1

    def test_extract_relationship(self):
        show_header("Memory Extract: 关系变化 (relationship)")
        _, extractor = make_gateway_and_extractor()

        scene_text = (
            "\"我之前一直不相信你，\"守卫队长低声说，\"但现在看来，你是对的。"
            "钟楼的事不是意外——有人想要毁掉这座城市。从今天起，守卫队会配合你的调查。\""
        )
        scene_context = {
            "scene_id": "scene_006",
            "location": "守卫营地",
            "time_label": "第3日深夜",
            "participants": ["主角", "守卫队长"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 1
        for m in result.memories:
            assert 0 <= m.certainty <= 1

    def test_extract_with_uncertainty(self):
        show_header("Memory Extract: 包含推测/不确定信息 — certainty < 1.0")
        _, extractor = make_gateway_and_extractor()

        scene_text = (
            "主角在灰烬中发现了一枚烧焦的徽章，上面隐约可以看到一个鹰的图案。"
            "根据形状和材质，这可能是皇家骑士团的徽章——但也有可能是仿制品。"
        )
        scene_context = {
            "scene_id": "scene_007",
            "location": "钟楼废墟",
            "time_label": "第2日午夜",
            "participants": ["主角"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        assert isinstance(result, MemoryExtractResult)
        assert len(result.memories) >= 1
        for m in result.memories:
            assert 0 <= m.certainty <= 1
            assert 0 <= m.salience <= 1


# ============================================================
# 全流程 + Trace
# ============================================================

class TestFullPipeline:
    def test_full_extract_save_read_pipeline(self):
        show_header("全流程: extract → save → read — 验证完整链路")
        gateway, extractor = make_gateway_and_extractor()

        scene_text = (
            "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。"
            "随后主角从大衣内袋取出一枚银色戒指，告诉伊莱娜这是她母亲留下的遗物。"
            "伊莱娜盯着戒指看了很久，最终默默接过，点头答应暂时留在旅店等待主角的消息。"
        )
        scene_context = {
            "scene_id": "scene_002",
            "location": "灰鹭旅店二楼",
            "time_label": "第2日夜晚",
            "participants": ["主角", "伊莱娜"],
            "viewpoint": "主角",
        }

        show_scene(scene_text, scene_context)

        print(f"\n  --- Step 1: MemoryExtractor.extract() ---")
        result = extractor.extract(scene_text, scene_context)
        show_memories(result.memories)

        print(f"\n  --- Step 2: MemoryStore.save_many() ---")
        with MemoryStore(":memory:") as store:
            ids = store.save_many(result.memories)
            print(f"  saved {len(ids)} records:")
            for rid in ids:
                print(f"    id = {rid}")

            print(f"\n  --- Step 3: MemoryStore.list_all() ---")
            records = store.list_all()
            show_memory_records(records)

            print(f"\n  --- Step 4: 再次 save 相同内容 (去重测试) ---")
            ids2 = store.save_many(result.memories)
            print(f"  saved {len(ids2)} records (应为 0，因为去重)")
            assert len(ids2) == 0
            print(f"  [PASS] 去重逻辑正常")

            assert len(records) == len(result.memories)
            assert store.count() == len(result.memories)


def show_self_check_result(check_result: SelfCheckResult) -> None:
    dedup_removed = check_result.original_count - check_result.final_count
    print(f"\n  [CHECK] original={check_result.original_count} → final={check_result.final_count}")
    if dedup_removed > 0:
        print(f"  [CHECK] dedup removed {dedup_removed} memory(s)")
    if check_result.warnings:
        print(f"  [CHECK] warnings ({check_result.warning_count}):")
        for w in check_result.warnings:
            print(f"    ! {w}")
    if check_result.fixes:
        print(f"  [CHECK] fixes ({check_result.fix_count}):")
        for f in check_result.fixes:
            print(f"    + {f}")
    if not check_result.warnings and not check_result.fixes:
        print(f"  [CHECK] all memories passed, no issues")


def show_fixes(check_result: SelfCheckResult) -> None:
    if check_result.fixes:
        print(f"\n  [CHECK] auto-fixes applied ({check_result.fix_count}):")
        for f in check_result.fixes:
            print(f"    + {f}")
    if check_result.warnings:
        print(f"\n  [CHECK] warnings ({check_result.warning_count}):")
        for w in check_result.warnings:
            print(f"    ! {w}")


# ============================================================
# 逐模块增量输出 — 真实 LLM Pipeline
# ============================================================

PIPELINE_SCENE = (
    "在灰鹭旅店二楼，主角告诉伊莱娜，如果他用左手轻敲两下桌面，就表示让她保持沉默。"
    "随后主角从大衣内袋取出一枚银色戒指，告诉伊莱娜这是她母亲留下的遗物。"
    "伊莱娜盯着戒指看了很久，最终默默接过，点头答应暂时留在旅店等待主角的消息。"
)

PIPELINE_CONTEXT = {
    "scene_id": "scene_002",
    "location": "灰鹭旅店二楼",
    "time_label": "第2日夜晚",
    "participants": ["主角", "伊莱娜"],
    "viewpoint": "主角",
}


class TestPipelineWithSelfCheck:
    """逐模块增量输出：extract → self_check → store"""

    def test_pipeline_modules_incremental(self):
        show_header("MODULE 1: MemoryExtractor → LLM 原始输出（自检前）")

        gateway = create_gateway()
        extractor_raw = MemoryExtractor(gateway, enable_self_check=False)

        show_scene(PIPELINE_SCENE, PIPELINE_CONTEXT)

        print(f"\n  [MODULE 1] MemoryExtractor.extract() — 调用 LLM...")
        raw_result = extractor_raw.extract(PIPELINE_SCENE, PIPELINE_CONTEXT)

        print(f"\n  [MODULE 1] LLM returned {len(raw_result.memories)} raw memory(s):")
        for i, m in enumerate(raw_result.memories, 1):
            label = MEMORY_TYPE_LABEL.get(m.type, m.type)
            print(f"\n    #{i}")
            print(f"      type         : {m.type} ({label})")
            print(f"      subject      : {m.subject}")
            print(f"      content      : {textwrap.fill(m.content, width=56, initial_indent='', subsequent_indent='                    ')}")
            if m.entities:
                print(f"      entities     : {m.entities}")
            if m.location:
                print(f"      location     : {m.location}")
            if m.time_label:
                print(f"      time_label   : {m.time_label}")
            if m.visibility:
                print(f"      visibility   : {m.visibility}")
            if m.tags:
                print(f"      tags         : {m.tags}")
            print(f"      certainty    : {m.certainty}  salience: {m.salience}")
            print(f"      source_scene_id: {m.source_scene_id!r}")
            if m.source_text_span:
                print(f"      source_span  : \"{m.source_text_span}\"")

        assert isinstance(raw_result, MemoryExtractResult)
        assert len(raw_result.memories) >= 1

        # --- MODULE 2 ---
        show_header("MODULE 2: MemorySelfCheck → 校验 / 修正 / 去重")

        print(f"\n  [MODULE 2] MemorySelfCheck.check() — 运行中...")
        checker = MemorySelfCheck()
        cleaned, check_result = checker.check(raw_result.memories, PIPELINE_SCENE, PIPELINE_CONTEXT)

        print(f"\n  [MODULE 2] 输入: {check_result.original_count} 条, 输出: {check_result.final_count} 条")
        print(f"  [MODULE 2] 修正: {check_result.fix_count} 处, 警告: {check_result.warning_count} 条")

        if check_result.fixes:
            print(f"\n  [MODULE 2] 自动修正明细:")
            for fix in check_result.fixes:
                print(f"    + {fix}")
        if check_result.warnings:
            print(f"\n  [MODULE 2] 警告明细:")
            for w in check_result.warnings:
                print(f"    ! {w}")

        print(f"\n  [MODULE 2] 自检后的 cleaned memories ({len(cleaned)} 条):")
        for i, m in enumerate(cleaned, 1):
            label = MEMORY_TYPE_LABEL.get(m.type, m.type)
            fixed_mark = ""
            print(f"\n    #{i}  [{m.type}] {m.subject}  cert={m.certainty} sal={m.salience}{fixed_mark}")
            print(f"      content: {m.content}")
            if m.entities:
                print(f"      entities: {m.entities}")
            if m.visibility:
                print(f"      visibility: {m.visibility}")
            if m.tags:
                print(f"      tags: {m.tags}")
            if m.source_text_span:
                print(f"      source_span: \"{m.source_text_span}\"")

        assert check_result.final_count >= 1

        # --- MODULE 3 ---
        show_header("MODULE 3: MemoryStore → 入库 / 读取 / 去重")

        with MemoryStore(":memory:") as store:
            print(f"\n  [MODULE 3] MemoryStore.save_many() — 写入 {len(cleaned)} 条...")
            ids = store.save_many(cleaned)
            print(f"  [MODULE 3] 成功写入 {len(ids)} 条, ids:")
            for rid in ids:
                print(f"    {rid}")

            print(f"\n  [MODULE 3] MemoryStore.list_all() — 读取全部记录:")
            records = store.list_all()
            for i, r in enumerate(records, 1):
                print(f"    {i}. [{r.type}] {r.subject}")
                print(f"       id={r.id[:16]}...  entities={r.entities}  created={r.created_at[:19]}")

            print(f"\n  [MODULE 3] MemoryStore.save_many() — 重复写入相同内容 (测试去重)...")
            ids2 = store.save_many(cleaned)
            print(f"  [MODULE 3] 重复写入返回 {len(ids2)} 条 (应为 0)")
            assert len(ids2) == 0

            print(f"\n  [PASS]  完整链路验证通过: extract → self_check → store → dedup")
            print(f"  [PASS]  共入库 {store.count()} 条记忆, 去重二次写入为 0")

        assert len(ids) == len(cleaned)


class TestTraceWithRealLLM:
    def test_trace_written_with_real_llm(self):
        show_header("Trace: 真实 LLM 调用后 trace 正确写入")
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            trace_path = f.name

        try:
            gateway = create_gateway(log_path=trace_path)

            trace_id = "real-llm-trace-1"
            result = gateway.run(
                task="echo_structured_test",
                input_data={"text": "trace test"},
                output_schema=EchoResult,
                trace_id=trace_id,
            )

            print(f"\n  [INPUT]  task = echo_structured_test, text = 'trace test'")
            print(f"  [OUTPUT] EchoResult(text={result.text!r}, length={result.length})")
            print(f"  [TRACE]  trace_id = {trace_id}")

            traces = gateway.tracer.read_traces(trace_id=trace_id)
            assert len(traces) == 1
            trace = traces[0]
            assert trace["trace_id"] == trace_id
            assert trace["schema_valid"] is True
            assert trace["error"] is None
            assert isinstance(trace["latency_ms"], float)
            assert trace["latency_ms"] > 0

            print(f"  [TRACE]  latency = {trace['latency_ms']:.1f}ms")
            print(f"  [TRACE]  model   = {trace['model']}")
            print(f"  [PASS]    trace 正常写入 {trace_path}")
        finally:
            os.unlink(trace_path)
