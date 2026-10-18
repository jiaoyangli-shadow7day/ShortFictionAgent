"""Expression coaching derived from local audits, not copied popular prose.

These small examples demonstrate an editorial mechanism. They are explicitly
authored controls, not source quotations or evidence of reader performance.
"""
from __future__ import annotations

PROTOCOL = 'narrative-expression-2026-10-09-v2'
AUDIT_SOURCE = 'docs/architecture.md'
RULES = [
    '先按本场人物欲望写行动、对方接招及随之改变的选择，再在内部核对事实；不要把核对过程写进小说。',
    '读者理解当前选择必需的信息要清楚交代；已知信息只有改变含义、产生新后果或被人物利用时才重提。事实表正确不要求正文逐条解释。',
    '工作细节、物件和日常照顾都可以写；选择能让读者经历试探、判断、失误、回应或亲近的细节，略去已经没有新作用的操作步骤。',
    '用人物当前会说的话表达要求、抗拒、偏爱与决定；性格分析和主题总结不能代替现场接招，也不要机械禁止所有心理描写或专业词。',
    '同一要求被再次提出时，检查双方已知信息、筹码或关系是否变化；重复一次成功办法而没有新后果，不因换了客人、地点或日期就成为新戏。',
    '步骤有实际作用，不等于值得逐步详写。若把准备、操作和实测压缩成一句事实，人物的选择、理解和后续期待基本不变，就缩写这些步骤；不能用段尾一次温情为前面的大段操作补证。保留真正发生接招、偏爱、责任转移或选择变化的部分。',
    '安静、温柔、舒适和稳定关系可以有效推进；保留使人理解人物或期待下一次相处的细节，不强制每段反转、吵架、吻戏或灾难。',
    '结构返修先合并无效重复并重排人物选择，局部事实修补放在原来起作用的位置；不要在段末追加一串免责、账目或审稿结论。',
]

EXAMPLES = [
    {
        'id': 'explanation_to_response', 'family': 'relationship_repair',
        'weak': '我意识到自己一直用防御机制回避亲密关系，于是决定进行坦诚沟通，建立共同约定。',
        'stronger': '我说完“普通朋友”，她把给我留的那杯热奶拿了回去。\n“那就不麻烦你送了。”\n她拎包走向门口。我赶过去，先把刚才那句话认了：“我怕他们起哄，让你难堪。结果让你更难堪了。”\n她停了下来，没把杯子递回来：“这次你自己跟他们说。”',
        'mechanism': '把性格诊断换成失言、对方收回照顾、补救及新的要求；后续需要真正行动。道歉不自动消除后果。',
        'limit': '这是关系修复示范，不要求每篇主角认错；心理描写有独立信息时应保留。',
    },
    {
        'id': 'quiet_choice', 'family': 'relationship',
        'weak': '我检查了早餐的温度和包装，核对班车时间，确认她已经感受到我的体贴。',
        'stronger': '她把包子塞进我外套口袋。\n“车还有五分钟。”\n“你怎么知道我今天几点走？”\n“你说了三遍。”\n我摸到口袋里的热气，没再拿桌上的钥匙：“下周日，你还在家吗？”\n她低头系袋口：“你先回来再说。”',
        'mechanism': '照顾的细节让人物发现对方一直在听，并第一次主动约下一次；温柔回应也能改变相处。',
        'limit': '保留含蓄与生活质感，不把所有温情强行改为讨价还价；不得照搬包子、班车或台词。',
    },
    {
        'id': 'professional_stakes', 'family': 'professional',
        'weak': '我完成资料核验，进行了权限分配，团队执行通过，领导认可了我的能力。',
        'stronger': '经理把合同推给我：“你跟的客户，你签。”\n我翻到最后一页，把空着的负责人一栏转向他：“您先填这里，我就签。”\n他伸手来拿合同。客户却按住了纸：“刚才是谁答应我的交期？”\n经理看了我一眼，没再叫我签字。',
        'mechanism': '专业动作成为责任转移与反制，客户的回应改变双方下一步；原理和手续随交锋被读懂。',
        'limit': '这段只展示一次局部变化，不证明主角已赢得客户或事业成功；职业细节必须符合本篇事实。',
    },
]


def writing_guidance(contract: dict) -> dict:
    """Select bounded coaching from positive author inputs, never avoid lists."""
    genre = contract.get('genre') or {}
    research = contract.get('research_preferences') or {}
    positive = ' '.join(str(value) for value in (
        contract.get('reader_promise', ''), contract.get('story_engine', ''),
        genre.get('primary', ''), genre.get('custom_description', ''),
        (contract.get('author_input') or {}).get('premise', ''),
        *(research.get('reader_tags') or [])))
    relationship = genre.get('primary') == 'romance' or any(
        term in positive for term in ('甜宠', '恋爱', '爱情', '亲密', '情侣', 'relationship_choice'))
    professional = any(term in positive for term in ('职场', '职业', '经营', '会所', '客户', '公司'))
    chosen = []
    if relationship:
        chosen += [row for row in EXAMPLES if row['family'] == 'relationship']
        if any(term in positive for term in ('失言', '认错', '误会', '隐瞒')):
            chosen += [row for row in EXAMPLES if row['family'] == 'relationship_repair']
    if professional:
        chosen += [row for row in EXAMPLES if row['family'] == 'professional']
    return {'protocol': PROTOCOL, 'source': AUDIT_SOURCE, 'rules': list(RULES),
            'examples': chosen[:2],
            'evidence_scope': '依据本地稿件审计编写的表达教学对照；stronger由编辑原创，不是热门作品原文、读者试验或平台数据。',
            'transfer_limit': '只迁移信息在行动与回应中产生作用的机制，不复制示范的人物、道具、情节或台词；服从作者指定题材。'}
