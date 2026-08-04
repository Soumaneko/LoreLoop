#!/usr/bin/env python3
"""临时调试脚本：用真实 LLM 走 opening_foundation → opening_setting_requirements → setting creation。

用法：
  OPENAI_API_KEY=... PYTHONPATH=. uv run python scripts/debug_opening_pipeline.py \
      --core-memory core_memory.json [--model gpt-4o] [--out result.json]

未提供 --core-memory 时使用内置示例 CoreMemory（由 config + campaign_setup 构建）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.core.llm.config import create_gateway
from app.domain.core_memory import (
    CampaignSetupProposal,
    CoreMemory,
    InitConfig,
    build_core_memory,
)
from app.domain.opening_foundation import OpeningFoundationAgent
from app.domain.opening_setting import OpeningSettingAgent
from app.domain.world_setting import WorldSetting, WorldSettingCreator

DEFAULT_CONFIG = {
    "version": 3,
    "director_enabled": True,
    "world_name": "艾兰",
    "world_style": "西幻",
    "ability_system": "魔法",
    "protagonist_name": "卢西安",
    "story_expectation": "探索、成长并建立真实关系。",
    "failure_severity": "适中",
}

DEFAULT_CAMPAIGN_SETUP = {
    "version": 2,
    "currency_unit": "金币",
    "public_power_baseline": {
        "source": "魔法",
        "prevalence": "少数人掌握",
        "learning_difficulty": "困难",
        "limitation": "不能复活死者。",
    },
    "peoples_and_beings": ["人类", "精灵"],
    "major_world_conflicts": ["帝国扩张持续挤压边境诸国。"],
    "dominant_powers": ["北方帝国", "圣辉教会"],
    "civilization_baseline": "以马车、冷兵器和手工业为主。",
}


def _load_core_memory(path: str) -> CoreMemory:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return CoreMemory.model_validate(payload)


def _sample_core_memory() -> CoreMemory:
    config = InitConfig.model_validate(DEFAULT_CONFIG)
    proposal = CampaignSetupProposal.model_validate(DEFAULT_CAMPAIGN_SETUP)
    return build_core_memory(config, proposal)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="走 opening_foundation + opening_setting_requirements 管线（真实 LLM）"
    )
    parser.add_argument("--core-memory", help="CoreMemory v2 JSON 文件")
    parser.add_argument("--model", default=None, help="模型名，覆盖 OPENAI_MODEL")
    parser.add_argument("--out", help="把结果写入该 JSON 文件")
    args = parser.parse_args()

    if args.core_memory:
        core_memory = _load_core_memory(args.core_memory)
    else:
        core_memory = _sample_core_memory()

    try:
        gateway = create_gateway(model=args.model)
    except RuntimeError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        sys.exit(1)

    kinds: dict[str, int] = {}
    for item in core_memory.items:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    print("== CoreMemory ==")
    print(f"items: {len(core_memory.items)}  kinds: {json.dumps(kinds, ensure_ascii=False)}")

    print("\n== opening_foundation ==")
    foundation = OpeningFoundationAgent(gateway).generate(
        core_memory=core_memory,
        trace_id="debug-opening-foundation",
    )
    print(json.dumps(foundation.model_dump(mode="json"), ensure_ascii=False, indent=2))

    print("\n== opening_setting_requirements ==")
    specs = OpeningSettingAgent(gateway).generate(
        foundation=foundation,
        core_memory=core_memory,
        trace_id="debug-opening-settings",
    )
    for spec in specs.specs:
        print(f"- [{spec.kind}] {spec.setting_description}")
    print(json.dumps(specs.model_dump(mode="json"), ensure_ascii=False, indent=2))

    print("\n== setting creation ==")
    creator = WorldSettingCreator(gateway)
    created: list[WorldSetting] = []
    for spec in specs.specs:
        setting = creator.create(
            kind=spec.kind,
            guidance=spec.setting_description,
            core_memory=core_memory,
            existing_settings=[],
            turn_index=0,
            trace_id=f"debug-opening-create-{spec.kind}",
        )
        created.append(setting)
        print(json.dumps(setting.model_dump(mode="json"), ensure_ascii=False, indent=2))

    if args.out:
        result = {
            "core_memory": core_memory.model_dump(mode="json"),
            "foundation": foundation.model_dump(mode="json"),
            "specs": specs.model_dump(mode="json"),
            "created": [setting.model_dump(mode="json") for setting in created],
        }
        Path(args.out).write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
