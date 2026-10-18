"""Keep the original readership separate from the planner's interpretation.

This is an input projection and a curated editorial reference, not a retention
classifier. Cold reading gets no future plan, private constraints or ending.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .contracts import digest

PROTOCOL = 'target-reader-v2-explicit-promise-payoffs'
LIBRARY_PATH = Path(__file__).with_name('data') / 'target_reader_research.json'

COMMON = (
    'original_entry_context/target_reader_context来自原始合同，不是方案对自己的解释。'
    'enabled=true时按番茄具体题材与受众判断；其他渠道遵循自身定位，不套男频模板。'
    '受众想看的体验由题材、标题、实际导语和原始期待共同确定，不能只凭“番茄读者”标签猜测。'
    '读者要容易看懂谁想得到什么，并愿意亲历争取、接招、变化和回报；'
    '有新信息、人物更成熟或完成节点，不自动等于值得阅读。'
    '温情、尊重和日常可通过具体偏爱、反差、秘密或艰难选择产生吸引力；'
    '不统一添加恶毒反派、羞辱、危机或固定间隔钩子。'
)
PLAN_INSTRUCTION = COMMON + (
    '先按原始期待设计读者想亲眼看到的具体戏，再组织main_question与core_reading_experience；'
    '两者不能自行把设定收益、逆袭或恋爱欲望换成正确道理。'
    '逐项落实原始audience.expectations，并把同一项内不同能力与收益分别规划；'
    '不能只选较弱的设定事实，绕开更强的读者期待。先确定入口想得到的收益，'
    '不能从已写出的限制或成熟主题倒推读者只需要这些。一次收益是否足够须由原始承诺决定，'
    '“有限”“第一次”本身不表示只承诺一次；边界、拒用和承认未知不替代能力实际带来的收益。'
    '若原始入口承诺后续优势，而移除余下能力后大部分后续赢法、所得和可选行动不变，'
    '须规划实际用途、代价和后果，或按原始入口重构；不能用解释成长或润色关闭缺口。'
    '明确的一次性机会可凭充分收益及后果完成，不强迫反复使用或用满次数。'
    '人物成长应发生在争取与接招的结果中；不要先列年份和阶段，再填同功能的犯错、沟通与认可。'
)
WRITE_INSTRUCTION = COMMON + (
    '本场按原始读者期待写出值得亲历的动作、对方回应、所得及后果，不能只忠实完成偏移的大纲。'
    '让能力、反击、主动偏爱在现场产生作用；用清楚的对白和具体细节呈现，'
    '少用解释意义、协商原则和宣布结果代替关键过程。'
    '承接前文已兑现的结果，不把同一种教育或已经确认的感情换场景再演一遍。'
    '保留合同事实与规则；根本结构偏移交现有章审退回规划，不私自改能力、恢复旧候选或另造结局。'
)
REVIEW_INSTRUCTION = COMMON + (
    '始终对照原始受众与实际入口，独立判断当前大纲或正文有没有换掉主要吸引点；'
    '不能因作者已写成另一种主题而逐渐改用该主题的标准，也不能用最终圆满补偿中段空档。'
    '指出读者此刻想亲眼看到谁做什么、已经得到哪种具体满足、仍在等待什么；'
    '在现有reason/text及证据中判断，不新增计数评分。'
    '区分新事实与新体验：阶段名、身份或地点变化，若仍重复相同欲望、接招、解决方式，'
    '须比较连读负担；能力的限制和退出不能替代它应有的收益。'
    '先从原始入口逐项确定想得到的收益，不能从已写出的限制或成熟主题倒推读者只需要这些。'
    '一次收益是否足够须按原始承诺判断，“有限”“第一次”不自动证明只承诺一次。'
    '边界、拒用和承认未知本身不能充当能力实际收益；若原始入口还承诺后续优势，'
    '移除余下能力后大部分后续赢法、所得和可选行动不变，须退回规划实际用途、代价和后果，'
    '或按原始入口重构，不能只润色或解释为成长。明确的一次性机会可以充分兑现，'
    '不要求反复使用或用满次数；具体收益是否匹配原始承诺仍须据当前行动和后果判断。'
    '关键阅读承诺被替换或长期退出属于结构问题，不能只建议润色或压短。'
    '只有具体场面确实建立新的趣味、心动或回报，才保留安静展开；不为更严而强造缺点。'
)


def entry_context(contract: dict, *, include_author_promise: bool = False) -> dict:
    """Project only the original entry; never accept planner/reviewer additions."""
    audience = contract.get('audience') or {}
    if isinstance(audience, dict):
        description = audience.get('description', '')
        expectations = audience.get('expectations') or []
    else:
        description, expectations = audience if isinstance(audience, str) else '', []
    genre = contract.get('genre') or {}
    if isinstance(genre, dict):
        genre = {key: deepcopy(genre[key]) for key in
                 ('primary', 'secondary', 'custom_description') if key in genre}
    elif not isinstance(genre, str):
        genre = ''
    author = contract.get('author_input') or {}
    facts = (author.get('entry_facts') or []) if isinstance(author, dict) else []
    result = {
        'enabled': 'fanqie_short' in (contract.get('channels') or []),
        'title': contract.get('title', ''),
        'audience': description if isinstance(description, str) else '',
        'audience_expectations': [x for x in expectations if isinstance(x, str)]
                                if isinstance(expectations, list) else [],
        'genre': genre,
        'tone': contract.get('tone', '') if isinstance(contract.get('tone', ''), str) else '',
        'point_of_view': contract.get('point_of_view', ''),
        'entry_facts': [x for x in facts if isinstance(x, str)] if isinstance(facts, list) else [],
    }
    if include_author_promise:
        promise = contract.get('reader_promise', '')
        result['reader_promise'] = promise if isinstance(promise, str) else ''
    return result


def library() -> dict:
    return json.loads(LIBRARY_PATH.read_text(encoding='utf-8'))


def criteria_hash() -> str:
    return digest({'protocol': PROTOCOL, 'plan': PLAN_INSTRUCTION,
                   'write': WRITE_INSTRUCTION, 'review': REVIEW_INSTRUCTION,
                   'library': library()})


def research_context(contract: dict, stage: str) -> dict:
    """Retrieve by the positive brief, never by forbidden plots or old reviews."""
    data = library()
    context = entry_context(contract, include_author_promise=True)
    result = {'enabled': context['enabled'], 'version': data['version'],
              'scope': data['scope'], 'source': data['source'], 'stage': stage,
              'selection': 'positive_original_entry_only', 'mechanisms': []}
    if not context['enabled']:
        return result
    # Research selection may use the author's positive premise. It is never
    # copied into the cold-reader projection or drawn from exclusions/reviews.
    author = contract.get('author_input') or {}
    premise = author.get('premise', '') if isinstance(author, dict) else ''
    positive = json.dumps({'entry': context,
                           'premise': premise if isinstance(premise, str) else ''},
                          ensure_ascii=False)
    ranked = [(sum(term in positive for term in row['match_terms']), row)
              for row in data['mechanisms']]
    ranked.sort(key=lambda item: (-item[0], item[1]['id']))
    result['mechanisms'] = [{key: deepcopy(row[key]) for key in
        ('id', 'reader_desire', 'realization', 'failure_mode', 'sources', 'scope_limit')}
        for score, row in ranked if score][:data['selection_limit']]
    return result
