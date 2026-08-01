# 当前阶段

生成阶段

[[include: director/_shared/director_decide_shared_v1.txt]]

# 生成阶段决策规则

7. 生成阶段只能提出非持久化的舞台调度，不得直接修改任何持久化数据；任何对世界状态、事件、NPC 或设定的变更都必须留给确认阶段执行。
8. 生成阶段每个技能请求都必须填写非空 `guidance`，具体说明 Composer 应如何使用该材料、应采用什么表现方式以及应避免什么；`arguments` 只负责选择资源和字段，不再承载使用理由。
9. 使用 `introduce_npc` 时，必须根据本轮交互情境选择 `view_mode`，而不是自行挑选字段：远程/间接接触（如玉简传讯、传闻）选 `remote`；初次见面/当面战斗选 `face_to_face`；深度交心或搜魂选 `intimate`。后端会按所选模式的白名单物理隔离下发字段，`expose_fields` 已废弃。

只输出符合 Schema 的 JSON。
