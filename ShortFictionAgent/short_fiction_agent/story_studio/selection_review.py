"""Editorial candidate comparison, bound to the complete planning input."""
import re

from .contracts import StoryError, digest
from .research_guidance import guidance_fingerprint


def candidate_contract_violations(contract: dict, candidate: dict) -> list[str]:
    """Catch an explicit external-cash boundary that a model reviewer may miss."""
    author = contract.get('author_input') or {}
    rules = str(author.get('premise') or '') + ' '.join(author.get('locked_facts') or [])
    rules += ' '.join((contract.get('constraints') or {}).get('avoid') or [])
    if '不黑银行' not in rules and '已入账银行' not in rules:
        return []
    text = ' '.join(str(candidate.get(key) or '') for key in ('cost', 'core_scene', 'ending', 'unique_element'))
    pattern = r'(?:已到账|已入账|到手|银行卡|卡里).{0,42}(?:系统|代理).{0,32}(?:扣押|冻结|划扣|清零)'
    if re.search(pattern, text):
        return ['已到账的现实现金被系统代理再次扣押、冻结或划扣，违反作者的现实账户边界']
    return []


CAUSAL_RUBRIC = {
    'knowledge_and_objects': '逐项核对人物何时、通过什么可见来源得知关键消息；不能把已知写成初次得知。核对钥匙、门锁、账户、证据的归属、转移与实际能力，动作不能触发未建立的权限或功能。用正文依据核验，不替人物补信息。',
    'earned_relationship_change': '和好、信任、亲近或立场转变须由前文可观察的选择、代价及后续行动支撑。若承诺持续改变，应在另一场压力下展示实际选择，不能把道歉、做饭或签一张分工表自动等同问题已解决；不强制所有题材和好或固定等待天数。',
    'hard_constraints': '核对时间、距离、钱数、物件能力和手续条件是否真的成立；必须区分人物声称的限制与叙述确认的事实，不能用倒计时或“来不及”直接证明两项行动互斥。',
    'external_rules_and_skills': '核心危机或解法依赖现实机构权限、法律手续、专业技术时，逐项检查权限主体、适用条件、能力来源和操作到结果的因果。数字、术语、职业头衔及人物声称均不是核实依据；输入没有依据时不得声称已核实。未建立且决定情节成立的机制须标为待修，改用可建立的具体约定、已展示的普通能力或经核实的规则。明确架空机制只核对故事内规则，不冒充现实事实。',
    'promise_alignment': '按合同的读者承诺检查主要悬念与回报。甜宠的协作须改变双方理解、选择或关系，不能四个候选都围绕修设备却宣称真正不同；技术困难不能代替情感主线。其他题材按自身承诺判断，不强制恋爱。',
    'whole_story_potential': '比较候选的整篇发展潜力：初始办法会遇到什么实际后果，人物随后为何需要改变选择、边界或理解。顺序完成任务、反复揭穿同类恶人、增加人物与事故不等于厚度；把只有开篇噱头而无后续因果的候选列为执行风险。短候选不等于成稿，不能仅因篇幅短便判缺少情节。',
    'earned_payoff_path': '候选若承诺钱、偏爱、对手失去筹码或特殊权力，逐项核对已写的取得途径。人物做了什么，谁/什么机制如何回应，为什么产生该结果？泼水、骂倒、众人惊呼只能支持当场难堪，不能自动推出合同、客户、钱或法律后果；“取出对手私藏文件”“警方当场带走”须有可建立的取得与核验途径。候选可留简短执行细节给场景方案，但决定高潮成立的关键因果桥梁不能只在ending一句宣称。',
    'low_cost_alternatives': '针对支撑主问题的困境，检查一至两个基于现有场景的低成本替代行动是否直接消除障碍，例如先办手续、请近处的人代为操作或换用已经在场的工具；不得凭空假设新能力、权限或设备。',
    'resource_consistency': '比较人物拒绝的行动与实际选择是否消耗同一资源；若以没时间拒绝合照，却花更久拍单人照，须说明差别，否则时间压力没有支撑核心选择。',
    'motivated_choice': '人物遇到有吸引力的机会时，先交代他想得到什么及机会为何诱人；若随后拒绝或反抗，须出现具体不能接受的条件、亲历触发或可信的旧伤，再让选择产生可见代价与新行动。羞怯、恐惧、偏见或关系边界也可支撑非最优选择，但要有正文证据；不能凭一句正确宣言抹掉正常欲望，也不能把人物失误包装成客观上别无选择。',
    'changed_life_motive': '重生改命须由正文建立前次经历、具体后果、回到的时刻和本次改选的理由；不能只写上一世三个字，也不能用大纲补齐正文。穿越不等于重生复仇。愤怒和亲人觉醒须有积累，不让长期退让的人瞬间成为口号式强者。',
    'scope': '只报告会使核心困境、代价或结尾不成立的虚假两难；这不属于可忽略的普通生活细节省略。不穷举所有可能行动，不要求安静故事制造极端危机。',
}


FEASIBILITY_OUTPUT = (
    '每项另须feasibility对象：decision为pass或revise，basis为具体非空理由，'
    'blocking_claims为数组；有核心机制、动机或读者承诺缺口则revise，'
    'blocking_claims至少一项，每项quote准确摘自该候选文本，reason说明缺失的支撑。'
    '具体审查高潮结果由何动作赢得：若核心回报仅列在ending，前述场景只能造成尴尬或情绪变化，'
    '却声称对手失去商业资源、判罚或获得系统权限，标revise并引用跳步处；不要用类型爽点齐全代替因果。'
    'pass时blocking_claims必须为空。生成者的viable不代替本次独立判断。'
    'recommended_id只能指向viable=true且feasibility.decision=pass的最高排名项；'
    '若全部需要修订，recommended_id为null，保留全部判断，不强选。'
)


def _prose_values(value):
    """Include nested entry-pack prose without turning IDs or JSON into evidence."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, part in value.items():
            if key != 'id':
                yield from _prose_values(part)
    elif isinstance(value, list):
        for part in value:
            yield from _prose_values(part)


def _check_feasibility(row, option):
    check = row.get('feasibility')
    if (not isinstance(check, dict) or check.get('decision') not in ('pass', 'revise')
            or not isinstance(check.get('basis'), str) or not check['basis'].strip()
            or not isinstance(check.get('blocking_claims'), list)):
        raise StoryError('比较缺少独立可行性判断或理由')
    claims = check['blocking_claims']
    if (check['decision'] == 'pass' and claims) or (check['decision'] == 'revise' and not claims):
        raise StoryError('可行性判断与核心缺口记录矛盾')
    # Only actual candidate prose can ground a blocker, never an ID or the critique itself.
    source = list(_prose_values(option))
    for claim in claims:
        if (not isinstance(claim, dict) or not isinstance(claim.get('quote'), str)
                or not claim['quote'].strip() or not any(claim['quote'] in part for part in source)
                or not isinstance(claim.get('reason'), str) or not claim['reason'].strip()):
            raise StoryError('核心缺口必须准确引用对应候选并说明缺失支撑')


def require_feasible_choice(comparison, option_id):
    row = next((r for r in comparison['comparisons']
                if str(r.get('candidate_id', r.get('opening_id'))) == str(option_id)), None)
    if not row or row['feasibility']['decision'] != 'pass':
        raise StoryError('所选方案存在未解决的核心缺口；请修订候选并重新开发')


OPENING_RUBRIC = {
    'scope': '内部编辑假设；不是平台热度、机审或推荐门槛，仍需真实读者反馈验证',
    'situation': '用人物的具体处境、关系和正在发生的行动入场，让陌生读者看懂谁遇到什么事',
    'disruption': '呈现可复述的反常、冲突或代价；情感题材可以是一次关系变化，不强塞危险或打脸',
    'reader_question': '让读者形成一个具体核心疑问，不能只靠隐瞒常识、含混指代或空泛预告制造悬念',
    'payoff': '正文必须能够兑现开头的事实与情绪承诺；说明如何兑现及其铺垫成本，不能另换主问题',
    'language': '检查背景先行、空泛感叹、主题说教、人物指代不清、冗长解释和敏感双关造成误读；用动作和细节承载情绪',
    'followthrough': '不能只评价第一句钩子。检查其后是否持续发生行动、信息或关系变化，是否突然停下堆履历、流程或重复说明；必要背景随当前行动补入。不能把模型猜测的停读位置当成真实读者数据。',
    'title_progress': '若有实际标题，检查开篇是否推进标题承诺的事件或情绪，并标出原文依据；允许有依据的铺垫，不强制在固定字数内揭晓结局或完成亲吻、反击。',
    'detail_preservation': '压缩前说明该细节是否改变人物理解、选择、代价或亲近感；保留有作用的生活细节和必要关系边界。遗憾可由具体缺失的经历或物件承载，不统一改成快节奏打脸。',
    'evidence_boundary': '目前平台样本只支持待验证的编辑假设，不能据少量阅读给题材排名、推断AI标记限流或承诺收益；保持不同阅读偏好和评审分歧。',
    'difference': '三个开篇应有不同入场时刻、行动或信息安排，不能只换措辞；不得复写研究样本原文或连续情节',
    'causal_feasibility': CAUSAL_RUBRIC,
}


def validate_openings(response: dict) -> list[dict]:
    choices = response.get('openings')
    if not isinstance(choices, list) or len(choices) != 3:
        raise StoryError('开头生成必须返回3个有效候选')
    required = ('id', 'text', 'rationale', 'fulfillment_cost')
    if any(not isinstance(item, dict) or any(
            not isinstance(item.get(key), str) or not item[key].strip() for key in required)
            for item in choices):
        raise StoryError('开头候选的id、text、rationale、fulfillment_cost必须是非空字符串')
    ids = [item['id'] for item in choices]
    if len({value.strip() for value in ids}) != 3:
        raise StoryError('开头候选ID必须唯一')
    if response.get('recommended_id') not in ids:
        raise StoryError('开头生成推荐了不存在的候选，不能自动改选第一项')
    return choices


def validate_opening_comparison(response: dict, choices: list[dict]) -> dict:
    rows = response.get('comparisons')
    if (not isinstance(rows, list) or len(rows) != 3
            or any(not isinstance(row, dict) or not isinstance(row.get('opening_id'), str)
                   or not row['opening_id'].strip() for row in rows)):
        raise StoryError('开篇比较必须逐项覆盖三个开头，opening_id不得缺失')
    # Reuse the same rank, evidence-field and recommendation checks as story selection.
    normalized = {**response, 'comparisons': [
        {**row, 'candidate_id': row['opening_id']} for row in rows]}
    try:
        result = validate_comparison(normalized, [
            {'id': item['id'], 'text': item['text'], 'viable': True} for item in choices])
    except StoryError as exc:
        raise StoryError(f'开篇比较无效：{exc}') from exc
    return {**result, 'comparisons': rows}


def opening_comparison_input_hash(contract, candidate, choices):
    return digest({'contract': contract, 'candidate': candidate, 'openings': choices,
                   'rubric': OPENING_RUBRIC, 'feasibility_protocol': FEASIBILITY_OUTPUT,
                   'research_guidance_hash': guidance_fingerprint()})


def validate_comparison(response: dict, options: list[dict], *, eligibility_check=None) -> dict:
    rows = response.get('comparisons')
    ids = [str(c['id']) for c in options]
    if (not isinstance(rows, list) or len(rows) != len(options)
            or any(not isinstance(r, dict) for r in rows)
            or sorted(str(r.get('candidate_id')) for r in rows) != sorted(ids)):
        raise StoryError('候选比较必须逐项覆盖全部候选，ID不得缺失或重复')
    ranks = [r.get('rank') for r in rows]
    if any(type(v) is not int for v in ranks) or sorted(ranks) != list(range(1, len(options) + 1)):
        raise StoryError('候选比较排名必须唯一且完整')
    for row in rows:
        for key in ('strength', 'weakness', 'execution_risk'):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise StoryError('候选比较缺少优点、短板或执行风险')
        _check_feasibility(row, options[ids.index(str(row['candidate_id']))])
    eligible = [r for r in rows if options[ids.index(str(r['candidate_id']))].get('viable') is True
                and r['feasibility']['decision'] == 'pass'
                and (eligibility_check is None or eligibility_check(r))]
    if not eligible:
        if response.get('recommended_id') is not None:
            raise StoryError('全部方案被独立否决，不能推荐生成者自评可行的候选'
                             if eligibility_check is not None else
                             '全部方案存在核心缺口，不能推荐生成者自评可行的候选')
        return {'comparisons': rows, 'recommended_id': None, 'recommended_index': None,
                'assessment_scope': 'internal_editorial_only', 'commercial_validation': 'unverified'}
    recommended = str(response.get('recommended_id'))
    index = next((i for i, c in enumerate(options) if str(c['id']) == recommended), -1)
    if index < 0 or options[index].get('viable') is not True:
        raise StoryError('候选比较推荐了不存在或不可行的方案')
    require_feasible_choice({'comparisons': rows}, recommended)
    if eligibility_check is not None and not eligibility_check(
            next(r for r in rows if str(r['candidate_id']) == recommended)):
        raise StoryError('候选入口被独立否决，不能推荐该方案')
    viable_ranks = [r['rank'] for r in eligible]
    if next(r['rank'] for r in rows if str(r['candidate_id']) == recommended) != min(viable_ranks):
        raise StoryError('推荐方案与可行候选排名矛盾')
    return {'comparisons': rows, 'recommended_id': options[index]['id'],
            'recommended_index': index, 'assessment_scope': 'internal_editorial_only',
            'commercial_validation': 'unverified'}


def comparison_input_hash(contract, options, source_hash):
    return digest({'contract': contract, 'candidates': options, 'source_hash': source_hash,
                   'causal_rubric': CAUSAL_RUBRIC, 'feasibility_protocol': FEASIBILITY_OUTPUT,
                   'research_guidance_hash': guidance_fingerprint()})
