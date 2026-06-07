
========================================================================
  Action Playground: Parse → SelfCheck
========================================================================

  [INPUT] user_input:
    我站在街角踌躇，突然想到背包里还有一颗藏尸者制造的防腐过的美女头颅。这玩意可不能随意带到公爵府或暴风要塞，还是先
    想办法处理掉吧。我决定先去猪和哨声旅店寻找肖尔，汇报一下我维护了夜色镇的治安，帮助了守备官，这也许能让他对我看法
    改观。并且询问他是否能找到能辨认头颅的人。如果没有，我将去已宰的羔羊找黑暗缚灵者们处理掉头颅，换取一些东西。

  [INPUT] scene_context:
    {
        "scene_id": "scene_010",
        "location": "狮王之傲旅店",
        "time_label": "第1日",
        "participants": [
            "主角",
            "米莉·奥斯沃斯",
            "醉醺醺的战士"
        ],
        "viewpoint": "主角"
    }

========================================================================
  MODULE 1: ActionParser → LLM 原始输出
========================================================================

  [MODULE 1] ActionParser.parse() — 调用真实 LLM...

  [INTENT] 先去猪和哨声旅店向肖尔汇报并请求帮助辨认头颅，若不行则去已宰的羔羊找黑暗缚灵者处理头颅换取物品。

  [MODULE 1] LLM returned 4 atomic action(s):

    [A1] seq=1  [move] 去
      actor       : 主角
      raw_text    : "我决定先去猪和哨声旅店寻找肖尔"
      target      : 猪和哨声旅店
      goal        : 寻找肖尔
      needs_roll  : False
      confidence  : 1.0

    [A2] seq=2  [speak] 汇报并询问
      actor       : 主角
      raw_text    : "汇报一下我维护了夜色镇的治安，帮助了守备官，这也许能让他对我看法改观。并且询问他是否能找到能辨认头颅的人"
      target      : 肖尔
      method      : 首先汇报维护夜色镇治安和帮助守备官的事迹，然后询问是否能帮忙找到辨认头颅的人
      goal        : 改善肖尔对自己的看法，同时获取辨认头颅的线索
      needs_roll  : True  hint=说服/印象改善
      confidence  : 0.95

    [A3] seq=3  [move] 去
      actor       : 主角
      raw_text    : "如果没有，我将去已宰的羔羊"
      target      : 已宰的羔羊
      goal        : 寻找黑暗缚灵者
      condition   : 如果肖尔无法找到能辨认头颅的人
      needs_roll  : False
      confidence  : 0.95

    [A4] seq=4  [interact_object] 处理并换取
      actor       : 主角
      raw_text    : "找黑暗缚灵者们处理掉头颅，换取一些东西"
      target      : 黑暗缚灵者们
      method      : 展示头颅并协商交易，用头颅换取物品
      goal        : 处理掉头颅并获得回报
      condition   : 在A3移动完成后且肖尔无法提供帮助
      needs_roll  : True  hint=交易谈判
      confidence  : 0.9

  [MODULE 1] 10 anchor(s):

    [location] 猪和哨声旅店  →  猪和哨声旅店
      source      : explicit
      action_ids  : ['A1', 'A2']
      confidence  : 1.0

    [character] 肖尔  →  肖尔
      source      : explicit
      action_ids  : ['A1', 'A2']
      confidence  : 1.0

    [location] 已宰的羔羊  →  已宰的羔羊
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

    [character] 黑暗缚灵者们  →  黑暗缚灵者们
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

    [item] 美女头颅  →  藏尸者制造的防腐过的美女头颅
      source      : explicit
      action_ids  : ['A2', 'A4']
      confidence  : 1.0

    [organization] 藏尸者  →  藏尸者
      source      : explicit
      action_ids  : []
      confidence  : 1.0

    [concept] 夜色镇治安  →  维护夜色镇治安
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [character] 守备官  →  帮助守备官
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [location] 街角  →  街角
      source      : explicit
      action_ids  : []
      confidence  : 1.0

    [condition] 如果没有  →  如果肖尔无法找到辨认头颅的人
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

========================================================================
  MODULE 2: ActionSelfCheck → 程序自检
========================================================================

  [MODULE 2] ActionSelfCheck.check() — 运行中...

  [INTENT] 先去猪和哨声旅店向肖尔汇报并请求帮助辨认头颅，若不行则去已宰的羔羊找黑暗缚灵者处理头颅换取物品。

  [MODULE 1] LLM returned 4 atomic action(s):

    [A1] seq=1  [move] 去
      actor       : 主角
      raw_text    : "我决定先去猪和哨声旅店寻找肖尔"
      target      : 猪和哨声旅店
      goal        : 寻找肖尔
      needs_roll  : False
      confidence  : 1.0

    [A2] seq=2  [speak] 汇报并询问
      actor       : 主角
      raw_text    : "汇报一下我维护了夜色镇的治安，帮助了守备官，这也许能让他对我看法改观。并且询问他是否能找到能辨认头颅的人"
      target      : 肖尔
      method      : 首先汇报维护夜色镇治安和帮助守备官的事迹，然后询问是否能帮忙找到辨认头颅的人
      goal        : 改善肖尔对自己的看法，同时获取辨认头颅的线索
      needs_roll  : True  hint=说服/印象改善
      confidence  : 0.95

    [A3] seq=3  [move] 去
      actor       : 主角
      raw_text    : "如果没有，我将去已宰的羔羊"
      target      : 已宰的羔羊
      goal        : 寻找黑暗缚灵者
      condition   : 如果肖尔无法找到能辨认头颅的人
      needs_roll  : False
      confidence  : 0.95

    [A4] seq=4  [interact_object] 处理并换取
      actor       : 主角
      raw_text    : "找黑暗缚灵者们处理掉头颅，换取一些东西"
      target      : 黑暗缚灵者们
      method      : 展示头颅并协商交易，用头颅换取物品
      goal        : 处理掉头颅并获得回报
      condition   : 在A3移动完成后且肖尔无法提供帮助
      needs_roll  : True  hint=交易谈判
      confidence  : 0.9

  [MODULE 1] 10 anchor(s):

    [location] 猪和哨声旅店  →  猪和哨声旅店
      source      : explicit
      action_ids  : ['A1', 'A2']
      confidence  : 1.0

    [character] 肖尔  →  肖尔
      source      : explicit
      action_ids  : ['A1', 'A2']
      confidence  : 1.0

    [location] 已宰的羔羊  →  已宰的羔羊
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

    [character] 黑暗缚灵者们  →  黑暗缚灵者们
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

    [item] 美女头颅  →  藏尸者制造的防腐过的美女头颅
      source      : explicit
      action_ids  : ['A2', 'A4']
      confidence  : 1.0

    [organization] 藏尸者  →  藏尸者
      source      : explicit
      action_ids  : []
      confidence  : 1.0

    [concept] 夜色镇治安  →  维护夜色镇治安
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [character] 守备官  →  帮助守备官
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [location] 街角  →  街角
      source      : explicit
      action_ids  : []
      confidence  : 1.0

    [condition] 如果没有  →  如果肖尔无法找到辨认头颅的人
      source      : explicit
      action_ids  : ['A3', 'A4']
      confidence  : 1.0

========================================================================
  FINAL RESULT
========================================================================

  [RESULT] 最终 4 actions, 10 anchors

  [INTENT] 先去猪和哨声旅店向肖尔汇报并请求帮助辨认头颅，若不行则去已宰的羔羊找黑暗缚灵者处理头颅换取物品。

  ── Actions ──
    [A1] seq=1  [move] 主角 去 → 猪和哨声旅店  roll=False
    [A2] seq=2  [speak] 主角 汇报并询问 → 肖尔  roll=True
    [A3] seq=3  [move] 主角 去 → 已宰的羔羊  roll=False
    [A4] seq=4  [interact_object] 主角 处理并换取 → 黑暗缚灵者们  roll=True

  ── Anchors ──
    [地点] 猪和哨声旅店  →  猪和哨声旅店  (actions: ['A1', 'A2'])
    [角色] 肖尔  →  肖尔  (actions: ['A1', 'A2'])
    [地点] 已宰的羔羊  →  已宰的羔羊  (actions: ['A3', 'A4'])
    [角色] 黑暗缚灵者们  →  黑暗缚灵者们  (actions: ['A3', 'A4'])
    [物品] 美女头颅  →  藏尸者制造的防腐过的美女头颅  (actions: ['A2', 'A4'])
    [组织] 藏尸者  →  藏尸者  (actions: [])
    [概念] 夜色镇治安  →  维护夜色镇治安  (actions: ['A2'])
    [角色] 守备官  →  帮助守备官  (actions: ['A2'])
    [地点] 街角  →  街角  (actions: [])
    [条件词] 如果没有  →  如果肖尔无法找到辨认头颅的人  (actions: ['A3', 'A4'])
