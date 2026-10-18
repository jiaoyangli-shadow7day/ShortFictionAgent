"""LLM 客户端抽象与实现。"""

import os
import json
import yaml
from pathlib import Path
from typing import Optional
from .models import ModelConfig


class LLMClient:
    """LLM 客户端抽象基类。"""

    def generate(self, prompt: str, task_type: str = "general") -> str:
        raise NotImplementedError


class MockLLMClient(LLMClient):
    """Mock LLM 客户端，用于本地无 API 测试闭环。"""

    def generate(self, prompt: str, task_type: str = "general") -> str:
        handler = getattr(self, f"_mock_{task_type}", None)
        if handler:
            return handler(prompt)
        return self._mock_general(prompt)

    def _mock_general(self, prompt: str) -> str:
        return "（Mock 通用响应）\n这是一段模拟输出，用于本地流程测试。"

    def _mock_setting(self, prompt: str) -> str:
        return """```yaml
novel:
  title: "第七排靠窗的你"
  genre: "青春校园恋爱"
  sub_genre: "双向暗恋、成长治愈"
  stage: "高中"
  target_words: 300000
  tone: "克制、清爽、真实，但结构层必须有追读钩子和情绪兑现"
  pace: "张弛交替，慢章节也要推进故事问题"
  perspective: "第三人称有限视角"
  core_theme: "在不确定的年纪，确认一件确定的事"

protagonist_male:
  name: "陈屿"
  age: 17
  grade: "高二"
  role: "伪装完美的理科天才，实际被家庭期待压得喘不过气"
  appearance: "身高178，偏瘦，戴细框眼镜，校服总是穿得规整"
  personality: "不善表达但观察力强，习惯用行动代替语言"
  background: "父亲是工程师常年出差，母亲是初中数学老师，家境中等"
  strengths:
    - "理科思维强，数学物理稳定年级前三"
    - "情绪稳定，危机时刻可靠"
    - "对在意的人非常细心"
  flaws:
    - "不善主动社交，容易被误解为冷漠"
    - "对自己的情绪缺乏觉察，习惯压抑"
    - "面对感情时防御机制强"
  defense_mechanisms:
    - "用沉默回避亲密话题"
    - "用理性分析掩盖内心波动"
  speaking_style: "话少，每句话都有分量；不废话，不说网络流行语"
  core_desire: "被真正理解，不需要解释自己"
  core_fear: "重要的人离开"
  growth_arc: "学会表达情感，从自我保护到主动靠近"
  forbidden_behaviors:
    - "禁止突然浪漫化，禁止油腻发言"
    - "禁止未经铺垫的性格突变"

protagonist_female:
  name: "林栀"
  age: 17
  grade: "高二"
  role: "想在重点班站稳脚跟的转学生，语文强但数学短板明显"
  appearance: "身高163，长发马尾，眼神认真，笑起来有浅浅的酒窝"
  personality: "表面要强内心敏感，在意别人的看法但不愿表现出来"
  background: "父母因工作调动全家搬到江苏，曾是原学校语文课代表"
  strengths:
    - "文字感受力强，作文经常被表扬"
    - "坚韧，遇到困难不轻易放弃"
    - "有同理心，能察觉别人的情绪"
  flaws:
    - "过度自我要求，容易焦虑"
    - "对批评敏感，容易钻牛角尖"
    - "有时因为要强而拒绝帮助"
  defense_mechanisms:
    - "用'没事'掩盖真实情绪"
    - "假装不在意来保护自己"
  speaking_style: "条理清晰，偶尔会引用诗句或书里的句子，但不做作"
  core_desire: "在新环境站稳脚跟，被认可"
  core_fear: "被当成弱者/被孤立"
  growth_arc: "从敏感要强到学会示弱与信任，学会接受别人的好"
  forbidden_behaviors:
    - "禁止突然恋爱脑，禁止降智"
    - "禁止为发糖而放弃自尊"

initial_relationship:
  relationship_stage: "初识"
  stage_description: "两人刚成为前后座，还处于客气疏离状态"
  romance_level: 5
  trust_level: 8
  conflict_level: 15
  male_to_female:
    current_attitude: "觉得新同学安静努力，但刻意保持距离"
    hidden_feeling: "注意到她每次回答语文问题时的认真神情"
    recent_change: "还没有明显变化"
  female_to_male:
    current_attitude: "觉得前座男生有点冷，但很聪明"
    hidden_feeling: "想知道他在草稿纸上算什么题"
    recent_change: "还没有明显变化"
  unresolved_tensions:
    - "两人还没有真正交谈过"
  forbidden_progress:
    - "当前阶段禁止直接表白"
    - "当前阶段禁止暧昧身体接触"
    - "当前阶段禁止一方突然过度关注另一方"

school_setting:
  school_name: "江城市第一中学"
  school_type: "省重点高中"
  location: "江苏某地级市"
  features:
    - "有百年历史的老校区，梧桐树很多"
    - "教学楼五层，没有电梯"
    - "每个年级12个班，1-4班为理科重点班"
    - "有晚自习，走读生可以申请不上"
    - "食堂有两个，一食堂便宜二食堂好吃"
    - "操场旁边有一条银杏大道"
  daily_schedule:
    - "7:20 早读"
    - "8:00-11:50 上午四节课"
    - "12:00-14:00 午休"
    - "14:10-17:00 下午三节课"
    - "18:30-21:00 晚自习"
  yearly_events:
    - "9月：开学、分班、教师节"
    - "10月：月考、运动会"
    - "11月：期中考试"
    - "12月：文化艺术节"
    - "1月：期末考试"
    - "3月：百日誓师（高三）"
    - "5-6月：高考、中考"
    - "7-8月：暑假"
```"""

    def _mock_book_outline(self, prompt: str) -> str:
        return """# 《第七排靠窗的你》全书大纲

## 全书结构

本书共分为 4 卷，约 60 章，目标 30 万字。

## 市场定位与追读引擎

- 一句话卖点：重点班偏科转学生和伪装完美的年级前三互相看见，却因为成绩、家庭和转学秘密一次次错过。
- 人设标签：要强转学生 × 失控边缘的天才学霸。
- 核心故事问题：林栀能否在新学校站稳脚跟，并发现陈屿冷淡外表下真正的崩溃？
- 强欲望：林栀想证明自己不是拖后腿的人；陈屿想维持完美成绩和家庭期待。
- 具体阻碍：成绩排名、班级偏见、家庭压力、转学隐情和两人都不愿示弱的防御机制。
- 情绪兑现：通过公开维护、秘密被发现、成绩反转、毕业选择和重逢回收逐步兑现。

## 八模块商业编辑设计

- OpeningHookAgent：首章使用“匿名薄荷糖页码 + 草稿纸被划掉的她字”作为强钩子。
- BeatHardnessScorer：每 3 章至少一次公开压力或秘密暴露，第一卷重点用成绩、便签、班级议论制造硬事件。
- FamousSceneAgent：第 3、8、14 章安排第一次可见维护、第一次失控、第一次公开站队。
- PayoffLedger：第 1 章制造匿名帮助债，第 3 章偿还第一次维护；第 8 章制造成绩崩盘痛点，第 10 章用约定小甜点偿还。
- CharacterTagPressure：林栀“要强偏科转学生”和陈屿“伪装稳定学霸”的标签持续互相刺痛。
- RivalFunctionGuard：周诚代表更主动、更公开的表达方式，逼陈屿承认自己在意。
- StyleDedupeAgent：第一卷少用窗、光、风收尾，改用错题本、薄荷糖、公告栏、广播站等具体校园物件。
- LongSuspensePlanner：长线悬念围绕林栀转学真相、陈屿草稿纸上的“她”和毕业后的旧物回收推进。

## 全书级长线悬念

- 长线悬念：林栀以为陈屿只是顺手帮她，后来才发现他从第一天就保存着她被划掉的草稿和错题页码。
- 开篇埋点：薄荷糖页码、草稿纸上被划掉的“她”、原学校校名。
- 中段误导：林栀误以为陈屿帮助她是出于学霸责任，周诚则以为陈屿只是怕班级扣分。
- 局部揭示：陈屿考试崩盘后，林栀发现他也一直在隐藏失控。
- 最终回收：毕业选择时旧物被重新解释，确认两人的沉默不是单向暗恋。

---

### 第一卷：秋天之前（第 1-15 章）
**核心功能：建立人物与世界观，铺垫关系张力**

**时间线**：高二上学期 9 月 - 10 月
**男女主关系变化**：从陌生前后座 → 偶有交流 → 产生初步注意
**主要校园事件**：
- 开学分班，林栀转学成为陈屿后座
- 第一次月考，两人成绩引人注目
- 运动会，班级接力赛意外事件
- 语文课上的小组合作
**人物成长**：
- 林栀适应新环境，从孤立到初步融入
- 陈屿开始从"不在意周围"到"注意到某个人"
**主要伏笔**：
- 林栀转学的真正原因
- 陈屿看似平静的家庭背后的张力
- 班级中存在的小团体
**节奏提醒**：此卷感情必须克制，以建立人物和校园氛围为主。

---

### 第二卷：冬天的秘密（第 16-35 章）
**核心功能：关系靠近，共同经历，伏笔展开**

**时间线**：高二上学期 11 月 - 高二下学期 1 月
**男女主关系变化**：从互相注意 → 产生默契 → 各自内心动摇
**主要校园事件**：
- 期中考试后班级调整
- 文化艺术节筹备
- 初雪和期末
- 寒假前的告别
**人物成长**：
- 林栀开始真正信任身边人
- 陈屿第一次面对自己的情绪
**主要伏笔**：
- 某人家庭问题的浮现
- 关于未来的焦虑
- 一个关于"选择"的核心矛盾

---

### 第三卷：春天的风（第 36-50 章）
**核心功能：关系转折，矛盾升级，成长加速**

**时间线**：高二下学期
**男女主关系变化**：从内心动摇 → 外部压力 → 误会与理解交替
**主要校园事件**：
- 分科/分班压力
- 运动会、社团活动
- 家长会带来的影响
- 关于未来的对话
**人物成长**：
- 两人都需要在"想要"和"应该"之间做选择
- 学会直面而非回避

---

### 第四卷：盛夏的答案（第 51-60 章）
**核心功能：伏笔回收，关系落点，主题回应**

**时间线**：高二暑假到高三开学
**男女主关系变化**：从混乱 → 清晰 → 确认
**主要校园事件**：
- 暑假补课
- 高三开学
**人物成长**：
- 成长并非解决问题，而是学会与问题共处
- 在不确定中确认某些确定的事
**结尾方向**：温暖但不油腻，确定但不终结

---

## 节奏风险提示
1. 第一卷不要过长，15 章内必须建立基本张力
2. 第二卷要避免"纯粹撒糖"，必须有外部矛盾
3. 第三卷不能让误会变成狗血
4. 第四卷结尾不能强行完美
"""

    def _mock_chapter_outline(self, prompt: str) -> str:
        return """volume: 1
chapters:
- chapter_id: 1
  title: "新来的转学生"
  story_question: "林栀能不能在重点班站稳第一天，而陈屿为什么会注意到她的短板？"
  reader_hook: "转学生被安排到年级前三后座，第一天就暴露了语文强、数学短板的反差"
  character_desire: "林栀想在新班级不被看轻，陈屿想维持对周围一切不在意的状态"
  campus_setting: "高二（3）班教室，开学第一天上午"
  main_event: "林栀作为转学生被班主任介绍进班，被安排坐在陈屿后面"
  obstacle: "重点班按成绩说话，林栀转学资料里的数学分数被同学私下议论"
  action: "林栀在语文小测中稳住表现，陈屿第一次主动确认她的数学错题"
  cost: "林栀被贴上偏科标签，陈屿的关注被同桌周诚看见并调侃"
  emotion_progress: "两人第一次短暂目光接触，林栀感到陈屿冷淡但并非恶意"
  emotional_intensity: 3
  emotional_payoff: "女主被轻视后用语文成绩小小扳回一局，男主第一次只对她的错题停顿"
  character_growth: "林栀表现出对陌生环境的要强；陈屿表现出习惯性的不关注"
  conflict: "座位安排和偏科标签带来的班级压力，林栀需要借笔记又不愿示弱"
  relationship_turn: "陈屿从无视转为记住她的名字、强项和短板"
  irreversible_change: "林栀不再只是转学生，而成为陈屿视野里第一个矛盾具体的人"
  reader_reward: "重点班垫底标签与语文高分形成反差，读者开始期待她反击"
  viral_scene: "全班议论林栀数学分数时，陈屿把自己多印的一份笔记推到桌角"
  quotable_line_goal: "围绕'分数只能说明上一场考试，不说明这个人'打磨一句克制台词"
  opening_hook_options:
    - "匿名薄荷糖页码指向她第一道数学错题"
    - "陈屿草稿纸上被划掉的'她'字"
    - "转学生第一天暴露偏科短板，被安排在年级前三后座"
  beat_hardness: 4
  famous_scene_type: "第一次破例"
  famous_scene_payload: "全班私下议论林栀数学短板，陈屿把多印笔记推到桌角，被周诚看见并调侃，关系第一次被外部捕捉"
  payoff_type: "制造情绪债"
  payoff_debt: "匿名帮助和草稿纸秘密让读者追问陈屿为什么注意她"
  payoff_visibility: "读者能看见笔记和薄荷糖，但林栀还不知道是谁"
  character_tag_pressure: "林栀的要强与偏科被公开压力刺痛；陈屿的稳定冷淡第一次破例"
  rival_function: "周诚作为旁观者捕捉陈屿异常，制造公开压力"
  supporting_pressure: "班级议论和重点班成绩规则让林栀必须证明自己"
  style_freshness_notes: "少用光和窗收尾，用座位表、错题页码、薄荷糖纸推进"
  long_suspense_role: "埋设"
  long_suspense_clue: "草稿纸上被划掉的'她'字和匿名薄荷糖页码"
  foreshadowing_new:
    - "林栀看到陈屿桌上写了一半的数学题，草稿上有个'她'字被划掉"
    - "班主任把林栀叫到办公室时长谈"
  foreshadowing_recall: []
  ending_hook: "放学后林栀发现抽屉里不知谁放了一颗薄荷糖，糖纸背面写着她数学错题的页码"
  next_chapter_question: "是谁知道她那道题错在哪里，又为什么不署名？"
  forbidden_items:
    - "禁止让陈屿过于热情"
    - "禁止让林栀立刻引起全班注意"
    - "禁止出现明显暧昧对话"
  status: planned
- chapter_id: 2
  title: "同桌的距离"
  story_question: "林栀会不会发现薄荷糖页码的来源？"
  reader_hook: "匿名页码指向她错题，林栀必须判断这是善意还是嘲笑"
  character_desire: "林栀想自己解出错题，陈屿想把帮助控制在不被发现的范围"
  campus_setting: "高二（3）班教室，开学第二周"
  main_event: "语文小组合作，林栀和陈屿被分到同一组"
  obstacle: "周诚误以为薄荷糖是恶作剧并当众提起，林栀的自尊被刺到"
  action: "陈屿用小组讨论把话题从成绩转开，林栀在语文分析里反过来帮了他"
  cost: "两人互相帮忙的痕迹被同组同学看见，关系不再完全隐形"
  emotion_progress: "第一次正式对话，双方都保持距离但开始注意到对方的优点"
  emotional_intensity: 3
  emotional_payoff: "男主半公开解围，女主在自己的强项里反向救场"
  character_growth: "陈屿发现林栀语文确实很好；林栀发现陈屿并不像表面那么冷漠"
  conflict: "小组讨论中的意见分歧"
  relationship_turn: "两人从单方面观察转为第一次互相补位"
  irreversible_change: "林栀知道陈屿并非完全冷漠，陈屿知道她不愿被当成弱者"
  reader_reward: "特殊对待和能力互补被读者看见"
  viral_scene: "周诚调侃时，陈屿只说一句'题是我给的，有问题问我'"
  quotable_line_goal: "围绕'别把帮忙说成施舍'打磨一句台词"
  opening_hook_options: []
  beat_hardness: 4
  famous_scene_type: "公开维护"
  famous_scene_payload: "周诚当众提起薄荷糖页码，陈屿半公开承认题是自己给的，林栀既被维护也被迫面对自尊"
  payoff_type: "小甜点兑现"
  payoff_debt: "偿还上一章匿名帮助的部分疑问，同时制造便签新债"
  payoff_visibility: "全组同学都听见陈屿解围，读者能清楚看见特殊对待"
  character_tag_pressure: "林栀不愿被施舍，陈屿必须把帮助说成题目讨论"
  rival_function: "周诚代表更直接的公开表达，逼陈屿把暗中帮助搬到台面"
  supporting_pressure: "小组合作让互帮痕迹被第三方看见"
  style_freshness_notes: "用小组讨论、便签和语文书替代空镜收尾"
  long_suspense_role: "误导"
  long_suspense_clue: "陈屿拿到林栀半张便签，读者不知道他看到多少"
  foreshadowing_new:
    - "陈屿注意到林栀写字时喜欢咬笔"
  foreshadowing_recall: []
  ending_hook: "陈屿回家后翻开语文书，里面夹着林栀白天遗落的半张便签"
  next_chapter_question: "便签上没写完的那句话到底指向谁？"
  forbidden_items:
    - "禁止两人在小组合作中'默契十足'"
    - "禁止出现陈屿主动帮忙"
  status: planned
- chapter_id: 3
  title: "晚自习的灯"
  story_question: "便签上的话会不会暴露林栀转学真正原因？"
  reader_hook: "停电晚自习里，陈屿手里的半张便签即将被林栀发现"
  character_desire: "林栀想拿回便签不让人看见，陈屿想确认她是不是被原学校排挤过"
  campus_setting: "教室晚自习，第一次月考前一周"
  main_event: "晚自习停电，教室里点蜡烛继续学习，两人有了第一次自然交谈"
  obstacle: "黑暗和同学起哄让便签几乎被公开，林栀的情绪失控边缘"
  action: "陈屿把便签夹进自己的错题本替她挡下，林栀第一次主动问他为什么帮她"
  cost: "陈屿被同学误会藏了女生纸条，林栀欠下一个必须解释的人情"
  emotion_progress: "林栀发现陈屿知道很多她以为没人注意的小细节"
  emotional_intensity: 4
  emotional_payoff: "秘密差点曝光、男主维护、女主第一次主动追问"
  character_growth: "两人都在黑暗中比平时放松"
  conflict: "月考压力，林栀担心自己跟不上省重点的节奏"
  relationship_turn: "从互相补位转为共同守住一个秘密"
  irreversible_change: "林栀知道陈屿看见了她最不想被看见的脆弱"
  reader_reward: "暗中维护变成可见维护，关系张力升级"
  viral_scene: "烛光里陈屿把便签压进错题本，说'这页题，我还没讲完'"
  quotable_line_goal: "围绕'不是所有错题都要交给别人看'打磨一句台词"
  opening_hook_options: []
  beat_hardness: 5
  famous_scene_type: "秘密暴露"
  famous_scene_payload: "停电晚自习里便签险些被公开，陈屿用错题本替林栀挡下，同学误会他藏女生纸条"
  payoff_type: "痛点兑现"
  payoff_debt: "偿还便签差点曝光的紧张，同时欠下转学原因真相"
  payoff_visibility: "维护动作被同学误会，林栀和读者都能看见代价"
  character_tag_pressure: "林栀最怕被看见过去，陈屿最怕失控却主动承担误会"
  rival_function: ""
  supporting_pressure: "停电、起哄和月考压力让秘密暴露风险升级"
  style_freshness_notes: "用蜡烛、错题本、便签角、原学校校名形成本章专属物件链"
  long_suspense_role: "局部揭示"
  long_suspense_clue: "便签角露出林栀原学校校名"
  foreshadowing_new:
    - "陈屿说'你语文已经是全班最高了'，透露他知道她的成绩"
  foreshadowing_recall: []
  ending_hook: "来电后，林栀看见陈屿错题本露出的半张便签角，上面是她原学校的校名"
  next_chapter_question: "陈屿到底看到了多少，林栀会不会被迫说出转学原因？"
  forbidden_items:
    - "禁止因为黑暗产生暧昧"
    - "禁止两人对话超过10句"
  status: planned
"""

    def _mock_context(self, prompt: str) -> str:
        return """# 写作任务书 - 第 1 章

## 本章目标
建立两位主角的初次接触，同时让读者看见"重点班偏科转学生 × 伪装完美学霸"的核心矛盾。

## 本章读者入口钩子
林栀第一天被安排到陈屿后座，却立刻被同学议论数学短板；读者会想看她如何站稳。

## 本章故事问题
林栀能不能不被偏科标签压住？陈屿为什么会注意到她的错题？

## 本章校园场景
开学第一天的教室。窗外的梧桐树、黑板上的座位表、搬动的桌椅、走廊里的喧哗。

## 本章主事件
班主任介绍转学生林栀，安排她坐在陈屿后面；班级私下议论她数学分数，林栀用语文小测稳住第一天。

## 本章欲望-阻碍-行动-代价
- 主角欲望：林栀想在新班级不被看轻。
- 阻碍：重点班看重成绩，她的数学短板被同学议论。
- 行动：林栀稳住语文小测，陈屿把多印的数学笔记留在桌角。
- 代价：林栀被贴上偏科标签，陈屿的异常关注被周诚看见。

## 本章感情推进
从"完全的陌生人"到"对方身上出现一个具体问题"。不要过量暧昧，但要让陈屿的第一次停顿有情节意义。

## 本章情绪强度与兑现
- 情绪强度：3
- 兑现点：女主被轻视后小小扳回一局；男主只对她的错题停顿。

## 本章人物成长
- 林栀：在陌生环境中的第一份倔强
- 陈屿：表现出来的是不关注，但环境有了微小变化

## 本章关系转向
陈屿从"不关注新同学"转为记住她的名字、语文强项和数学短板。本章之后，林栀不再只是班里的转学生。

## 本章可传播名场面
全班议论林栀数学分数时，陈屿把多印的一份笔记推到桌角，没有解释。

## 本章事件硬度
- beat_hardness：4
- 本章硬度来自班级议论、偏科标签、陈屿异常关注被周诚看见。
- 不是纯观察章，因为林栀被迫面对公开压力，陈屿也付出被调侃的代价。

## 本章情绪兑现账本
- 制造情绪债：匿名薄荷糖页码和草稿纸秘密。
- 小甜点兑现：陈屿只对林栀的错题停顿并留下笔记。
- 读者能看见特殊对待，但女主暂时不知道来源。

## 本章人设标签压力
- 林栀的“要强偏科转学生”被数学短板和班级议论刺痛。
- 陈屿的“伪装稳定学霸”被第一次破例帮助打出裂缝。

## 本章配角/竞争者功能
周诚不是单纯调侃工具人，他作为更公开、更敏锐的旁观者，第一次捕捉陈屿的异常。

## 本章长线悬念任务
埋设：薄荷糖页码、草稿纸上被划掉的“她”、陈屿为什么知道她的错题。

## 本章风格避重
少用窗、光、风、沉默结尾；用座位表、错题页码、薄荷糖纸等具体物件推进。

## 本章金句/台词目标
打磨一句克制台词，表达"分数只能说明上一场考试，不说明这个人"。

## 本章必须回收的信息
无（第一章无前置信息）

## 本章禁止事项
- 禁止让陈屿过多关注林栀
- 禁止让林栀立刻引起注意或发生戏剧化事件
- 禁止出现任何明示或暗示的好感
- 禁止使用"命运的齿轮开始转动"类表达

## 本章语气风格
平静、克制、有温度但不过热。像初秋下午的阳光。

## 本章结尾钩子
放学后的薄荷糖——糖纸背面写着她数学错题的页码，不署名。

## 下一章追问
是谁知道她那道题错在哪里，又为什么不署名？
"""

    def _mock_write_chapter(self, prompt: str) -> str:
        return """# 第 1 章 新来的转学生

九月的阳光还算客气。

江城市一中的梧桐树还没开始落叶，教室里的风扇转得懒洋洋的。高二（3）班的门开着，走廊里断断续续有人经过，有人拖着凳子，有人在喊"谁来帮我看下这门怎么锁不上了"。

陈屿坐在第四排靠窗的位置，面前摊着一本数学竞赛题集。

他没有抬头。

倒不是看不起周围的喧闹。他只是习惯了——习惯耳边有声音，习惯旁边有人进进出出，习惯自己的世界和外面的世界之间隔着一层不厚不薄的玻璃。

开学第一天总是这样。

班主任老周还没进来，前排几个女生凑在一起聊暑假的综艺和某个明星的名字，后排男生在比较谁晒得更黑，中间几排的人则在赶似乎永远赶不完的暑假作业。

陈屿写完一道不等式证明，翻过一页。

他旁边空着一个位子。

准确地说，是他后面。

高二分班之后调整了座位。理科重点班的座位按成绩排，陈屿第三，所以坐在靠窗的黄金位置。他后面的位置原属于一个去了文科班的女生，现在空着。

据说今天会有转学生。

陈屿没有在意。转学生是转学生的事，他的事是眼前这本竞赛题。

门被推开了。

老周走进来，身后跟着一个人。

教室里的声音下意识收敛了一些——不是安静，是从沸水变成了热水。老周拍拍讲台："都回来了？先坐好，先坐好。"

大家稀稀拉拉地找到自己的位置。

陈屿这时候才抬头。

老周身后站着一个个子不高的女生，扎马尾，校服是新的，没什么褶皱，看起来像是还没来得及被洗旧的那种新。她两只手抓着书包肩带，嘴唇抿着，眼睛快速地扫了一圈教室，然后定在一个不远不近的地方。

那种眼神陈屿认得——努力让自己看起来不紧张的眼神。

"这是这学期转到我们班的林栀同学，"老周侧身指了指她，"之前在南京读书，因为父母工作调动来我们学校。大家欢迎。"

稀稀拉拉的掌声。

林栀微微鞠了一躬，幅度不大不小，有点像排练过的那种。

"大家好，我叫林栀，双木林，栀子花的栀。请大家多多关照。"

声音不算大，但咬字很清楚。像是语文成绩不会太差的人。

老周低头看了眼手里的座位表："你就坐陈屿后面那个位子吧——第四排靠窗，他后面。"

林栀顺着老周指的方向看过来。

陈屿正好也在看她。

大概一秒。

然后陈屿低下了头，继续看他的题目。

林栀背着书包走过来。那个空位子擦得不算干净——上一个主人走的时候大概没有好好清理，桌面上还有没撕干净的便签纸残骸。她把书包放下，抽出湿纸巾开始擦桌子。

很自然，很安静。

陈屿听见身后细小的声响，闻到一丝很淡的洗衣液的味道——不是香味，是那种"刚拆封"的感觉。

他在草稿纸上算完最后一步，写了答案。

又翻过一页。

---

第一天的课排得不重。语文课在第二节。教语文的孙老师四十多岁，讲课的时候喜欢用粉笔敲黑板，每敲一下前排就有人缩脖子。

"这学期我们开始讲古诗文鉴赏。先来一个小测验，看看大家暑假有没有看书。"

一片哀嚎。

孙老师没理会，开始念题目。无非是些默写和鉴赏的填空题，陈屿写得很快，写完就把笔放下了。

他听见后面传来笔尖划过纸面的声音，很快，很流畅。

等他听到第三题的时候，他忍不住微微侧了一下头——余光里看到林栀答题的手一直没有停，而且那个速度不是蒙的速度，是知道答案的速度。

语文不错。

孙老师收完卷子当场改。五分钟后她抬头："这次最高分是——林栀，96。"

全班有人回头。

林栀的脸红了一点，但坐得很直。她说"谢谢老师"的时候，声音和自我介绍时一样稳。

陈屿继续看他的数学题。

他没有觉得有什么特别的。只是经过一上午的听课之后，他发现自己记住了两件事：

第一，她的名字叫林栀。
第二，她语文确实很好。

仅此而已。

---

放学铃响的时候，教室像泄了气的球，人走得很快。陈屿收拾东西比较慢——他习惯等大部分人走了之后再起身，这样不用挤走廊。

林栀也还没走。她在整理笔记本，把新发的各科试卷按顺序夹好。

两人之间隔了大约四十秒的沉默。

然后林栀站起来，背上书包，从后门出去了。

陈屿过了一分钟也站起来。

走到门口的时候他顿了一下，回头看了眼自己的桌子。桌上那本竞赛题旁边，不知什么时候多了一颗薄荷糖。

透明包装纸，绿色糖粒。

他没吃糖的习惯。

陈屿拿着那颗糖看了一秒，随手放进抽屉里，走了。

教室里最后一个离开的人关了灯。

门外走廊已经空了。九月的梧桐树影子斜斜地铺在地上，有一阵风吹过来，带着食堂晚饭的味道。

那颗糖埋在抽屉最里面。

谁也不知道是谁放的。

甚至不知道是不是给谁的。

就像这个年纪的大多数事情一样——你看到了，你没问，它就在那里。

安静地等着被发现，或者被忘掉。

---

*（第 1 章完 · 约 2200 字）*
"""

    def _mock_review(self, prompt: str) -> str:
        return """# 第 1 章审查报告

## 总分
82 / 100

## 结论
本章可以保留，整体质量较好。校园氛围真实，人设稳定，感情推进克制。需要微调部分细节的真实性，但总体方向正确。

## 主要优点
1. 校园氛围真实——梧桐树、风扇、座位表、粉笔敲黑板等细节唤起真实的校园记忆
2. 男女主初次接触处理得当——克制，没有过度反应，保持了中长篇的节奏空间
3. 对话自然——"大家好，我叫林栀，双木林，栀子花的栀"符合转学生心态
4. 薄荷糖的钩子轻而有余味，符合青春校园的叙事节奏
5. 避免了"命运的齿轮开始转动"类AI腔表达
6. 陈屿的观察角度准确——有注意但不夸张，符合"表面冷淡观察力强"的人设

## 主要问题
1. 薄荷糖的出现可以再隐晦一些，目前仍有轻微"设计感"
2. 林栀在语文测验中的表现可以更自然地融入，而非直接通过分数宣告
3. 结尾旁白段落略显说教，"就像这个年纪的大多数事情一样"这句话可以删掉
4. 可以增加1-2处更多元的校园感官细节（如广播站的音乐、清洁工拖地等）

## 逐项评分
- 情感推进自然度：8/10
- 故事驱动力：6/10
- 欲望阻碍行动代价链：6/10
- 暧昧感：7/10（尚在初识阶段，暧昧尚未成立是正常的）
- 人设稳定：8/10
- 校园真实感：8/10
- 对话自然度：8/10
- AI 腔风险：中（结尾旁白段需修改）
- 霸总腔风险：低
- 感情过快风险：低
- 章节钩子：7/10
- 情绪兑现：6/10
- 可传播名场面：5/10
- 废氛围风险：中

## 必须修改
1. 删除或重写结尾旁白段落（"就像这个年纪的大多数事情一样..."），避免说教感
2. 薄荷糖的出现方式改为更隐晦的描述

## 可选优化
1. 增加校园声音层次（广播、铃声、风吹树叶声等）
2. 可增加1个其他同学的细节，让班级更有群像感

## 下一章建议
1. 第二章应引入第一次正面交流，但仍保持距离感
2. 可以开始通过细节暗示陈屿注意到林栀的某些小习惯
3. 避免让"薄荷糖"快速被揭示来源
"""

    def _mock_data_extract(self, prompt: str) -> str:
        return json.dumps({
            "chapter_summary": {
                "chapter_id": 1,
                "title": "新来的转学生",
                "summary": "高二开学第一天，转学生林栀来到江城市一中高二（3）班，被安排坐在陈屿后面。林栀在语文小测中获得最高分。放学后陈屿发现桌上多了一颗薄荷糖，来源不明。",
                "key_events": [
                    "林栀作为转学生进入高二（3）班",
                    "林栀被安排坐在陈屿后面的位置",
                    "语文课小测林栀获得96分最高分",
                    "放学后陈屿发现桌上出现不明来源的薄荷糖"
                ],
                "relationship_change": "两人从完全陌生到出现在对方视野中，陈屿记住了林栀的名字和语文成绩",
                "word_count": 2200,
                "date_in_story": "高二上学期9月1日"
            },
            "events": [
                {
                    "event_id": "evt_001_001",
                    "chapter_id": 1,
                    "event_type": "转学入学",
                    "description": "林栀转学到江城市一中高二（3）班",
                    "characters_involved": ["林栀", "老周"],
                    "timestamp_story": "高二上学期9月1日上午",
                    "relationship_impact": "引入女主",
                    "tags": ["入学", "转学", "初次登场"]
                },
                {
                    "event_id": "evt_001_002",
                    "chapter_id": 1,
                    "event_type": "座位安排",
                    "description": "林栀被安排坐在陈屿后面",
                    "characters_involved": ["林栀", "陈屿"],
                    "timestamp_story": "高二上学期9月1日上午",
                    "relationship_impact": "两人第一次在物理空间中接近",
                    "tags": ["座位", "第一次接触"]
                },
                {
                    "event_id": "evt_001_003",
                    "chapter_id": 1,
                    "event_type": "学业表现",
                    "description": "语文小测林栀获得96分全班最高",
                    "characters_involved": ["林栀", "孙老师"],
                    "timestamp_story": "高二上学期9月1日上午",
                    "relationship_impact": "陈屿注意到林栀的语文能力",
                    "tags": ["语文", "测验", "学霸"]
                },
                {
                    "event_id": "evt_001_004",
                    "chapter_id": 1,
                    "event_type": "微小事件",
                    "description": "陈屿放学后发现桌上有一棵薄荷糖，来源不明",
                    "characters_involved": ["陈屿"],
                    "timestamp_story": "高二上学期9月1日下午放学后",
                    "relationship_impact": "埋下悬念",
                    "tags": ["薄荷糖", "悬念", "伏笔"]
                }
            ],
            "foreshadowing": [
                {
                    "foreshadow_id": "fore_001",
                    "description": "陈屿桌上一颗不明来源的薄荷糖",
                    "planted_chapter": 1,
                    "intended_payoff_chapter": None,
                    "status": "active",
                    "related_characters": ["陈屿"],
                    "importance": "medium",
                    "notes": "可能是林栀放的，也可能是别人；目前不可确定"
                }
            ],
            "relationship_update": {
                "relationship_stage": "初识",
                "stage_description": "两人刚成为前后座，处于完全陌生的状态",
                "romance_level": 3,
                "trust_level": 5,
                "conflict_level": 5,
                "male_to_female": {
                    "current_attitude": "注意到后座新同学安静努力",
                    "hidden_feeling": "没有特别感受，纯粹关注",
                    "recent_change": "记住了她的名字和语文成绩"
                },
                "female_to_male": {
                    "current_attitude": "觉得前座男生冷淡但并非恶意",
                    "hidden_feeling": "隐约觉得他其实并非完全不关心周围",
                    "recent_change": "注意到陈屿在做很难的数学题"
                },
                "unresolved_tensions": [],
                "forbidden_progress": [
                    "当前阶段禁止直接表白",
                    "当前阶段禁止暧昧身体接触",
                    "当前阶段禁止一方突然过度关注另一方"
                ]
            }
        }, ensure_ascii=False, indent=2)

    def _mock_revise(self, prompt: str) -> str:
        return """# 第 1 章修改计划

## 基于审查报告的修改方案

### 必须修改

#### 1. 删除结尾旁白段落
- **位置**：第 1 章最后两段
- **问题**：说教感太强，"就像这个年纪的大多数事情一样"是 AI 腔典型表达
- **修改方案**：
  - 删除"就像这个年纪的大多数事情一样——你看到了，你没问，它就在那里。安静地等着被发现，或者被忘掉。"
  - 改为以场景收尾，不做总结
  - 建议替代：直接以"门关了。走廊空了。"结束，让场景本身说话

#### 2. 薄荷糖出现方式调整
- **位置**：陈屿发现薄荷糖的段落
- **问题**：设计感稍重
- **修改方案**：
  - 不要让陈屿"拿着糖看了一秒"，改为更不经意的发现
  - 不要写"透明包装纸，绿色糖粒"，改为更模糊的描述
  - 让糖的出现更像一个不经意的细节而非精心设计的暗示

### 可选优化

#### 3. 增加校园声音层次
- 可以在早读或课间增加广播站播放的音乐
- 午休时可以提及教室外清洁工拖地的声音
- 增加风吹梧桐叶的细微声音

#### 4. 增加群像细节
- 可以在班级场景中增加1个有名字的配角
- 这个配角不需要被记住，只是让班级感觉不像"只有主角"

### 不建议修改
- 陈屿和林栀的人设表现稳定，不用调整
- 对话内容和数量合适
- 整体叙事节奏良好

### 预计修改后得分
修改后可达到 86-88 分区间。
"""

    def _mock_query(self, prompt: str) -> str:
        return """## 关键词搜索结果："林栀"

### characters/protagonist_female.yaml
匹配类型：角色设定
片段：name: "林栀" - 语文好、敏感要强、转学生

### story/premise.md
匹配类型：故事梗概
片段：女主林栀，语文好、敏感要强，转学到江城市一中...

### memory/chapter_summaries.json
匹配类型：章节摘要
片段：第1章 - 林栀转入高二（3）班，坐在陈屿后面

### 共找到 3 个匹配结果。
"""

    def _mock_consistency_check(self, prompt: str) -> str:
        return """```json
[
  {
    "issue_id": "llm_001",
    "issue_type": "character_inconsistency",
    "severity": "low",
    "description": "第2章中男主对话略多，与设定中'话少'的特征有轻微偏差",
    "chapter_id_a": 1,
    "chapter_id_b": 2,
    "evidence_a": "第1章男主对话约3句",
    "evidence_b": "第2章男主对话约8句",
    "suggestion": "检查第2章是否有合理的剧情原因导致男主话变多，如无则建议减少对话量"
  }
]
```"""

    def _mock_reality_check(self, prompt: str) -> str:
        return """# 现实逻辑审核报告

## 发现的问题

### 🔴 严重（与事实明确矛盾，必须修改）

**无。**

### 🟡 建议（不太合理但非硬伤，推荐修改）

- **【时代技术】** 检查是否有旧式功能机等不符合时代的设备描述。2017年高中生使用的应是红米、OPPO、vivo等千元以内的智能手机。
- **【教育制度】** 确认所有考试和竞赛的名称都是真实存在的（如"省作文竞赛"而非"语文竞赛"）。

### 🔵 提醒（合理但可以更精准）

- **【日常细节】** 确认校门口小吃、饮品店等细节符合四川县城高中的实际情况。

## 已确认合理

- 小县城到成都2-3小时大巴 ✓
- 2017年四川高考为旧高考模式 ✓
- 高一暑假组建重点班的操作符合实际 ✓
- 5:30跑操、周末双休、走读制符合四川县城高中实际 ✓

## 无问题维度

- 地理交通：未发现异常
- 社会经济：未发现异常
- 制度流程：未发现异常

## 总结

- 严重问题 0 处
- 建议修改 0 处（以上为通用提醒）
- 提醒 1 处
- 总体现实逻辑可信度：高

内容在现实逻辑层面整体可靠，未发现与事实明确矛盾之处。"""

    def _mock_outline_update(self, prompt: str) -> str:
        return """```yaml
chapters:
  - chapter_id: 4
    title: "调整后的第4章"
    campus_setting: "教室晚自习 + 走廊偶遇"
    main_event: "基于前三章实际推进速度，增加一次自然的课后交流"
    emotion_progress: "在晚自习后偶遇中，让两人发现一个共同的兴趣或习惯"
    character_growth: "林栀开始不那么防备；陈屿第一次主动多说了一句话"
    conflict: "月考成绩公布带来的压力"
    foreshadowing_new: ["陈屿注意到林栀每晚都会多留20分钟做语文题"]
    foreshadowing_recall: []
    ending_hook: "林栀发现陈屿也留到了最后"
    forbidden_items:
      - "禁止陈屿明显表现出关心"
      - "禁止两人对话超过5句"
    status: "planned"
    update_reason: "根据前三章反馈，原大纲第4章的感情推进节奏偏快，调整为更克制的课后偶遇"
```
"""

    def _mock_chapter_stats(self, prompt: str) -> str:
        return self._mock_general(prompt)


class OpenAICompatibleClient(LLMClient):
    """兼容 OpenAI 接口格式的 LLM 客户端，支持自动重试和 Token 预算检查。"""

    # DeepSeek 模型上下文窗口约 64K tokens，预留安全边界
    MAX_PROMPT_TOKENS = 50000
    MAX_RETRIES = 3
    RETRY_BACKOFF_BASE = 2.0  # 秒
    RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
    INVALID_CONTENT_MARKERS = [
        "Response ended prematurely",
        "response ended prematurely",
        "prematurely",
        "maximum context length",
        "context length exceeded",
        "The response was filtered",
    ]
    MIN_RESPONSE_CHARS = {
        "write_chapter": 1200,
        "context": 500,
        "review": 300,
        "revise": 300,
        "book_outline": 800,
        "chapter_outline": 800,
        "setting": 800,
        "data_extract": 100,
        "consistency_check": 100,
        "reality_check": 200,
        "outline_update": 300,
    }

    def __init__(self, config: ModelConfig):
        self.config = config
        self.base_url = config.base_url.rstrip("/")
        self.api_key = config.api_key
        self.model = config.model
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens

        # Token 预算：优先使用配置文件中的值，否则使用默认 50000
        self.max_prompt_tokens = getattr(config, 'max_prompt_tokens', None) or 50000
        # HTTP read 超时(秒): 死连接(无字节)由 socket 层在此触发 requests.Timeout,
        # 不依赖信号/外部 kill。子进程会按本次调用的 hard_timeout 收紧这个值。
        self.read_timeout = getattr(config, 'read_timeout', None) or 600

    def _estimate_tokens(self, text: str) -> int:
        from .context_budget import estimate_tokens
        return estimate_tokens(text)

    def _check_token_budget(self, prompt: str) -> tuple:
        """检查 Prompt 是否超出 Token 预算。返回 (is_ok, estimated_tokens, warning)。"""
        estimated = self._estimate_tokens(prompt)
        warning = ""
        is_ok = True
        if estimated > self.max_prompt_tokens:
            warning = (
                f"[Token 预算警告] 预估 Prompt Token 数 ({estimated}) 超过安全阈值 "
                f"({self.max_prompt_tokens})。生成可能不稳定或输出截断。"
            )
            is_ok = False
        return is_ok, estimated, warning

    def _fit_prompt_to_budget(self, prompt: str) -> tuple[str, int, bool]:
        """检查完整输入；超限时拒绝请求，保留原文与规则。"""
        estimated = self._estimate_tokens(prompt)
        if estimated <= self.max_prompt_tokens:
            return prompt, estimated, False
        raise ValueError(
            f"完整 Prompt 超出输入预算: {estimated}/{self.max_prompt_tokens} tokens；"
            "请使用足够容量的模型并调整预算，或由工作流按完整段落安排输入；不会截断正文或规则。"
        )

    def _validate_completion(self, data: dict, content: str, task_type: str) -> tuple[bool, str]:
        """校验 API 200 响应是否真的完整可用。"""
        choices = data.get("choices") or []
        choice = choices[0] if choices else {}
        finish_reason = choice.get("finish_reason")
        if finish_reason and finish_reason != "stop":
            return False, f"finish_reason={finish_reason}，响应可能被截断或被过滤"

        stripped = (content or "").strip()
        if not stripped:
            return False, "响应内容为空"

        for marker in self.INVALID_CONTENT_MARKERS:
            if marker in stripped:
                return False, f"响应包含截断/异常标记: {marker}"

        min_chars = self.MIN_RESPONSE_CHARS.get(task_type, 20)
        if len(stripped) < min_chars:
            return False, f"响应过短: {len(stripped)} 字符，低于 {min_chars} 字符"

        return True, ""

    def _should_retry(self, status_code: int, attempt: int) -> bool:
        """判断是否应该重试。"""
        if attempt >= self.MAX_RETRIES:
            return False
        return status_code in self.RETRYABLE_STATUSES

    def _retry_delay(self, attempt: int) -> float:
        """计算指数退避延迟（含随机抖动）。"""
        import random
        base = self.RETRY_BACKOFF_BASE ** (attempt + 1)
        jitter = random.uniform(0, base * 0.5)
        return base + jitter

    def _retry_after_seconds(self, resp) -> float | None:
        """从 429/503 响应里解析 Retry-After(秒)。

        服务端明确告诉我们多久后再试时, 必须尊重它, 而不是用固定退避
        硬怼(问题: Rate Limit 不感知)。支持"秒数"格式; 上限 120s 防异常值。
        """
        try:
            raw = resp.headers.get("Retry-After") or resp.headers.get("retry-after")
        except Exception:
            raw = None
        if not raw:
            return None
        try:
            secs = float(str(raw).strip())
            if secs < 0:
                return None
            return min(secs, 120.0)
        except (ValueError, TypeError):
            # HTTP-date 格式较少见, 这里不解析, 退回指数退避。
            return None

    def preflight(self) -> dict:
        """用最小请求验证 API Key、模型名和连通性。"""
        import requests

        if not self.api_key:
            return {"ok": False, "error": "未设置 API Key"}

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0,
            "max_tokens": 8,
        }
        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=30,
            )
            if resp.status_code != 200:
                return {"ok": False, "status_code": resp.status_code, "error": resp.text[:300]}
            data = resp.json()
            choice = (data.get("choices") or [{}])[0]
            content = (choice.get("message") or {}).get("content", "")
            return {
                "ok": True,
                "configured_model": self.model,
                "actual_model": data.get("model", ""),
                "finish_reason": choice.get("finish_reason"),
                "sample": content[:50],
            }
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def generate(self, prompt: str, task_type: str = "general") -> str:
        import requests
        import time as time_module

        # 每次调用重置真实用量(供 harness 读取真实 token/成本, 而非字符估算)
        self.last_usage = {}
        self.last_actual_model = ""

        if not self.api_key:
            return "[错误] 未设置 API Key。请在 config/model.yaml 中设置 api_key，或设置环境变量 LLM_API_KEY。"

        # API transport must preserve the complete manuscript and research rules.
        prompt, estimated_tokens, _ = self._fit_prompt_to_budget(prompt)

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": (
                    "你是一名专业的中文短篇小说作者与编辑。尊重简报中的题材、篇幅、叙事引擎和锁定事实；"
                    "让人物选择推动因果，删除无作用细节，完整兑现本篇承诺。"
                    if task_type in {"story_candidates", "opening_candidates", "story_blueprint", "story_blueprint_repair", "story_plan", "story_plan_repair",
                                     "blueprint_audit", "serial_plan",
                                     "write_micro_story", "write_short_story", "write_story_scene", "repair_story_length",
                                     "story_edit", "cold_read_review", "continuity_review", "story_fact_review",
                                     "blind_read_evaluation", "causal_challenge", "channel_metadata", "carousel_adapt", "carousel_review"}
                    else "你是一名专业的中文小说作者与编辑，按当前任务执行策划、写作或审读，并遵守作者的篇幅和事实要求。")},
                {"role": "user", "content": prompt},
            ],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        json_tasks = {"story_candidates", "opening_candidates", "story_blueprint", "story_blueprint_repair", "story_plan", "story_plan_repair",
                      "blueprint_audit", "serial_plan",
                      "story_edit", "cold_read_review", "continuity_review", "story_fact_review",
                      "blind_read_evaluation", "causal_challenge", "channel_metadata", "carousel_adapt", "carousel_review"}
        if self.model.startswith("deepseek-") and task_type in json_tasks:
            payload["response_format"] = {"type": "json_object"}
        if self.model.startswith("deepseek-") and self.config.thinking in ("enabled", "disabled"):
            payload["thinking"] = {"type": self.config.thinking}

        last_error = None
        for attempt in range(self.MAX_RETRIES + 1):
            try:
                # connect=30s (TCP), read=600s (response) — 防止凌晨 API TCP 挂死
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=(30, self.read_timeout),
                )

                if resp.status_code == 200:
                    try:
                        data = resp.json()
                        content = data["choices"][0]["message"]["content"]
                        finish_reason = data["choices"][0].get("finish_reason", "")
                        # 捕获真实用量与服务端实际模型名
                        self.last_usage = data.get("usage") or {}
                        self.last_actual_model = data.get("model", "") or ""
                    except (ValueError, KeyError, IndexError, TypeError) as parse_error:
                        last_error = f"响应解析失败: {parse_error}"
                        if attempt < self.MAX_RETRIES:
                            delay = self._retry_delay(attempt)
                            print(f"  [响应异常] {last_error}")
                            print(f"  [重试] 第 {attempt + 1}/{self.MAX_RETRIES} 次重试，等待 {delay:.1f}s...")
                            time_module.sleep(delay)
                            continue
                        return f"[API 调用失败] {last_error}，已重试 {self.MAX_RETRIES} 次"

                    # 自动扩容: finish_reason="length" 表示 max_tokens 被 reasoning 吃光了
                    if finish_reason == "length" and self.max_tokens < 32000:
                        new_max = min(self.max_tokens * 2, 32000)
                        print(f"  [自动扩容] max_tokens {self.max_tokens}→{new_max}（reasoning 消耗过大，输出被截断）")
                        self.max_tokens = new_max
                        payload["max_tokens"] = new_max
                        continue  # Retry with larger token budget

                    valid, invalid_reason = self._validate_completion(data, content, task_type)
                    if valid:
                        if attempt > 0:
                            print(f"  [重试成功] 第 {attempt} 次重试后成功。")
                        return content

                    last_error = f"响应不完整: {invalid_reason}"
                    if attempt < self.MAX_RETRIES:
                        delay = self._retry_delay(attempt)
                        print(f"  [响应异常] {last_error}")
                        print(f"  [重试] 第 {attempt + 1}/{self.MAX_RETRIES} 次重试，等待 {delay:.1f}s...")
                        time_module.sleep(delay)
                        continue
                    return f"[API 调用失败] {last_error}，已重试 {self.MAX_RETRIES} 次"

                # 非 200 状态码
                error_msg = f"HTTP {resp.status_code}: {resp.text[:200]}"
                last_error = error_msg

                if self._should_retry(resp.status_code, attempt):
                    # 限流(429)/过载(503): 优先尊重服务端 Retry-After
                    delay = self._retry_delay(attempt)
                    if resp.status_code in (429, 503):
                        retry_after = self._retry_after_seconds(resp)
                        if retry_after is not None:
                            delay = max(delay, retry_after)
                            print(f"  [限流] 服务端要求 Retry-After={retry_after:.1f}s，遵从。")
                    print(f"  [API 错误] {error_msg}")
                    print(f"  [重试] 第 {attempt + 1}/{self.MAX_RETRIES} 次重试，等待 {delay:.1f}s...")
                    time_module.sleep(delay)
                    continue
                else:
                    return f"[API 调用失败] {error_msg}（不重试，状态码 {resp.status_code}）"

            except requests.exceptions.Timeout:
                last_error = "请求超时（600s）"
                if attempt < self.MAX_RETRIES:
                    delay = self._retry_delay(attempt)
                    print(f"  [超时] 第 {attempt + 1}/{self.MAX_RETRIES} 次重试，等待 {delay:.1f}s...")
                    time_module.sleep(delay)
                    continue
                return f"[API 调用失败] {last_error}，已重试 {self.MAX_RETRIES} 次"

            except requests.exceptions.ConnectionError as e:
                last_error = f"连接错误: {e}"
                if attempt < self.MAX_RETRIES:
                    delay = self._retry_delay(attempt)
                    print(f"  [连接错误] 第 {attempt + 1}/{self.MAX_RETRIES} 次重试，等待 {delay:.1f}s...")
                    time_module.sleep(delay)
                    continue
                return f"[API 调用失败] {last_error}，已重试 {self.MAX_RETRIES} 次"

            except requests.exceptions.RequestException as e:
                return f"[API 调用失败] {e}"

        return f"[API 调用失败] {last_error}，已重试 {self.MAX_RETRIES} 次"


def create_llm_client(config: ModelConfig) -> LLMClient:
    """工厂函数：根据配置创建 LLM 客户端。"""
    if config.provider == "mock":
        return MockLLMClient()
    elif config.provider == "openai_compatible":
        return OpenAICompatibleClient(config)
    else:
        raise ValueError(f"未知的 LLM provider: {config.provider}。支持: mock, openai_compatible")

