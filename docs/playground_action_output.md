
========================================================================
  Action + Retrieval + Roll Playground: Parse → SelfCheck → CandidateRetriever → RollResolver
========================================================================

  [INPUT] user_input:
    品酒最忌讳心急，人也一样，潇洒的和米莉打个招呼，让她别忘了我把狗头人变成的那只羊，做出下次再来的约定吧。接着，和
    莎拉简单的聊一聊，这对明天的安排也有帮助。

  [INPUT] scene_context:
    {
        "scene_id": "scene_010",
        "location": "北郡修道院",
        "time_label": "第1日深夜",
        "participants": [
            "主角",
            "米莉·奥斯沃斯",
            "莎拉·月刃"
        ],
        "viewpoint": "主角"
    }

========================================================================
  MODULE 1: ActionParser → LLM 原始输出
========================================================================

  [MODULE 1] ActionParser.parse() — 调用真实 LLM...

  [INTENT] 与米莉打招呼并提醒她关于羊和下次约定，然后与莎拉简单聊天以助于明天安排

  [MODULE 1] LLM returned 2 atomic action(s):

    [A1] seq=1  [speak] 打招呼
      actor       : 主角
      raw_text    : "潇洒的和米莉打个招呼，让她别忘了我把狗头人变成的那只羊，做出下次再来的约定吧"
      target      : 米莉
      method      : 提醒她别忘了那只羊，并做出下次再来的约定
      goal        : 延续互动/巩固关系
      needs_roll  : False
      confidence  : 0.95

    [A2] seq=2  [speak] 聊一聊
      actor       : 主角
      raw_text    : "和莎拉简单的聊一聊"
      target      : 莎拉
      goal        : 对明天的安排有帮助
      needs_roll  : False
      confidence  : 0.95

  [MODULE 1] 9 anchor(s):

    [character] 米莉  →  米莉
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [character] 莎拉  →  莎拉
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [item] 那只羊  →  羊
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [concept] 狗头人  →  狗头人
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [concept] 下次再来  →  下次再来
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [past_reference] 那只  →  那只
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [temporal_reference] 明天  →  明天
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [action_keyword] 打招呼  →  打招呼
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [action_keyword] 聊一聊  →  聊一聊
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

========================================================================
  MODULE 2: ActionSelfCheck → 程序自检
========================================================================

  [MODULE 2] ActionSelfCheck.check() — 运行中...

  [INTENT] 与米莉打招呼并提醒她关于羊和下次约定，然后与莎拉简单聊天以助于明天安排

  [MODULE 1] LLM returned 2 atomic action(s):

    [A1] seq=1  [speak] 打招呼
      actor       : 主角
      raw_text    : "潇洒的和米莉打个招呼，让她别忘了我把狗头人变成的那只羊，做出下次再来的约定吧"
      target      : 米莉
      method      : 提醒她别忘了那只羊，并做出下次再来的约定
      goal        : 延续互动/巩固关系
      needs_roll  : False
      confidence  : 0.95

    [A2] seq=2  [speak] 聊一聊
      actor       : 主角
      raw_text    : "和莎拉简单的聊一聊"
      target      : 莎拉
      goal        : 对明天的安排有帮助
      needs_roll  : False
      confidence  : 0.95

  [MODULE 1] 10 anchor(s):

    [character] 米莉  →  米莉
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [character] 莎拉  →  莎拉
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [item] 那只羊  →  羊
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [concept] 狗头人  →  狗头人
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [concept] 下次再来  →  下次再来
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [past_reference] 那只  →  那只
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [temporal_reference] 明天  →  明天
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [action_keyword] 打招呼  →  打招呼
      source      : explicit
      action_ids  : ['A1']
      confidence  : 1.0

    [action_keyword] 聊一聊  →  聊一聊
      source      : explicit
      action_ids  : ['A2']
      confidence  : 1.0

    [character] 主角  →  主角
      source      : implicit_from_scene
      action_ids  : ['A1', 'A2']
      confidence  : 1.0

========================================================================
  MODULE 3: CandidateRetriever → 记忆候选召回
========================================================================

  [MODULE 3] 从 data/memories.db 加载记忆库...
  [MODULE 3] 记忆库共有 24 条记忆

  [MODULE 3] 扫描 24 条记忆，命中 20 条候选:

  #1  rrf=0.0715  [relationship] 关系变化
      subject   : 莎拉·月刃对主角的认可
      content   : 莎拉·月刃对主角的战斗风格和头脑表示赞赏，认为主角与一般人类法师不同，脑子更好用。
      entities  : ['莎拉·月刃', '主角']
      tags      : ['信任', '认可']
      location  : 回音山路口
      time_label: 第1日夜晚
      salience=0.7  certainty=0.9
      actions hit: 2  max_rrf=0.0615  bonus=+0.0100
      matched_anchors : ['主角', '莎拉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.87  (rank 3/24)
           R2 subj+tag :    4.58  (rank 1/24)
            R3 content :    1.83  (rank 1/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    3.85  (rank 4/24)

  #2  rrf=0.0710  [relationship] 关系变化
      subject   : 莎拉·月刃加入队伍
      content   : 莎拉·月刃同意与主角同行，但明确表示是为了保护本地少女米莉，而非信任主角。她对主角仍持怀疑态度。
      entities  : ['主角', '莎拉·月刃', '米莉·奥斯沃斯']
      tags      : ['人物关系', '临时合作']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.8  certainty=1.0
      actions hit: 2  max_rrf=0.0610  bonus=+0.0100
      matched_anchors : ['主角', '米莉', '莎拉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.87  (rank 3/24)
           R2 subj+tag :    3.39  (rank 4/24)
            R3 content :    1.83  (rank 1/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    4.10  (rank 1/24)

  #3  rrf=0.0707  [relationship] 关系变化
      subject   : 暗夜精灵女性对主角的初步信任
      content   : 暗夜精灵女性因为主角身上的宁神花香气，在潜意识里将主角归类为相对无害、可交流的生物，因此没有立刻拔刀。
      entities  : ['暗夜精灵女性', '主角']
      tags      : ['态度', '信任', '线索']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.8  certainty=0.8
      actions hit: 2  max_rrf=0.0607  bonus=+0.0100
      matched_anchors : ['主角', '莎拉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    4.68  (rank 1/24)
           R2 subj+tag :    3.90  (rank 2/24)
            R3 content :    1.56  (rank 3/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    3.00  (rank 7/24)

  #4  rrf=0.0704  [event] 重要事件
      subject   : 回音山路口遭遇狗头人
      content   : 在前往北郡修道院的回音山路口，队伍遭遇三个狗头人，其中有一个狗头人地卜师（会施放火球术）正在围攻一辆侧翻的马车。狗头人地卜师似乎注意到了队伍。
      entities  : ['主角', '莎拉·月刃', '米莉·奥斯沃斯', '狗头人', '狗头人地卜师']
      tags      : ['战斗', '事件']
      location  : 回音山路口
      time_label: 第1日夜晚
      salience=0.9  certainty=0.8
      actions hit: 2  max_rrf=0.0604  bonus=+0.0100
      matched_anchors : ['主角', '狗头人', '米莉', '莎拉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    9.60  (rank 1/24)
           R2 subj+tag :    6.00  (rank 1/24)
            R3 content :    2.40  (rank 2/24)
               R4 type :    1.00  (rank 11/24)
                R5 ctx :    3.20  (rank 5/24)

  #5  rrf=0.0702  [relationship] 关系变化
      subject   : 主角与暗夜精灵女性初次接触
      content   : 主角（卢西安）主动搭讪暗夜精灵女性，试图以请酒方式建立关系，对方以‘不喝劣质酒精’和‘不陪富家少爷玩过家家’回应，但未拔刀赶人，表明存在进一步交涉的可能性。
      entities  : ['主角', '暗夜精灵女性']
      tags      : ['初次接触', '潜在合作']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=0.9
      actions hit: 2  max_rrf=0.0602  bonus=+0.0100
      matched_anchors : ['主角', '莎拉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    4.68  (rank 1/24)
           R2 subj+tag :    3.90  (rank 2/24)
            R3 content :    1.56  (rank 3/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    2.85  (rank 12/24)

  #6  rrf=0.0701  [fact] 稳定事实
      subject   : 北郡小径的狗头人威胁
      content   : 主角声称通往北郡的小径近期不太平，狗头人甚至敢袭击落单的卫兵，夜间行路有危险。
      entities  : ['北郡小径', '狗头人']
      tags      : ['安全威胁', '狗头人', '道路信息']
      location  : 北郡小径
      time_label: 第1日夜晚
      salience=0.8  certainty=0.7
      actions hit: 2  max_rrf=0.0601  bonus=+0.0100
      matched_anchors : ['主角', '狗头人']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    7.20  (rank 2/24)
           R2 subj+tag :    6.00  (rank 1/24)
            R3 content :    2.88  (rank 1/24)
               R4 type :    1.00  (rank 11/24)
                R5 ctx :    2.95  (rank 8/24)

  #7  rrf=0.0695  [agreement] 约定/暗号
      subject   : 米莉·奥斯沃斯的邀请
      content   : 米莉·奥斯沃斯承诺北郡修道院的大门永远为主角敞开，欢迎主角以后来品尝新酿的葡萄酒。
      entities  : ['米莉·奥斯沃斯', '主角']
      tags      : ['邀请', '承诺']
      location  : 北郡修道院门口
      time_label: 第1日夜晚
      salience=0.8  certainty=1.0
      actions hit: 2  max_rrf=0.0595  bonus=+0.0100
      matched_anchors : ['主角', '米莉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    2.40  (rank 3/24)
           R2 subj+tag :    1.35  (rank 5/24)
            R3 content :    1.02  (rank 3/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    4.10  (rank 1/24)

  #8  rrf=0.0694  [relationship] 关系变化
      subject   : 米莉对主角的感激
      content   : 米莉对主角出手相助深表感激，并主动走到主角桌前致谢，但表示自己没有钱作为回报。
      entities  : ['米莉·奥斯沃斯', '主角']
      tags      : ['感激', '恩情']
      location  : 狮王之傲旅店
      time_label: 第1日
      salience=0.6  certainty=1.0
      actions hit: 2  max_rrf=0.0594  bonus=+0.0100
      matched_anchors : ['主角', '米莉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    2.40  (rank 3/24)
           R2 subj+tag :    2.54  (rank 3/24)
            R3 content :    1.02  (rank 3/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    2.70  (rank 14/24)

  #9  rrf=0.0690  [relationship] 关系变化
      subject   : 主角认出米莉·奥斯沃斯
      content   : 主角认得米莉·奥斯沃斯，因为任何在北郡修道院附近待过的人都应该见过她。
      entities  : ['主角', '米莉·奥斯沃斯']
      tags      : ['角色关系', '认识']
      location  : 狮王之傲旅店
      time_label: 第1日
      salience=0.4  certainty=1.0
      actions hit: 2  max_rrf=0.0590  bonus=+0.0100
      matched_anchors : ['主角', '米莉']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    2.40  (rank 3/24)
           R2 subj+tag :    2.54  (rank 3/24)
            R3 content :    1.02  (rank 3/24)
               R4 type :    3.00  (rank 1/24)
                R5 ctx :    1.30  (rank 19/24)

  #10  rrf=0.0651  [trait] 性格/习惯
      subject   : 主角的战斗风格
      content   : 主角偏好控制法术，善于在战斗中优先控制敌人，而非单纯输出伤害，战斗时冷静有条理。
      entities  : ['主角']
      tags      : ['战斗风格', '控制优先']
      location  : 回音山路口
      time_label: 第1日夜晚
      salience=0.7  certainty=1.0
      actions hit: 2  max_rrf=0.0551  bonus=+0.0100
      matched_anchors : ['主角']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    1.43  (rank 8/24)
           R2 subj+tag :    1.19  (rank 11/24)
            R3 content :    0.48  (rank 14/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    3.90  (rank 3/24)

  #11  rrf=0.0646  [trait] 性格/习惯
      subject   : 主角的魔法控制能力
      content   : 主角能施展精细的奥术魔法，无需念咒即可隔空取物，使物品悬浮并平稳移动，且魔法呈淡紫色光辉。
      entities  : ['主角']
      tags      : ['魔法', '隔空取物', '奥术']
      location  : 狮王之傲旅店
      time_label: 第1日
      salience=0.8  certainty=1.0
      actions hit: 2  max_rrf=0.0546  bonus=+0.0100
      matched_anchors : ['主角']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    1.43  (rank 8/24)
           R2 subj+tag :    1.19  (rank 11/24)
            R3 content :    0.48  (rank 14/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    3.10  (rank 6/24)

  #12  rrf=0.0643  [trait] 性格/习惯
      subject   : 主角敏捷值低
      content   : 主角的敏捷值为11，属于偏低水平，走快了容易绊倒。
      entities  : ['主角']
      tags      : ['角色属性', '弱点']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=1.0
      actions hit: 2  max_rrf=0.0543  bonus=+0.0100
      matched_anchors : ['主角']
      matched_actions : ['A1', 'A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    1.43  (rank 8/24)
           R2 subj+tag :    1.19  (rank 11/24)
            R3 content :    0.48  (rank 14/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    2.90  (rank 9/24)

  #13  rrf=0.0576  [trait] 性格/习惯
      subject   : 莎拉·月刃是优秀斥候
      content   : 莎拉·月刃作为暗夜精灵，拥有敏锐的听觉和侦察能力，能提前发现野狼等危险。
      entities  : ['莎拉·月刃']
      tags      : ['角色能力']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.6  certainty=1.0
      matched_anchors : ['莎拉']
      matched_actions : ['A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    2.44  (rank 10/24)
           R2 subj+tag :    3.39  (rank 4/24)
            R3 content :    1.36  (rank 5/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    3.70  (rank 5/24)

  #14  rrf=0.0567  [trait] 性格/习惯
      subject   : 暗夜精灵女性性格
      content   : 暗夜精灵女性性格冷漠、警惕、不信任人类贵族，拒绝廉价酒精，语言具有攻击性，但并未直接驱赶，留有交涉余地。
      entities  : ['暗夜精灵女性']
      tags      : ['性格', '态度']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=0.9
      matched_anchors : ['莎拉']
      matched_actions : ['A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.25  (rank 6/24)
           R2 subj+tag :    2.71  (rank 6/24)
            R3 content :    1.08  (rank 6/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    2.85  (rank 13/24)

  #15  rrf=0.0566  [trait] 性格/习惯
      subject   : 暗夜精灵女性的防御姿态
      content   : 暗夜精灵女性紧握匕首不是为了攻击，而是因为缺乏安全感，对人类社会极度不适和警惕，像一只被迫闯入闹市区的黑豹，随时准备炸毛。
      entities  : ['暗夜精灵女性']
      tags      : ['性格', '弱点', '行为模式']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=0.8
      matched_anchors : ['莎拉']
      matched_actions : ['A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.25  (rank 6/24)
           R2 subj+tag :    2.71  (rank 6/24)
            R3 content :    1.08  (rank 6/24)
               R4 type :    2.00  (rank 7/24)
                R5 ctx :    2.80  (rank 15/24)

  #16  rrf=0.0558  [fact] 稳定事实
      subject   : 暗夜精灵女性的目的地与困境
      content   : 暗夜精灵女性想去暴风城，但迷路了，对人类繁琐的入城登记、卫兵盘问以及复杂的人际规则感到头大，不知如何与人类卫兵打交道。
      entities  : ['暗夜精灵女性', '暴风城', '人类卫兵']
      tags      : ['背景', '目标', '阻碍']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=0.8
      matched_anchors : ['莎拉']
      matched_actions : ['A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.25  (rank 6/24)
           R2 subj+tag :    2.71  (rank 6/24)
            R3 content :    1.08  (rank 6/24)
               R4 type :    1.00  (rank 14/24)
                R5 ctx :    2.80  (rank 15/24)

  #17  rrf=0.0558  [fact] 稳定事实
      subject   : 暗夜精灵女性的旅行经验
      content   : 暗夜精灵女性身手不错，但这是她第一次离开卡利姆多，是新手。
      entities  : ['暗夜精灵女性', '卡利姆多']
      tags      : ['背景', '新手']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=0.8
      matched_anchors : ['莎拉']
      matched_actions : ['A2']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    3.25  (rank 6/24)
           R2 subj+tag :    2.71  (rank 6/24)
            R3 content :    1.08  (rank 6/24)
               R4 type :    1.00  (rank 14/24)
                R5 ctx :    2.80  (rank 15/24)

  #18  rrf=0.0553  [fact] 稳定事实
      subject   : 米莉·奥斯沃斯的回程困境
      content   : 米莉·奥斯沃斯因错过天黑且未带够住店钱，打算在马厩凑合一晚，同时担忧回程安全以及明天一早葡萄园的农活。
      entities  : ['米莉·奥斯沃斯', '狮王之傲旅店']
      tags      : ['困境', '回程', '住宿']
      location  : 狮王之傲旅店
      time_label: 第1日夜晚
      salience=0.7  certainty=1.0
      matched_anchors : ['米莉']
      matched_actions : ['A1']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    0.97  (rank 14/24)
           R2 subj+tag :    1.35  (rank 5/24)
            R3 content :    0.54  (rank 8/24)
               R4 type :    1.00  (rank 11/24)
                R5 ctx :    2.90  (rank 9/24)

  #19  rrf=0.0550  [fact] 稳定事实
      subject   : 米莉的背景与困境
      content   : 米莉·奥斯沃斯自称来自北郡葡萄园，她正在给狮王之傲旅店的老板法雷送葡萄酒样品，并称如果样品损坏将无法向修道院支付租金。
      entities  : ['米莉·奥斯沃斯', '北郡葡萄园', '法雷', '狮王之傲旅店', '修道院']
      tags      : ['背景', '财务压力', '葡萄园', '修道院']
      location  : 狮王之傲旅店
      time_label: 第1日
      salience=0.7  certainty=0.9
      matched_anchors : ['米莉']
      matched_actions : ['A1']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    0.97  (rank 14/24)
           R2 subj+tag :    1.35  (rank 5/24)
            R3 content :    0.54  (rank 8/24)
               R4 type :    1.00  (rank 11/24)
                R5 ctx :    2.85  (rank 12/24)

  #20  rrf=0.0547  [fact] 稳定事实
      subject   : 米莉·奥斯沃斯来自北郡葡萄园
      content   : 米莉·奥斯沃斯与北郡葡萄园有关联，她前来狮王之傲旅店送货，并提到第二天一早还要回葡萄园干活。
      entities  : ['米莉·奥斯沃斯', '北郡葡萄园']
      tags      : ['人物背景', '职业', '葡萄园']
      location  : 北郡
      time_label: 第1日夜晚
      salience=0.6  certainty=0.9
      matched_anchors : ['米莉']
      matched_actions : ['A1']
      scores & ranks (top-scored items / 24 total):
             R1 entity :    0.97  (rank 14/24)
           R2 subj+tag :    1.35  (rank 5/24)
            R3 content :    0.54  (rank 8/24)
               R4 type :    1.00  (rank 11/24)
                R5 ctx :    2.65  (rank 15/24)

  ── Anchor IDF weights ──
    [character] "米莉"  norm="米莉"  weight=0.538
    [character] "莎拉"  norm="莎拉"  weight=1.356
    [item] "那只羊"  norm="羊"  weight=2.400
    [concept] "狗头人"  norm="狗头人"  weight=2.400
    [concept] "下次再来"  norm="下次再来"  weight=2.400
    [past_reference] "那只"  norm="那只"  weight=2.400
    [temporal_reference] "明天"  norm="明天"  weight=1.000
    [action_keyword] "打招呼"  norm="打招呼"  weight=1.600
    [action_keyword] "聊一聊"  norm="聊一聊"  weight=1.600
    [character] "主角"  norm="主角"  weight=0.477

========================================================================
  MODULE 4: RollResolver → NdM 骰点
========================================================================

  [MODULE 4] Roll Pipeline — NdM Roll Resolver
  [MODULE 4] 2 total actions, 0 need roll, min_rolls=2
  [MODULE 4] dice_formula: 2d20  (dice_sides=20)
  [MODULE 4] 2 action(s) forced to roll (补充至 min_rolls)
    [A1] seq=1  主角  [speak] 对话 [forced]
      roll: 1d20 = 9
    [A2] seq=2  主角  [speak] 对话 [forced]
      roll: 1d20 = 8

========================================================================
  FINAL RESULT
========================================================================

  [RESULT] 最终 2 actions, 10 anchors

  [INTENT] 与米莉打招呼并提醒她关于羊和下次约定，然后与莎拉简单聊天以助于明天安排

  ── Actions ──
    [A1] seq=1  [speak] 主角 打招呼 → 米莉  roll=False
    [A2] seq=2  [speak] 主角 聊一聊 → 莎拉  roll=False

  ── Anchors ──
    [角色] 米莉  →  米莉  (actions: ['A1'])
    [角色] 莎拉  →  莎拉  (actions: ['A2'])
    [物品] 那只羊  →  羊  (actions: ['A1'])
    [概念] 狗头人  →  狗头人  (actions: ['A1'])
    [概念] 下次再来  →  下次再来  (actions: ['A1'])
    [旧事引用] 那只  →  那只  (actions: ['A1'])
    [时间词] 明天  →  明天  (actions: ['A2'])
    [动作关键词] 打招呼  →  打招呼  (actions: ['A1'])
    [动作关键词] 聊一聊  →  聊一聊  (actions: ['A2'])
    [角色] 主角  →  主角  (actions: ['A1', 'A2'])
