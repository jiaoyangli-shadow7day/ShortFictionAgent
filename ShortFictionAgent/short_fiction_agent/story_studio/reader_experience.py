"""A blinded, source-grounded reader veto, independent of editorial scores.

Only sampled prose is sent to this reviewer. This is a model's editorial
observation, not measured reader retention or a platform recommendation.
"""
from __future__ import annotations

import json
import hashlib
import re
from copy import deepcopy

from .contracts import StoryError, digest, visible_count
from .lead import CHAPTER

PROTOCOL = 'reader-experience-2026-10-08-v3-grounded-basis'
GROUNDING_PROTOCOL = 'reader-basis-grounding-2026-10-08-v2-atomic'
GROUNDING_FIELDS = ('reason', 'question', 'change.before', 'change.action',
                    'change.response', 'change.after', 'pull', 'compression')
SCOPE = 'anonymous_prose_windows_model_observation_not_market_validation'
INSTRUCTION = (
    '你是第一次看到匿名正文的目标读者。各窗口单独读，只使用该窗口的原文；'
    '没有未来大纲、作者自证、旧评分或预定结局，不猜测后文为现在补分。'
    '中段缺少前文时允许unclear，不能把截取边界当作故事漏洞。'
    '不评分、不泛称口语自然、节奏紧凑或无冗余。先核对实际行动与回应，再做压缩对照和追读判断，最后给verdict。'
    'change分别写原文已经发生的action、对方实际response和可观察的after；'
    '意图、预告、事前想象、内心自证属于未兑现或主观信息，不能写成对方已回应或关系已改变。'
    '没有实际回应就直说没有；推断必须写明推断及依据，不把手续完成、接受条款夸成信任或默契升级。'
    'pull独立回答：现在为何还愿意读、具体期待谁做什么、这个期待对谁有何利益或情感意义。'
    '有动作、有互动、流程合理、完成手续只能证明事件存在，不能自动证明追读动力充足。'
    'strength=sufficient须解释原文形成的当前阅读收益或尚待回应的具体牵挂；'
    '只有后续步骤预告、泛称想看发展、作者声称重视，判weak；证据不足判unclear。'
    '读者期待已兑现可question.status=settled，expected_next_action可写已收束；'
    '稳定相处、安静照顾、必要描写可以形成理解、被珍惜的感受或余味，须指出其当下证据，不要求再造悬念或冲突。'
    '简短对照：列完行程、对方答应，只证明约定成立；若说不出当下牵挂或感受，不补造默契。'
    '一人留意小事并照顾，对方当场回应，可以确认偏爱；职业材料使对方拒绝并迫使改策，也可形成具体期待。'
    'most_skippable选真正最想跳过的一段，若无明显负担也选相对最弱段并如实说明。'
    'compression.replacement写该段的压缩对照，保留原段事实、行动先后、边界、话语意思和认知状态；'
    '不能把计划写成已经完成、补造回应或续写下一事件。无需改动时可原样保留。'
    'interest_effect判断替换后当前兴趣improved/preserved/damaged，reason解释具体增加或损失的牵挂、理解、感受。'
    'loss_if_removed只写删除该段的当前损失；若没有就直说，不因未来也许回收一律判needed。'
    '人物真实、交代身份、埋伏笔等标签本身不足以证明必要；须说明原段事实或表达怎样作用于眼前阅读。'
    '术语、句长、清单、比喻都不是自动问题；可压缩也不必然停读，但若解释确实挡住期待回应则revise。'
    'verdict=continue/revise/unclear；blocks_current_interest须revise，pull为weak/unclear会独立阻止通过。'
    '每项文字字段都须解释具体对象和因果，不能只贴功能标签。evidence_paragraph_indices选本窗已列出的1至3个整数编号，'
    '不要抄quote，程序锚定原文。示意值a|b须选择一个。输出JSON对象windows数组，完整覆盖window_id。'
)


def enabled(contract: dict) -> bool:
    return contract.get('format') == 'short_story'


def _paragraphs(body: str) -> list[dict]:
    return [{'paragraph_index': index, 'text': match.group(),
             'span': [match.start(), match.end()]}
            for index, match in enumerate(m for m in re.finditer(r'[^\n]+', body) if m.group().strip())]


def windows(body: str) -> list[dict]:
    """Choose complete paragraphs; sizes are sampling budgets, not quality rules."""
    paragraphs = _paragraphs(body)
    if not paragraphs:
        raise StoryError('读者体验审读正文为空')
    chapter = CHAPTER.search(body)
    beginning = chapter.end() if chapter else 0
    prose = [p for p in paragraphs if p['span'][0] >= beginning]
    if not prose:
        raise StoryError('读者体验审读缺少第一章正文')
    first, count = [], 0
    for item in prose:
        first.append(item)
        count += visible_count(item['text'])
        if count >= 1000:
            break
    result = [_window('first_screen', first)]
    # Short calibration fragments require one complete window, not two copies.
    remaining = [p for p in prose if p['span'][0] >= first[-1]['span'][1]]
    if remaining:
        midpoint = (prose[0]['span'][0] + prose[-1]['span'][1]) // 2
        anchor = min(range(len(remaining)), key=lambda i: abs(remaining[i]['span'][0] - midpoint))
        middle, count = [], 0
        for item in remaining[anchor:]:
            middle.append(item)
            count += visible_count(item['text'])
            if count >= 1100:
                break
        result.append(_window('middle', middle))
    return result


def _window(window_id: str, paragraphs: list[dict]) -> dict:
    return {'window_id': window_id,
            'span': [paragraphs[0]['span'][0], paragraphs[-1]['span'][1]],
            'paragraphs': paragraphs}


def prompt(body: str, contract: dict) -> str:
    """Public API for both production and live calibration; no author rationale."""
    shape = {'window_id': '本窗window_id', 'verdict': 'continue|revise|unclear',
        'reason': '具体读者判断', 'fix_intent': '保留或具体修订方向',
        'question': {'status': 'active|settled|absent', 'text': '眼下期待或已兑现的感受',
                     'evidence_paragraph_indices': ['本窗段落编号（整数）']},
        'change': {'kind': 'relationship|interest|understanding|atmosphere|none',
                   'before': '当前处境', 'action': '已发生行动或说明尚未发生',
                   'response': '实际回应或说明尚无回应', 'after': '已改变或确认什么；区分推断',
                   'evidence_paragraph_indices': ['本窗段落编号（整数）']},
        'most_skippable': {'paragraph_index': '本窗段落编号（整数）',
                           'pressure': 'explanation|workflow|description|none',
                           'necessity': 'needed|compressible|removable|no_explanation',
                           'burden': 'tolerable|blocks_current_interest',
                           'reason': '为何此段相对最弱或令人想跳过',
                           'loss_if_removed': '删去会损失哪项当前信息或感受，或没有损失',
                           'compression': {'replacement': '保留该段事实的压缩对照或原段',
                               'interest_effect': 'improved|preserved|damaged',
                               'reason': '对当前兴趣的具体收益或损失'}},
        'pull': {'strength': 'sufficient|weak|unclear',
                 'reason_to_continue': '此刻愿意继续的具体原因，或为何不足',
                 'expected_next_action': '具体期待的行动，或已收束的余味',
                 'incentive': '为何在意这个回应、变化或感受',
                 'evidence_paragraph_indices': ['本窗段落编号（整数）']}}
    return INSTRUCTION + '\n' + json.dumps({
        'protocol': PROTOCOL, 'scope': SCOPE,
        'audience': {'description': (contract.get('audience') or {}).get('description', '')},
        'schema_example': shape,
        'enums': {'question.status': ['active', 'settled', 'absent'],
                  'change.kind': ['relationship', 'interest', 'understanding', 'atmosphere', 'none'],
                  'most_skippable.pressure': ['explanation', 'workflow', 'description', 'none'],
                  'most_skippable.necessity': ['needed', 'compressible', 'removable', 'no_explanation'],
                  'most_skippable.burden': ['tolerable', 'blocks_current_interest'],
                  'most_skippable.compression.interest_effect': ['improved', 'preserved', 'damaged'],
                  'pull.strength': ['sufficient', 'weak', 'unclear']},
        'reader_windows': windows(body)}, ensure_ascii=False)


def _text_fields(row: dict, names: tuple[str, ...]) -> None:
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in names):
        raise StoryError('读者体验判断缺少具体期待、变化、阅读收益或压缩损益')


def _evidence(row: dict, available: dict[int, dict]) -> list[dict]:
    indices = row.get('evidence_paragraph_indices')
    if (not isinstance(indices, list) or not 1 <= len(indices) <= 3
            or any(type(i) is not int or i not in available for i in indices)
            or len(set(indices)) != len(indices)):
        raise StoryError('读者体验证据必须来自当前匿名窗口，编号不得缺失、重复或越窗')
    return [deepcopy(available[index]) for index in indices]


def validate(response: dict, body: str, contract: dict) -> dict:
    """Ground model IDs ourselves; never accept a supplied offset or rewritten quote."""
    expected = windows(body)
    rows = response.get('windows') if isinstance(response, dict) else None
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or sorted(str(row.get('window_id')) for row in rows)
            != sorted(window['window_id'] for window in expected)):
        raise StoryError('读者体验必须完整覆盖匿名窗口，ID不能缺失或重复')
    result = []
    by_id = {row['window_id']: row for row in rows}
    for window in expected:
        row = by_id[window['window_id']]
        available = {p['paragraph_index']: p for p in window['paragraphs']}
        if row.get('verdict') not in {'continue', 'revise', 'unclear'}:
            raise StoryError('读者体验verdict无效')
        _text_fields(row, ('reason', 'fix_intent'))
        question, change, skip, pull = (row.get(key) for key in ('question', 'change', 'most_skippable', 'pull'))
        if any(not isinstance(item, dict) for item in (question, change, skip, pull)):
            raise StoryError('读者体验缺少期待、变化、追读动力或最想跳过段')
        if question.get('status') not in {'active', 'settled', 'absent'}:
            raise StoryError('读者体验当前期待状态无效')
        _text_fields(question, ('text',))
        if change.get('kind') not in {'relationship', 'interest', 'understanding', 'atmosphere', 'none'}:
            raise StoryError('读者体验变化种类无效')
        _text_fields(change, ('before', 'action', 'response', 'after'))
        if pull.get('strength') not in {'sufficient', 'weak', 'unclear'}:
            raise StoryError('读者体验追读动力强度无效')
        _text_fields(pull, ('reason_to_continue', 'expected_next_action', 'incentive'))
        if (skip.get('pressure') not in {'explanation', 'workflow', 'description', 'none'}
                or skip.get('necessity') not in {'needed', 'compressible', 'removable', 'no_explanation'}
                or skip.get('burden') not in {'tolerable', 'blocks_current_interest'}):
            raise StoryError('读者体验解释必要性判断无效')
        _text_fields(skip, ('reason', 'loss_if_removed'))
        compression = skip.get('compression')
        if (not isinstance(compression, dict)
                or compression.get('interest_effect') not in {'improved', 'preserved', 'damaged'}):
            raise StoryError('读者体验缺少有效的保留事实压缩对照')
        _text_fields(compression, ('replacement', 'reason'))
        index = skip.get('paragraph_index')
        if type(index) is not int or index not in available:
            raise StoryError('最想跳过段必须锚定当前匿名窗口')
        if row['verdict'] == 'continue' and (skip['burden'] == 'blocks_current_interest'
                or question['status'] == 'absent' and change['kind'] == 'none'):
            raise StoryError('读者体验正面结论与自身停读证据冲突')
        result.append({'window_id': window['window_id'], 'span': window['span'],
            'verdict': row['verdict'], 'reason': row['reason'], 'fix_intent': row['fix_intent'],
            'question': {**question, 'evidence': _evidence(question, available)},
            'change': {**change, 'evidence': _evidence(change, available)},
            'pull': {**pull, 'evidence': _evidence(pull, available)},
            'most_skippable': {**skip, 'evidence': deepcopy(available[index])}})
    return {'protocol': PROTOCOL, 'scope': SCOPE, 'content_hash': digest(body),
            'source_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
            'contract_hash': digest(contract), 'window_plan_hash': digest(expected),
            'window_sources': expected, 'windows': result}


def gate(body: str, contract: dict, record: dict | None) -> list[str]:
    """A high final score cannot erase a frozen window veto or replace its evidence."""
    if not enabled(contract):
        return []
    if (not isinstance(record, dict) or record.get('protocol') != PROTOCOL
            or record.get('scope') != SCOPE or record.get('content_hash') != digest(body)
            or record.get('contract_hash') != digest(contract)
            or record.get('window_plan_hash') != digest(windows(body))):
        raise StoryError('缺少当前正文、合同和窗口绑定的独立读者体验审读')
    normalized = validate(record, body, contract)
    if record != normalized:
        raise StoryError('读者体验保存证据与当前原文位置不匹配')
    return [blocker for row in normalized['windows'] for blocker in _blockers(row)]


def _blockers(row: dict) -> list[str]:
    blockers = []
    if row['verdict'] != 'continue':
        blockers.append(f"reader_experience:{row['window_id']}:{row['verdict']}")
    if row['pull']['strength'] != 'sufficient':
        blockers.append(f"reader_experience:{row['window_id']}:pull:{row['pull']['strength']}")
    return blockers


def issues(record: dict) -> list[dict]:
    result = []
    for row in record['windows']:
        if not _blockers(row):
            continue
        evidence = row['most_skippable']['evidence']
        reason = row['reason']
        if row['pull']['strength'] != 'sufficient':
            evidence = row['pull']['evidence'][0]
            reason += (f"；追读动力{row['pull']['strength']}：{row['pull']['reason_to_continue']}"
                       f"；当前阅读收益：{row['pull']['incentive']}")
        result.append({'severity': 'blocker', 'category': 'economy', 'origin': 'reader_experience',
            'reader_window_id': row['window_id'], 'reason': reason,
            'fix_intent': row['fix_intent'], 'quote': evidence['text'], 'span': evidence['span']})
    return result


GROUNDING_INSTRUCTION = (
    '独立核验匿名读者审读的依据，只使用各自窗口原文。没有全文、未来大纲、作者解释或旧评分。'
    '不重评兴趣、不改正文，不讨论原文应该更好或更坏；只核实审读说的事是否由这个窗口支持。'
    '每窗完整核验reason/question/change.before/change.action/change.response/change.after/pull/compression八项；'
    '各项面对原字段逐字说法，核验完整断言，不得概括成较弱说法、只挑有依据部分或替它补限定词后判supported。'
    '一项中只要有无依据事实，该项为unsupported；不能因另半句有依据放过。'
    'supported表示事实或认知状态有原文依据；inferred表示合理推断且原审读已明确将它说成推断、猜测、期待或疑问；'
    '未注明的推断、捏造回应、把计划或意愿称为已经实现均为unsupported，不能自行替原审读补上“可能”使其通过。'
    'reason核所有事实前提；question核问题的前提，不要求问题中的未来行动已经发生。'
    'change.before/action/response/after分别核验，区分叙述者主观声称、对方实际回应、意愿和已经发生的结果。'
    '提出一起去检查不等于已经动身；答应明天付款不等于已付清；亲属现身不等于已确认串通骗财。'
    '未直接回应不能用主观意愿替代；确认共识不能替原审读声称的已实现结果开脱。'
    'pull只核依赖的事实与认知状态，不再判断追读强弱。expected_next_action和问题本身已明示对未来的期待，'
    '允许尚未发生，但其中当成事实的身份、动机、处境仍须有窗口依据。incentive中的读者愿望不是已实现结果。'
    '安静相处、温情、悲剧和坏结局均可成立；核验不能强造外部冲突、道德改善或幸福结局。'
    'compression逐比原段与replacement，保留原段事实、话语意思、行动先后、必要边界和认知状态。'
    '删去事实、添加未给出的事实、把主观想法改成客观结果、把计划改成已执行，均为unsupported；'
    '这是压缩对照不可靠，不意味着正文应返修。原样保留可以supported。'
    '各组reason写具体被核验的说法与原文对应或差异；只给1至3个本窗段落整数编号。'
    '不输出兴趣评分、verdict、pull.strength或正文修订建议。输出JSON windows数组，每窗checks数组完整覆盖八项。'
)


def _current_reader_record(body: str, contract: dict, record: dict) -> None:
    # Binding is required even when the reader observation itself vetoes prose.
    gate(body, contract, record)


def _basis_projection(record: dict) -> list[dict]:
    result = []
    for row in record['windows']:
        result.append({'window_id': row['window_id'], 'claims': {
            'reason': row['reason'],
            'question': {key: row['question'][key] for key in ('status', 'text')},
            **{f'change.{key}': row['change'][key] for key in ('before', 'action', 'response', 'after')},
            'pull': {key: row['pull'][key]
                     for key in ('reason_to_continue', 'expected_next_action', 'incentive')},
            'compression': {'paragraph_index': row['most_skippable']['paragraph_index'],
                            'replacement': row['most_skippable']['compression']['replacement']}}})
    return result


def grounding_prompt(body: str, contract: dict, record: dict) -> str:
    """Check source support independently, without the reader's verdict or score."""
    _current_reader_record(body, contract, record)
    return GROUNDING_INSTRUCTION + '\n' + json.dumps({
        'protocol': GROUNDING_PROTOCOL, 'reader_protocol': PROTOCOL,
        'schema_example': {'windows': [{'window_id': '本窗window_id', 'checks': [{
            'field': 'reason|question|change.before|change.action|change.response|change.after|pull|compression',
            'status': 'supported|inferred|unsupported',
            'reason': '具体说法与窗口原文的对应或差异',
            'evidence_paragraph_indices': ['本窗段落编号（整数）']}]}]},
        'fields': list(GROUNDING_FIELDS), 'reader_windows': windows(body),
        'reader_basis': _basis_projection(record)}, ensure_ascii=False)


def validate_grounding(response: dict, body: str, contract: dict, record: dict) -> dict:
    """Bind a source-support audit to this precise frozen reader observation."""
    _current_reader_record(body, contract, record)
    expected = windows(body)
    rows = response.get('windows') if isinstance(response, dict) else None
    if (not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows)
            or sorted(str(row.get('window_id')) for row in rows)
            != sorted(window['window_id'] for window in expected)):
        raise StoryError('读者依据核验必须完整覆盖原匿名窗口，ID不能缺失或重复')
    by_id = {row['window_id']: row for row in rows}
    result = []
    for window in expected:
        row = by_id[window['window_id']]
        checks = row.get('checks')
        if (not isinstance(checks, list) or any(not isinstance(check, dict) for check in checks)
                or sorted(str(check.get('field')) for check in checks) != sorted(GROUNDING_FIELDS)):
            raise StoryError('读者依据核验必须完整覆盖八项说法，字段不能缺失或重复')
        available = {p['paragraph_index']: p for p in window['paragraphs']}
        by_field = {check['field']: check for check in checks}
        normalized = []
        for field in GROUNDING_FIELDS:
            check = by_field[field]
            if check.get('status') not in {'supported', 'inferred', 'unsupported'}:
                raise StoryError('读者依据核验支持状态无效')
            _text_fields(check, ('reason',))
            evidence = _evidence(check, available)
            normalized.append({'field': field, 'status': check['status'], 'reason': check['reason'],
                'evidence_paragraph_indices': deepcopy(check['evidence_paragraph_indices']),
                'evidence': evidence})
        result.append({'window_id': window['window_id'], 'span': deepcopy(window['span']),
                       'checks': normalized})
    return {'protocol': GROUNDING_PROTOCOL, 'reader_protocol': PROTOCOL, 'scope': SCOPE,
        'content_hash': digest(body), 'source_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
        'contract_hash': digest(contract), 'reader_review_hash': digest(record),
        'window_plan_hash': digest(expected), 'windows': result}


def grounding_gate(body: str, contract: dict, record: dict, grounding_record: dict | None) -> list[str]:
    """Unsupported reviewer claims block confirmation, not the manuscript itself."""
    if not enabled(contract):
        return []
    _current_reader_record(body, contract, record)
    if (not isinstance(grounding_record, dict) or grounding_record.get('protocol') != GROUNDING_PROTOCOL
            or grounding_record.get('reader_protocol') != PROTOCOL
            or grounding_record.get('scope') != SCOPE
            or grounding_record.get('content_hash') != digest(body)
            or grounding_record.get('contract_hash') != digest(contract)
            or grounding_record.get('reader_review_hash') != digest(record)
            or grounding_record.get('window_plan_hash') != digest(windows(body))):
        raise StoryError('缺少当前正文、合同与原审读记录绑定的读者依据核验')
    normalized = validate_grounding(grounding_record, body, contract, record)
    if grounding_record != normalized:
        raise StoryError('读者依据核验保存证据与原窗口位置不匹配')
    return [f"reader_basis:{row['window_id']}:{check['field']}:unsupported"
            for row in normalized['windows'] for check in row['checks']
            if check['status'] == 'unsupported']


def basis_repair_prompt(body: str, contract: dict, record: dict, grounding_record: dict) -> str:
    """Repair the observation's basis; never turn an audit error into prose edits."""
    grounding_gate(body, contract, record, grounding_record)
    # Source paragraphs occur once in prompt(); omit saved evidence/source copies.
    def without_anchors(value):
        if isinstance(value, dict):
            return {key: without_anchors(item) for key, item in value.items() if key not in {'evidence', 'span'}}
        if isinstance(value, list):
            return [without_anchors(item) for item in value]
        return value
    original = {'windows': [{key: without_anchors(row[key]) for key in (
        'window_id', 'verdict', 'reason', 'fix_intent', 'question', 'change', 'most_skippable', 'pull')}
        for row in record['windows']]}
    findings = [{'window_id': row['window_id'], 'checks': [
        {key: check[key] for key in ('field', 'status', 'reason', 'evidence_paragraph_indices')}
        for check in row['checks'] if check['status'] != 'supported']}
        for row in grounding_record['windows']]
    instruction = (
        '修正下面的读者审读记录，只使用相同匿名窗口，不改写正文。逐项纠正核验指出的无依据说法，'
        '保持实际行动、推断、认知状态和压缩对照准确；不可添加未发生回应或结果。'
        '不以“通过”为目标，不强制continue，也不因核验发现审读错误就把正文判得更差。'
        '只有纠正后原阅读收益或停读理由不成立时才重判对应verdict/pull；仍有依据的负面判断须保留。'
        '输出完整原reader windows schema供重新核验，不输出正文、核验checks或解释文字。\n'
    )
    return instruction + prompt(body, contract) + '\n' + json.dumps({
        'basis_repair': {'reader_review_hash': digest(record),
                         'original_reader_response': original, 'basis_findings': findings}}, ensure_ascii=False)
