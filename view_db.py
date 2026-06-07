"""
记忆库可视化：读取 data/memories.db，导出为 Markdown 文件.

用法:
    python view_db.py                        # 默认输出到 docs/memory_db_export.md
    python view_db.py data/memories.db       # 指定数据库路径
    python view_db.py -o my_export.md        # 指定输出文件
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone

TYPE_LABEL = {
    "agreement": "约定/暗号", "fact": "稳定事实", "trait": "性格/习惯",
    "relationship": "关系变化", "item": "道具/物品", "event": "重要事件",
}

TYPE_ORDER = ["agreement", "fact", "trait", "relationship", "item", "event"]


def main():
    parser = argparse.ArgumentParser(description="记忆库可视化导出")
    parser.add_argument("db_path", nargs="?", default="data/memories.db", help="数据库路径 (默认: data/memories.db)")
    parser.add_argument("-o", "--output", default="docs/memory_db_export.md", help="输出文件路径 (默认: docs/memory_db_export.md)")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM memories ORDER BY type, created_at DESC").fetchall()
    conn.close()

    if not rows:
        print("数据库为空。")
        return

    total = len(rows)
    type_counts = {}
    for r in rows:
        t = r["type"]
        type_counts[t] = type_counts.get(t, 0) + 1

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    lines.append(f"# 记忆库导出")
    lines.append(f"")
    lines.append(f"**数据库**: `{args.db_path}` | **导出时间**: {now} | **总计**: {total} 条")
    lines.append(f"")

    # ── 概览 ──
    lines.append(f"## 概览")
    lines.append(f"")
    lines.append(f"| 类型 | 数量 |")
    lines.append(f"|------|------|")
    for t in TYPE_ORDER:
        if t in type_counts:
            lines.append(f"| {TYPE_LABEL.get(t, t)} ({t}) | {type_counts[t]} |")
    lines.append(f"")

    # ── 详细 ──
    lines.append(f"## 详细记忆")
    lines.append(f"")

    records = []
    for i, r in enumerate(rows, 1):
        records.append({
            "index": i,
            "id": r["id"],
            "type": r["type"],
            "subject": r["subject"],
            "content": r["content"],
            "entities": json.loads(r["entities_json"]),
            "location": r["location"],
            "time_label": r["time_label"],
            "visibility": json.loads(r["visibility_json"]),
            "tags": json.loads(r["tags_json"]),
            "certainty": r["certainty"],
            "salience": r["salience"],
            "source_scene_id": r["source_scene_id"],
            "source_text_span": json.loads(r["source_text_span_json"]),
            "created_at": r["created_at"],
        })

    current_type = None
    for rec in records:
        if rec["type"] != current_type:
            current_type = rec["type"]
            label = TYPE_LABEL.get(current_type, current_type)
            lines.append(f"### {label} (`{current_type}`)")
            lines.append(f"")

        idx = rec["index"]
        lines.append(f"#### #{idx} {rec['subject']}")
        lines.append(f"")
        lines.append(f"| 字段 | 值 |")
        lines.append(f"|------|-----|")
        lines.append(f"| ID | `{rec['id'][:8]}...` |")
        lines.append(f"| 类型 | {TYPE_LABEL.get(rec['type'], rec['type'])} |")
        lines.append(f"| 主体 | {rec['subject']} |")
        lines.append(f"| 内容 | {rec['content']} |")
        if rec["entities"]:
            lines.append(f"| 实体 | {', '.join(rec['entities'])} |")
        if rec["location"]:
            lines.append(f"| 地点 | {rec['location']} |")
        if rec["time_label"]:
            lines.append(f"| 时间 | {rec['time_label']} |")
        if rec["visibility"]:
            lines.append(f"| 可见角色 | {', '.join(rec['visibility'])} |")
        if rec["tags"]:
            lines.append(f"| 标签 | {', '.join(rec['tags'])} |")
        lines.append(f"| 确定性 | {rec['certainty']} |")
        lines.append(f"| 重要性 | {rec['salience']} |")
        lines.append(f"| 来源场景 | `{rec['source_scene_id']}` |")
        if rec["source_text_span"]:
            spans = " / ".join(f'"{s}"' for s in rec["source_text_span"])
            lines.append(f"| 原文片段 | {spans} |")
        lines.append(f"| 创建时间 | {rec['created_at']} |")
        lines.append(f"")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"导出完成: {args.output} ({total} 条记忆)")


if __name__ == "__main__":
    main()
