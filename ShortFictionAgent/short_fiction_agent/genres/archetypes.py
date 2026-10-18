"""题材原型（archetype）基线包。

番茄/起点有 ~26 个细分类别, 逐个深度作者化不现实。把它们归并到少数**结构原型**:
- male_progression: 男频·单主角进度流(力量/事业/复仇)——覆盖 武侠/仙侠/奇幻/科幻/
  历史/军事/游戏/体育/诸天无限/现实(男) 等。
- female_romance: 女频·双主角言情——覆盖 古代言情/现代言情/玄幻言情/仙侠奇缘/
  科幻空间/游戏竞技/现实生活 等。

各细分题材从对应原型派生(共享词表/prompt, 覆盖 id/名称/别名/channel), 都能端到端跑;
需要时再为某个细分题材单独作者化深度包(如已有的 玄幻/都市/校园)。这是诚实的分层:
**全部类别都注册可跑, 旗舰题材深度作者化, 其余走原型基线。**
"""

from pathlib import Path
from .base import GenrePack

_MALE_PROMPTS = str(Path(__file__).resolve().parent / "archetype_prompts" / "male_progression")
_FEMALE_PROMPTS = str(Path(__file__).resolve().parent / "archetype_prompts" / "female_romance")


# ── 男频进度流通用词表 ──────────────────────────────────────
MALE_PROGRESSION_LEXICONS = {
    "opening_strong_hook": ["重生", "逆袭", "打脸", "退婚", "觉醒", "系统", "身份", "真相", "夺", "复仇", "天才", "底牌"],
    "beat_hard_markers": ["打脸", "碾压", "击败", "夺", "胜", "挑战", "羞辱", "逆袭", "扬名", "淘汰", "出局", "生死", "对决", "威胁", "背叛", "夺权"],
    "beat_soft_markers": ["思索", "犹豫", "观察", "回忆", "沉吟", "暗忖"],
    "early_change_markers": ["重生", "逆袭", "打脸", "觉醒", "夺", "胜", "扬名", "结仇", "契约", "淘汰", "夺权"],
    "early_visible_consequence": ["当众", "淘汰", "出局", "羞辱", "击败", "扬名", "封锁", "夺权"],
    "female_independent": ["事业", "独立", "尊严", "目标", "家族", "梦想"],
    "male_independent": ["变强", "事业", "复仇", "真相", "野心", "夺回", "称霸", "扬名", "底牌"],
    "romance_only": ["喜不喜欢", "心意", "暗恋", "一厢情愿"],
    "text_desire": ["要", "想", "必须", "决意", "夺", "争", "证明", "拿下", "保住", "扳回"],
    "text_obstacle": ["但", "却", "对手", "强敌", "压制", "封锁", "背叛", "威胁", "势力", "围杀", "陷阱"],
    "text_action": ["出手", "击", "夺", "拿下", "签", "踏出", "拨通", "摊牌", "拒绝", "反击", "破局"],
    "text_cost": ["代价", "重创", "损失", "出局", "羞辱", "颜面", "扫地", "失去", "当众", "结仇"],
    "text_turn": ["第一次", "从此", "之后", "再也", "翻盘", "逆转", "扬名", "击败", "拿下", "反杀"],
    "text_visible_consequence": ["当众", "众人", "围观", "对手", "扬名", "淘汰", "出局", "媒体", "势力"],
    "famous_scene_types": ["当众打脸", "绝地逆袭", "身份揭露", "扮猪吃虎", "强敌对决", "扬名立万", "翻盘反杀"],
    "payoff_markers": ["打脸", "逆袭", "碾压", "击败", "夺", "胜", "扬名", "收服", "翻盘", "认怂", "反杀"],
    "debt_markers": ["小看", "羞辱", "退婚", "背叛", "淘汰", "欺压", "夺", "结仇", "威胁"],
    "character_tag_candidates": ["落魄", "低调", "隐忍", "腹黑", "高傲", "天才", "重生", "野心", "护短", "复仇"],
    "character_tag_collide": ["冲突", "反差", "碾压", "对撞", "试探", "结仇"],
    "rival_names": ["对手", "反派", "强敌", "宿敌", "少主", "小人", "竞争者"],
    "rival_function": ["挑战", "压制", "碾压", "逼", "选择", "价值", "比较", "施压", "算计", "背叛"],
    "style_phrases": ["冷笑", "淡淡道", "不以为然", "嘴角一扬", "眼神一冷", "不为所动"],
    "style_images": ["夜色", "灯火", "风", "烟", "目光", "影子"],
    "long_suspense_markers": ["身世", "真相", "复仇", "秘密", "棋局", "底牌", "布局"],
    "ng_weak_ending": ["余味", "明日", "若有所思", "意味深长", "夜色", "沉默", "长叹"],
    "ng_weak_conflict_weak": ["沉默", "犹豫", "观察", "暗忖", "思索", "不为所动"],
    "ng_weak_conflict_strong": ["打脸", "击败", "夺", "背叛", "淘汰", "退婚", "对决", "生死", "夺权"],
    "ng_mood_hook_strong": ["击败", "夺", "逆袭", "扬名", "出手", "翻盘", "摊牌", "反杀", "揭露"],
    "ng_weak_change_weak": ["似乎", "隐约", "有一点", "仿佛", "略微"],
    "ng_weak_change_concrete": ["击败", "夺得", "扬名", "翻盘", "拿下", "出局", "反杀", "揭穿"],
    "ng_repetitive_silence": ["冷笑", "淡淡道", "不为所动", "嘴角一扬", "眼神一冷"],
    "ng_mood_tail_strong": ["击败", "夺", "逆袭", "扬名", "出手", "翻盘", "摊牌", "反杀"],
    "ng_mood_tail_mood": ["夜色", "灯火", "风", "烟", "沉默", "明日", "余味"],
    "opening_strong_endings": ["击败", "夺", "逆袭", "出手", "没想到", "为何", "怎么办", "翻盘", "反杀"],
    "opening_visible_consequences": ["当众", "众人", "对手", "淘汰", "出局", "围观", "扬名", "羞辱", "势力"],
}


# ── 女频言情通用词表 ──────────────────────────────────────
FEMALE_ROMANCE_LEXICONS = {
    "opening_strong_hook": ["重逢", "契约", "误会", "身份", "暗恋", "告白", "退婚", "豪门", "秘密", "心动", "替身", "婚约"],
    "beat_hard_markers": ["告白", "吃醋", "误会", "争吵", "和解", "守护", "牺牲", "退婚", "曝光", "当众", "选择", "拒绝", "公开", "维护", "心动"],
    "beat_soft_markers": ["心想", "出神", "犹豫", "回忆", "沉默", "暗想"],
    "early_change_markers": ["重逢", "契约", "误会", "身份", "告白", "退婚", "曝光", "公开", "守护", "选择", "心动", "婚约"],
    "early_visible_consequence": ["当众", "公开", "曝光", "退婚", "拒绝", "维护", "选择"],
    "female_independent": ["事业", "独立", "尊严", "梦想", "家族", "目标", "成长"],
    "male_independent": ["事业", "责任", "家族", "守护", "底线", "目标"],
    "romance_only": ["喜不喜欢", "爱不爱", "心意", "暗恋", "一厢情愿"],
    "text_desire": ["想", "要", "希望", "决定", "守护", "靠近", "证明", "在乎"],
    "text_obstacle": ["但", "却", "误会", "身份", "家族", "前任", "情敌", "距离", "误解", "阻拦"],
    "text_action": ["走近", "拥抱", "牵手", "递", "拨通", "转身", "拦住", "告白", "拒绝", "守护"],
    "text_cost": ["误会", "错过", "眼泪", "受伤", "心疼", "失去", "当众", "代价", "委屈"],
    "text_turn": ["第一次", "从此", "之后", "再也", "心动", "承认", "告白", "和解", "守护", "回头"],
    "text_visible_consequence": ["当众", "公开", "众人", "围观", "曝光", "家族", "情敌", "媒体"],
    "famous_scene_types": ["公开维护", "身份揭露", "吃醋告白", "误会爆发", "牺牲守护", "重逢回头", "当众示爱"],
    "payoff_markers": ["心动", "告白", "牵手", "拥抱", "接吻", "守护", "吃醋", "和解", "主动", "承认", "回头"],
    "debt_markers": ["误会", "暗恋", "隐瞒", "错过", "吃醋", "退婚", "身份", "替身", "前任"],
    "character_tag_candidates": ["要强", "敏感", "独立", "高冷", "腹黑", "深情", "隐忍", "傲娇", "温柔"],
    "character_tag_collide": ["冲突", "互补", "反差", "误会", "靠近", "对撞", "试探"],
    "rival_names": ["情敌", "女二", "男二", "前任", "白莲花", "竞争者", "追求者"],
    "rival_function": ["主动", "公开", "逼", "承认", "选择", "价值", "比较", "误会", "施压"],
    "style_phrases": ["没有说话", "像什么都没发生", "收回视线", "低下头", "心头一颤", "鼻子一酸"],
    "style_images": ["月光", "夜色", "灯火", "风", "雨", "眼泪", "影子", "窗"],
    "long_suspense_markers": ["身世", "误会", "秘密", "真相", "旧情", "替身", "婚约", "重逢"],
    "ng_weak_ending": ["余味", "明天", "月光", "夜色", "沉默", "没有说话", "若有所思", "眼泪"],
    "ng_weak_conflict_weak": ["沉默", "犹豫", "心想", "暗想", "出神", "没说"],
    "ng_weak_conflict_strong": ["告白", "误会", "退婚", "争吵", "守护", "牺牲", "曝光", "当众", "选择", "身份"],
    "ng_mood_hook_strong": ["告白", "心动", "误会", "守护", "曝光", "重逢", "选择", "承认", "和解", "回头"],
    "ng_weak_change_weak": ["似乎", "隐约", "有一点", "仿佛", "说不清", "略微"],
    "ng_weak_change_concrete": ["告白", "心动", "守护", "承认", "和解", "选择", "失去", "回头", "牵手"],
    "ng_repetitive_silence": ["没有说话", "低下头", "收回视线", "心头一颤", "鼻子一酸", "出神"],
    "ng_mood_tail_strong": ["告白", "心动", "守护", "曝光", "重逢", "选择", "承认", "和解", "回头"],
    "ng_mood_tail_mood": ["月光", "夜色", "灯火", "风", "雨", "沉默", "明天", "眼泪", "窗"],
    "opening_strong_endings": ["心动", "告白", "误会", "重逢", "没想到", "为什么", "怎么办", "守护", "回头"],
    "opening_visible_consequences": ["当众", "公开", "众人", "曝光", "家族", "情敌", "围观", "退婚", "维护"],
}


_MALE_SCAFFOLD = {
    "novel": {"tone": "热血爽快、有压迫感, 但金手指有代价、不降智平推",
              "pace": "事件驱动、节奏明快, 周期性兑现爽点", "perspective": "第三人称有限视角"},
    "style": {"prohibited_expressions": ["命运的齿轮开始转动", "全场哗然", "震惊所有人", "不可一世", "嚣张至极"],
              "required_elements": ["清晰的进度/力量体系与边界", "变强/复仇/事业的外部目标线",
                                    "爽点要有铺垫、围观与代价", "动作与对白而非心理总结"]},
}
_FEMALE_SCAFFOLD = {
    "novel": {"tone": "细腻、有张力、克制处见真实, 甜中带虐", "pace": "情绪与事件交织, 周期性兑现情绪",
              "perspective": "第三人称有限视角"},
    "style": {"prohibited_expressions": ["命运的齿轮开始转动", "霸道总裁爱上我", "一眼万年", "心如鹿撞般"],
              "required_elements": ["具体的关系推进与误会和解", "女主有独立目标线、不恋爱脑",
                                    "甜点/虐点要有铺垫与后果", "动作与对白而非心理总结"]},
}


MALE_PROGRESSION_PACK = GenrePack(
    genre_id="male_progression", display_name="男频进度流", channel="male",
    lexicons=MALE_PROGRESSION_LEXICONS,
    progression_axes=["power_level", "status_level"], relationship_jump_threshold=18,
    character_roles=["protagonist"], prompts_dir=_MALE_PROMPTS,
    rules_filename="archetype_rules.md",
    system_persona="你是一名擅长中国男频爽文(单主角进度流: 力量/事业/复仇)的作家和创作助手。",
    scaffolding=_MALE_SCAFFOLD,
)

FEMALE_ROMANCE_PACK = GenrePack(
    genre_id="female_romance", display_name="女频言情", channel="female",
    lexicons=FEMALE_ROMANCE_LEXICONS,
    progression_axes=["romance_level", "trust_level", "conflict_level"], relationship_jump_threshold=12,
    character_roles=["protagonist_male", "protagonist_female"], prompts_dir=_FEMALE_PROMPTS,
    rules_filename="archetype_rules.md",
    system_persona="你是一名擅长中国女频言情小说的作家和创作助手。",
    scaffolding=_FEMALE_SCAFFOLD,
)

ARCHETYPES = {"male_progression": MALE_PROGRESSION_PACK, "female_romance": FEMALE_ROMANCE_PACK}

