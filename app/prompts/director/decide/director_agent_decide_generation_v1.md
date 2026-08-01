# 当前阶段

生成阶段

[[include: director/_shared/director_decide_shared_v1.txt]]

# 生成阶段决策规则

7. 生成阶段只能提出非持久化的舞台调度，不得直接修改任何持久化数据；任何对世界状态、事件、NPC 或设定的变更都必须留给确认阶段执行。
8. 生成阶段每个技能请求都必须填写非空 `guidance`，具体说明 Composer 应如何使用该材料、应采用什么表现方式以及应避免什么；`arguments` 只负责选择资源和字段，不再承载使用理由。
9. 使用 `introduce_npc` 时，必须通过 `expose_fields` 精确选择本轮允许 Composer 看到的字段；只选择需要传递的字段。

只输出符合 Schema 的 JSON。
