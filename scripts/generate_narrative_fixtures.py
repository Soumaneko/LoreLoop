"""Script to generate the 10 production-accurate fixture test cases for narrative quality benchmark."""

from __future__ import annotations

import json
from pathlib import Path

from app.domain.context_facts.schemas import ContextFact, ContextFactSet
from app.domain.core_memory.schemas import CoreMemory, CoreMemoryItem
from app.domain.current_state.schemas import (
    CurrentState,
    InventoryItem,
    ParticipantState,
    PlayerState,
    SceneState,
    WorldClock,
)
from app.domain.director.schemas import (
    DirectorStageMaterial,
    DirectorStageResult,
)
from app.domain.director_event.schemas import DirectorEvent
from app.domain.npc_profile.schemas import (
    KnownAbility,
    NPCProfileDirectorView,
)
from app.domain.short_term_memory.schemas import ShortTermScene
from app.domain.turn_resolution.schemas import (
    ActionResolutionFrame,
    TurnResolutionResult,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURES_PATH = ROOT / "benchmarks" / "narrative_quality" / "fixtures.json"


def get_base_core_memory() -> CoreMemory:
    return CoreMemory(
        items=[
            CoreMemoryItem(
                id="core_world_identity",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="当前世界名为「中州大陆」，世界风格为：古典仙侠奇幻\n能力体系为：仙道修真与魔法",
            ),
            CoreMemoryItem(
                id="core_player_identity",
                kind="protagonist_invariant",
                strength="hard_constraint",
                source="player",
                content="主角是萧欢，男性。百年前远古宗门合欢宗的唯一幸存者，沉睡百年苏醒，正在陷空山旧址重建宗门，并招募女弟子与收集资源。",
            ),
            CoreMemoryItem(
                id="core_story_expectation",
                kind="narrative_preference",
                strength="strong_preference",
                source="player",
                content="玩家期望的故事体验：重整宗门，结识各色修仙者，探索秘境与险地，在正魔各派与凡俗势力的夹缝中壮大自身。",
            ),
            CoreMemoryItem(
                id="core_failure_severity",
                kind="narrative_preference",
                strength="strong_preference",
                source="player",
                content="失败后果尺度：宽容",
            ),
            CoreMemoryItem(
                id="core_player_abilities",
                kind="ability_rule",
                strength="hard_constraint",
                source="player",
                content="主角的特殊能力：\n- 合欢秘术：与合欢宗弟子双修可互补灵力突破瓶颈\n- 远古神识：经历百年沉睡，神识感知力远超同阶修士",
            ),
            CoreMemoryItem(
                id="core_currency_system",
                kind="system_convention",
                strength="hard_constraint",
                source="campaign_setup",
                content="默认货币单位：灵石",
            ),
            CoreMemoryItem(
                id="core_major_world_conflicts",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="长期塑造世界的主要冲突：\n- 正道仙盟与魔道诸宗争夺灵脉秘境\n- 散修生存艰难，常年受大宗门与黑市盘剥",
            ),
            CoreMemoryItem(
                id="core_dominant_powers",
                kind="world_invariant",
                strength="hard_constraint",
                source="campaign_setup",
                content="主要主导势力：正道仙盟（如太玄门）、万宝商会、大周王朝、暗市商会",
            ),
        ]
    )


def create_fixtures() -> list[dict]:
    core_memory = get_base_core_memory()

    cases = [
        # Case 1: Tavern Inquiry (打听情报)
        {
            "case_id": "case_01_tavern_inquiry",
            "category": "tavern_inquiry",
            "title": "望江茶楼向掌柜打听妖兽精血",
            "description": "检验打听消息时，正文是否给出具象有抓手的线索，而非泛泛空话。",
            "turn_index": 2,
            "user_input": "我将两块下品灵石放在桌上推过去，向孙掌柜打听：“孙掌柜，落雁渡这几日灵材暗市里，可有二阶雷纹豹精血或者极品火髓的消息？”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_001",
                    location="落雁渡·望江茶楼二楼雅座",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="孙掌柜", attitude_to_player="生意人客套"),
                    ],
                ),
                world_clock=WorldClock(day=1, phase="下午"),
                player=PlayerState(name="萧欢", visible_status="身着月白素袍的年轻修士"),
                resources={
                    "inventory": [
                        InventoryItem(name="低阶灵石", quantity=30),
                        InventoryItem(name="传讯玉简", quantity=1),
                    ],
                    "money": 30,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府旧址"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="玩家使用灵石作为报酬向茶楼掌柜打听高阶妖兽精血，行动判定为 success。孙掌柜收下灵石并告知了关于精血流向的真实可靠情报，具体线索交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="inquire",
                        outcome_band="success",
                        effect_reasoning="支点：灵石酬劳明确；边界：掌柜提供可靠情报，授权下游展开具体线索。",
                        resolved_fact="萧欢成功从孙掌柜口中打听到了关于妖兽精血的有效情报与去向。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="落雁渡望江茶楼打听情报",
                facts=[
                    ContextFact(fact_id="f1", fact="孙掌柜是落雁渡暗市的耳目之一，专司兜售低阶情报。"),
                    ContextFact(fact_id="f2", fact="落雁渡近日有太玄门弟子和各路猎妖修士聚集。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="孙掌柜",
                    role="望江茶楼掌柜，落雁渡暗市情报贩子",
                    appearance="五十岁上下微胖长者，留着两撇山羊胡，眼睛笑成一条缝。",
                    personality_trait="精明市侩、见风使舵、认钱不认人",
                    social_style="热情迎合但说话留半句",
                    attitude_to_player="生意人客套",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": ShortTermScene(
                scene_id="scene_001",
                turn_index=1,
                user_input="进入茶楼落座",
                scene_text="你步入望江茶楼，挑了个靠江的雅座坐下。孙掌柜亲自拎着青瓷茶壶上来添茶，笑意盈盈地候着你开口。",
                closing_prompt="孙掌柜正在为你沏茶，你会怎么做？",
            ).model_dump(mode="json"),
        },

        # Case 2: Rogue Recruitment (招募交涉)
        {
            "case_id": "case_02_rogue_recruitment",
            "category": "rogue_recruitment",
            "title": "茶棚招募散修女修苏念薇",
            "description": "检验招募散修时，NPC 是否有自身利益诉求与性格顾虑，而非无条件盲从。",
            "turn_index": 4,
            "user_input": "我看着苏念薇，正色道：“合欢宗百废待兴，但灵脉未损，我承诺每月提供三块中品灵石与一部玄阶功法，你可愿随我回山成为宗门首徒？”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_002",
                    location="落雁渡·河湾茶棚",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="苏念薇", attitude_to_player="审视与好奇"),
                        ParticipantState(name="姚沉璧", attitude_to_player="冷眼旁观"),
                    ],
                ),
                world_clock=WorldClock(day=1, phase="下午"),
                player=PlayerState(name="萧欢", visible_status="年轻修士"),
                resources={
                    "inventory": [InventoryItem(name="低阶灵石", quantity=50)],
                    "money": 50,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢开出了相当优厚的招募待遇，判定为 success。苏念薇对待遇十分心动并有加入意向，但仍有身为散修的顾虑和具体要求等待确认，具体对白由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="recruit",
                        outcome_band="success",
                        effect_reasoning="支点：开出中品灵石与功法诱饵；边界：苏念薇认可条件并表态，授权下游展现其反应与具体要求。",
                        resolved_fact="萧欢的开价打动了苏念薇，苏念薇认可了宗门待遇并愿意进一步商榷加入细节。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="招募散修苏念薇",
                facts=[
                    ContextFact(fact_id="f1", fact="苏念薇是炼气八层散修，以护送商队和猎杀妖兽为生，手头拮据。"),
                    ContextFact(fact_id="f2", fact="苏念薇肩头有一只通灵火纹蟾蜍，性子好胜但极重义气。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="苏念薇",
                    role="独行散修，炼气八层应征者",
                    appearance="二十出头，高挑英气，束马尾，左眉尾有浅疤，腰挂赤铜短刀，肩趴火纹蟾蜍。",
                    personality_trait="直爽利落、好胜心强、提防宗门欺压",
                    social_style="快人快语、不拘小节",
                    attitude_to_player="审视与心动",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": ShortTermScene(
                scene_id="scene_002",
                turn_index=3,
                user_input="与姚沉璧引荐的苏念薇见面",
                scene_text="苏念薇在你对面大马金刀地坐下，双手搭在桌沿上，露出一颗小虎牙：“听说萧道友宗门要招人？只要待遇给足，能吃苦是真的。”",
                closing_prompt="苏念薇正在等待你的报价，你会怎么做？",
            ).model_dump(mode="json"),
        },

        # Case 3: Black Market Trade (黑市倒卖与讨价还价)
        {
            "case_id": "case_03_black_market_trade",
            "category": "black_market_trade",
            "title": "当铺掌柜交易旧宗法器",
            "description": "检验商业交易时，双方是否有利益交锋与微观还价质感。",
            "turn_index": 3,
            "user_input": "我将一枚泛着微光的旧宗门玉佩按在柜台上，直视严掌柜：“这可是百年前的温灵古玉，一口价，四十块灵石加上一份落雁渡周边黑市分布图，少一枚子儿我就去隔壁万宝阁。”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_003",
                    location="落雁渡暗巷·百宝当铺",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="严掌柜", attitude_to_player="贪婪试探"),
                    ],
                ),
                world_clock=WorldClock(day=1, phase="黄昏"),
                player=PlayerState(name="萧欢", visible_status="神色笃定的青袍修士"),
                resources={
                    "inventory": [InventoryItem(name="温灵古玉", quantity=1)],
                    "money": 10,
                    "money_unit": "灵石",
                    "fixed_assets": [],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢以去万宝阁施压并出示真实古玉，判定为 success。严掌柜识货且不想放过这桩买卖，接受了交易提议并取出灵石与地图，具体交锋交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="trade",
                        outcome_band="success",
                        effect_reasoning="支点：古玉价值确凿且有万宝阁竞价威胁；边界：当铺掌柜同意交易条件并交割货物。",
                        resolved_fact="萧欢成功迫使严掌柜接受交易条件，严掌柜拿出灵石与黑市地图完成交接。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="百宝当铺典当古玉",
                facts=[
                    ContextFact(fact_id="f1", fact="百宝当铺掌柜严三爷是黑市出了名的铁公鸡，但极度痴迷前朝古物。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="严掌柜",
                    role="百宝当铺掌柜，暗市黑心典当商",
                    appearance="六十来岁干瘦老头，戴着一副墨晶单片眼镜，手指枯瘦如鸡爪。",
                    personality_trait="唯利是图、嗜古成痴、谨小慎微",
                    social_style="压价成瘾、阴阳怪气",
                    attitude_to_player="贪婪而忌惮",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 4: Secret Investigation (夜探废弃阵眼)
        {
            "case_id": "case_04_secret_investigation",
            "category": "secret_investigation",
            "title": "后山阵眼搜查入侵者踪迹",
            "description": "检验探索调查时，正文是否给出具象线索，且停点留有悬念而不过度越权。",
            "turn_index": 5,
            "user_input": "我屏息潜行至被破坏的西侧聚灵阵眼旁，蹲下身用指尖捻起泥土中的残余粉末，运转神识仔细探查残留的法力属性与脚印去向。",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_004",
                    location="陷空山后山·废弃聚灵阵眼",
                    participants=[ParticipantState(name="萧欢")],
                ),
                world_clock=WorldClock(day=2, phase="深夜"),
                player=PlayerState(name="萧欢", visible_status="隐匿夜行打扮"),
                resources={
                    "inventory": [InventoryItem(name="传讯玉简", quantity=1)],
                    "money": 20,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢运用远古神识探查破坏阵眼，判定为 critical_success。萧欢成功辨识出了残留粉末的特殊功法属性，并发现了入侵者留下的关键信物印记，具体发现交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="investigate",
                        outcome_band="critical_success",
                        effect_reasoning="支点：远古神识优势；边界：发现高价值明确线索与信物痕迹，授权下游展开细节。",
                        resolved_fact="萧欢在阵眼处成功发现了入侵者留下的特殊阴冥法力残渣以及一枚带刻字的黑铁暗器。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="后山聚灵阵眼勘验",
                facts=[
                    ContextFact(fact_id="f1", fact="陷空山外围近日有疑似黑煞宗或百毒谷的生面孔在暗中打探。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 5: Faction Confrontation (阵营冲突与言语试探)
        {
            "case_id": "case_05_faction_confrontation",
            "category": "faction_confrontation",
            "title": "码头石桥应对太玄门执事盘查",
            "description": "检验高压盘问场景中，正文是否有剑拔弩张的交锋感与停点心理博弈。",
            "turn_index": 2,
            "user_input": "面对执事递过来的探灵镜，我神色自若地任由青光扫过，同时从容出示青云商会的客卿腰牌，微笑反问：“陆仙长如此大阵仗封桥盘查，莫非是落雁渡混进了什么魔门要犯？”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_005",
                    location="落雁渡·青石拱桥头",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="陆执事", attitude_to_player="傲慢疑虑"),
                    ],
                ),
                world_clock=WorldClock(day=1, phase="上午"),
                player=PlayerState(name="萧欢", visible_status="青袍儒雅散修"),
                resources={
                    "inventory": [InventoryItem(name="青云商会假腰牌", quantity=1)],
                    "money": 15,
                    "money_unit": "灵石",
                    "fixed_assets": [],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢从容应对探灵镜并以商会身份作掩护，判定为 success。探灵镜未察觉异样，陆执事打消了部分疑虑，顺口透露了封桥搜捕的目标背景，具体对白交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="bluff_and_inquire",
                        outcome_band="success",
                        effect_reasoning="支点：伪装得当且言语得体；边界：通过盘查并获得关于搜查目标的口风。",
                        resolved_fact="萧欢成功通过了探灵镜排查，陆执事收起戒备，向萧欢透露了太玄门正在搜寻一名盗取丹方的叛逃内门女修。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="落雁渡石桥太玄门盘查",
                facts=[
                    ContextFact(fact_id="f1", fact="太玄门是中州正道魁首之一，执事弟子向来看不起凡俗与散修。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="陆执事",
                    role="太玄门外门执事弟子，筑基初期",
                    appearance="三十来岁身着月白道袍，眉眼狭长，腰佩青锋法剑，手托八卦探灵镜。",
                    personality_trait="自命不凡、狐假虎威、严防死守",
                    social_style="居高临下、盛气凌人",
                    attitude_to_player="傲慢审视转为例行公事",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 6: Dungeon Puzzle (地下古洞府解密)
        {
            "case_id": "case_06_dungeon_puzzle",
            "category": "dungeon_puzzle",
            "title": "洞府密室机关解密",
            "description": "检验环境探索与机关开启时的感官细节与新发现抓手。",
            "turn_index": 3,
            "user_input": "我按照合欢宗旧典籍残卷上的方位，将灵力注入石门中央的阴阳交泰图腾，逆时针转动三圈后猛然按下中枢暗扣。",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_006",
                    location="陷空山地宫·藏宝室石门前",
                    participants=[ParticipantState(name="萧欢")],
                ),
                world_clock=WorldClock(day=2, phase="下午"),
                player=PlayerState(name="萧欢", visible_status="全神贯注的修士"),
                resources={
                    "inventory": [InventoryItem(name="合欢宗旧典籍（残卷）", quantity=1)],
                    "money": 20,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢准确对应典籍残卷操作机关，判定为 success。沉重的石门轰然开启，露出了被尘封百年的藏宝室内部景象，具体环境与发现交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="operate_mechanism",
                        outcome_band="success",
                        effect_reasoning="支点：典籍记载无误；边界：石门开启，展现藏宝室内景。",
                        resolved_fact="萧欢成功解开石门图腾机关，密室大门缓缓洞开，显露出藏室内的陈列与微弱灵光。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="地宫藏宝室开启",
                facts=[
                    ContextFact(fact_id="f1", fact="该藏宝室自百年前宗门大变后便被彻底封死，从未有人踏足。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 7: Inn Rest & Director Atmosphere (客栈调息与导演氛围注入)
        {
            "case_id": "case_07_inn_rest_overhear",
            "category": "inn_rest_overhear",
            "title": "客栈歇息旁听暗流风声",
            "description": "检验日常过渡场景中，正文是否能融合导演舞台材料与环境微波澜。",
            "turn_index": 2,
            "user_input": "我在客栈临街窗边自斟自饮，运转周天平复体内气血，同时侧耳倾听大堂内各路行商与散修的议论。",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_007",
                    location="落雁渡·悦来客栈一楼大堂",
                    participants=[ParticipantState(name="萧欢")],
                ),
                world_clock=WorldClock(day=1, phase="晚上"),
                player=PlayerState(name="萧欢", visible_status="独饮的清冷修士"),
                resources={
                    "inventory": [InventoryItem(name="低阶灵石", quantity=25)],
                    "money": 25,
                    "money_unit": "灵石",
                    "fixed_assets": [],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢在客栈正常调息休整并留意四周，判定为 success。萧欢气血平复，并成功听取了大堂中几桌修士关于近期古怪变故的交谈，具体交谈内容由下游结合导演材料展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="rest_and_listen",
                        outcome_band="success",
                        effect_reasoning="支点：感知敏锐且身处热闹客栈；边界：调息完成并捕获环境风声。",
                        resolved_fact="萧欢成功完成调息，体内气力恢复充沛，并敏锐捕捉到邻桌修士关于周边山林异动的窃窃私语。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="悦来客栈调息与打探",
                facts=[
                    ContextFact(fact_id="f1", fact="悦来客栈是落雁渡鱼龙混杂之地，夜间常有各路见不得光的修士落脚。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [],
            "director_stage": DirectorStageResult(
                reasoning="当前无重大剧情推进，注入一段关于陷空山夜间赤色妖云异象的传闻作为环境渲染。",
                materials=[
                    DirectorStageMaterial(
                        kind="setting",
                        resource_id="setting_red_cloud",
                        guidance="在大堂散修的闲聊中，隐晦提及昨夜陷空山深处有赤色妖云冲天而起、似有异兽筑巢的传闻。",
                        payload={"setting_name": "陷空山赤云异象", "rumor_content": "昨夜子时陷空山深处有血色雷光，惊动了山外巡逻队。"},
                    )
                ],
            ).model_dump(mode="json"),
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 8: Moral Dilemma & Rescue (密林救援与矛盾抉择)
        {
            "case_id": "case_08_moral_dilemma",
            "category": "moral_dilemma",
            "title": "山脚密林救助带伤女修柳若依",
            "description": "检验救援遇袭 NPC 时，对方是否展现出警惕、戒备与复杂背景牵连。",
            "turn_index": 2,
            "user_input": "我蹲下身扶起灌木丛中的血衣女子，渡入一道精纯温和的灵力护住她心脉，沉声道：“姑娘莫慌，追你的人已经被我引开。你究竟是何身份，为何伤成这样？”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_008",
                    location="陷空山山脚·阴风古道旁的密林",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="柳若依", attitude_to_player="虚弱警惕"),
                    ],
                ),
                world_clock=WorldClock(day=2, phase="清晨"),
                player=PlayerState(name="萧欢", visible_status="白袍沾染露水的修士"),
                resources={
                    "inventory": [
                        InventoryItem(name="基础聚灵丹", quantity=4),
                        InventoryItem(name="低阶灵石", quantity=20),
                    ],
                    "money": 20,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢及时施救并出言安抚，判定为 success。女子心脉稳住苏醒，但因身上背负大祸，在感激的同时难掩极度戒备与恐惧，勉强吐露部分真相，具体由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="heal_and_question",
                        outcome_band="success",
                        effect_reasoning="支点：灵力救治有效；边界：女子脱离生命危险苏醒，并道出追兵来头。",
                        resolved_fact="萧欢成功稳住了血衣女子的伤势，女子苏醒后惊魂未定地告知萧欢自己乃是遭到万毒谷执事追杀。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="密林救治重伤女修",
                facts=[
                    ContextFact(fact_id="f1", fact="万毒谷是中州南方凶名赫赫的魔道宗门，行事狠辣不留活口。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="柳若依",
                    role="身负重伤的逃亡女修，怀揣秘宝残卷",
                    appearance="容貌娇美清丽但面色惨白如纸，嘴角带血，藕荷色纱裙破损多处，紧捂着怀中包裹。",
                    personality_trait="外柔内刚、戒心极重、恩怨分明",
                    social_style="虚弱而警戒，字字带着试探",
                    attitude_to_player="感激与提防交织",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 9: Partial Success (部分成功与局势推进)
        {
            "case_id": "case_09_failed_action_recovery",
            "category": "failed_action_recovery",
            "title": "拍卖会暗中探查古玉禁制",
            "description": "检验 Partial Success 时，正文是否能平衡收益与局限，制造下一步博弈。",
            "turn_index": 3,
            "user_input": "我微闭双目，将一缕微不可查的神识丝线顺着木椅脚探入拍卖台红绸之下，强行解析被遮蔽的压轴古玉符文。",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_009",
                    location="落雁渡地下黑市·暗金拍卖厅",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="红拂女", attitude_to_player="台上主持拍卖"),
                    ],
                ),
                world_clock=WorldClock(day=1, phase="晚上"),
                player=PlayerState(name="萧欢", visible_status="坐在后排阴影中的蒙面客人"),
                resources={
                    "inventory": [InventoryItem(name="低阶灵石", quantity=80)],
                    "money": 80,
                    "money_unit": "灵石",
                    "fixed_assets": [],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="拍卖台设有阵法防窥视，萧欢强行探查判定为 partial_success。萧欢未能看清完整古玉图腾，但成功感应到了玉石散发的极寒煞气并确定其来自北海冰魄宗，且在禁制反弹前及时斩断神识未被抓现行。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="probe_shield",
                        outcome_band="partial_success",
                        effect_reasoning="支点：神识隐蔽但阵法坚固；边界：获悉部分煞气属性与来历，未暴露自身，但未能洞悉全貌。",
                        resolved_fact="萧欢成功探明古玉散发着北海冰魄宗的极寒阴煞之气，但在阵法警觉前及时撤回神识，未能看清玉佩全貌图腾。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="黑市拍卖厅神识试探",
                facts=[
                    ContextFact(fact_id="f1", fact="黑市拍卖由万宝阁暗中护盘，台上布置了三阶防窥聚灵阵。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="红拂女",
                    role="黑市首席拍卖师，筑基中期女修",
                    appearance="一袭红裙妖娆动人，巧笑倩兮，举手投足间带着媚惑之气，眼神却锐利如刀。",
                    personality_trait="八面玲珑、洞若观火、精明果决",
                    social_style="媚态横生而防线森严",
                    attitude_to_player="公事公办的商人态度",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },

        # Case 10: High Stakes Negotiation (高压谈判与阵营博弈)
        {
            "case_id": "case_10_high_stakes_negotiation",
            "category": "high_stakes_negotiation",
            "title": "画舫内厅与万宝阁大掌柜谈判",
            "description": "检验高压谈判时，NPC 是否有商贾老手的城府与有来有回的筹码拉锯。",
            "turn_index": 4,
            "user_input": "我轻轻放下茶盏，看着钱大掌柜微笑道：“钱掌柜，若无我合欢宗秘法提纯丹药，贵阁这批劣质灵药就只能烂在库里。我要落雁渡三成的丹药分润，外加这批灵草的优先选购权，少一成我们便免谈。”",
            "core_memory": core_memory.model_dump(mode="json"),
            "current_state": CurrentState(
                scene=SceneState(
                    scene_id="scene_010",
                    location="落雁渡江面·万宝阁水上画舫内厅",
                    participants=[
                        ParticipantState(name="萧欢"),
                        ParticipantState(name="钱管事", attitude_to_player="城府深沉"),
                    ],
                ),
                world_clock=WorldClock(day=2, phase="黄昏"),
                player=PlayerState(name="萧欢", visible_status="气定神闲的白袍修士"),
                resources={
                    "inventory": [
                        InventoryItem(name="古法淬灵丹方样本", quantity=1),
                        InventoryItem(name="低阶灵石", quantity=60),
                    ],
                    "money": 60,
                    "money_unit": "灵石",
                    "fixed_assets": ["陷空山灵脉洞府"],
                },
            ).model_dump(mode="json"),
            "turn_resolution": TurnResolutionResult(
                resolution_reasoning="萧欢手握丹药提纯的绝活切中万宝阁痛点，判定为 success。钱管事被萧欢的底气和筹码触动，虽震惊于其胃口，但意识到合作的巨大价值，收敛了傲气开始认真还价与拟定合作底线，具体对白交由下游展开。",
                action_resolutions=[
                    ActionResolutionFrame(
                        action_id="act_01",
                        sequence=1,
                        actor=["萧欢"],
                        action_type="negotiate",
                        outcome_band="success",
                        effect_reasoning="支点：切中对方商业死穴；边界：打破对方心理防线，迫使钱掌柜认真对待并开出底线反制条件。",
                        resolved_fact="萧欢成功以丹药秘法压制了钱掌柜的气焰，钱掌柜认可了合作价值并开始就分润比例与供货条件进行实质性商榷。",
                    )
                ],
            ).model_dump(mode="json"),
            "context_facts": ContextFactSet(
                turn_focus="万宝阁画舫商业谈判",
                facts=[
                    ContextFact(fact_id="f1", fact="万宝阁落雁渡分舵近日积压了一大批杂质过多的下品灵草，急需高明丹师化解亏损。"),
                ],
            ).model_dump(mode="json"),
            "npc_profiles": [
                NPCProfileDirectorView(
                    name="钱管事",
                    role="万宝商会落雁渡分舵大管事，筑基后期",
                    appearance="五十岁左右，锦衣华服，大腹便便，满脸堆笑但双眼精光闪烁，转动着手里的两枚极品灵玉核桃。",
                    personality_trait="笑里藏刀、算无遗策、不见兔子不撒鹰",
                    social_style="言语温和但字字见血",
                    attitude_to_player="从轻视转为极其郑重",
                    disclosure_level="face_to_face",
                ).model_dump(mode="json")
            ],
            "director_stage": None,
            "engaged_events": [],
            "recent_scene": None,
        },
    ]

    return cases


def main():
    fixtures = create_fixtures()
    FIXTURES_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURES_PATH.write_text(
        json.dumps(fixtures, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Generated {len(fixtures)} fixture cases to {FIXTURES_PATH}")


if __name__ == "__main__":
    main()
