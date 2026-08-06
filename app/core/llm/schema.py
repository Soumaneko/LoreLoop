"""JSON Schema 提示渲染工具。

LLM 提示普遍以 ``model_json_schema()`` 的产物作为 ``output_schema_json``
注入。这里集中处理 schema 的展示策略，避免每个调用点各自决定序列化方式：

- ``compact_schema`` 只剔除 pydantic 自动生成的 ``title`` 元数据（与属性名冗余），
  且做到属性名安全：名为 ``title`` 的业务字段不会被误删。数值/数量/长度约束
  （``min*``/``max*``）、``default`` 与 ``additionalProperties`` 全部保留——
  因为网关未使用原生 Structured Output，注入的 schema 是模型唯一可见的约束来源。
- ``schema_json`` 以紧凑 JSON 渲染。schema 只是模型读取的指令而非其输出，
  缩进纯属 token 浪费；校验仍由 pydantic 在解析输出时强制执行。
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """递归剔除 pydantic 自动生成的 ``title`` 元数据，属性名安全。

    pydantic 会为每个字段/模型生成 ``{"title": "Name", ...}`` 形式的标签键；
    该键位于 schema 节点（字段描述、模型描述、``$defs`` 条目）上，与属性名冗余，
    模型并不依赖。业务字段名可能就叫 ``title``（如事件标题），它位于
    ``properties`` 映射内，属于属性名而非元数据——因此对 ``properties`` 映射的
    键一律保留，只在其它位置剔除 ``title``。幂等。
    """
    return _compact(schema, in_properties=False)


def _compact(value: Any, *, in_properties: bool) -> Any:
    if isinstance(value, dict):
        if in_properties:
            return {
                key: _compact(item, in_properties=False)
                for key, item in value.items()
            }
        return {
            key: _compact(item, in_properties=(key == "properties"))
            for key, item in value.items()
            if key != "title"
        }
    if isinstance(value, list):
        return [_compact(item, in_properties=False) for item in value]
    return value


def schema_json(model: type[BaseModel]) -> str:
    """渲染某个 pydantic 模型的紧凑 schema 文本，供提示注入。"""
    return json.dumps(
        compact_schema(model.model_json_schema()),
        ensure_ascii=False,
        separators=(",", ":"),
    )
