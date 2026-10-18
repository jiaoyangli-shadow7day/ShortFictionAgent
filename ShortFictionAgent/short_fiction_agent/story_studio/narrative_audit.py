"""Source-bound narrative coverage for the same editor, never a quality score.

The program checks what was answered and what source was supplied. Whether a
choice changes the story or a quiet scene earns its payoff remains the editor's
semantic judgment. No extra model role or call is introduced here.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .contracts import digest
from . import editorial_priority

PROTOCOL = 'source-bound-reader-expectation-v5-original-entry'
VALIDATION_VERSION = 'source-bound-narrative-gate-v6-original-entry'
INSTRUCTION = (
    editorial_priority.INSTRUCTION +
    '同时输出narrative_audit，不能只核事实或挑漂亮片段宣布整篇精彩。'
    'coverage恰有beginning/middle/ending三项，每项含phase、evidence_ids、change、promise_link；'
    '按narrative_scope和coverage_plan检查本次实际提供范围的前中后，写实际变化和作者承诺的关联。'
    'coverage的change和promise_link须按实际阅读顺序，分清期待何时提出、何时已回答或提前告知；'
    '回答之后核对正文还留下哪些有据的未定方法、人物选择、回应或代价，不能把读者明确已知终点的'
    '后续流程变化本身当成期待持续的证据。'
    'chunk只代表本阅读块，不冒充全篇；full才是整篇终审。'
    'weakest_middle含evidence_ids、verdict(effective/thin/uncertain)、reason、counterevidence_ids、'
    'counterevidence、repair_scope(none/local/replan)，必须从中部选择最弱处，说明是否仍有效。'
    '有作用、四环齐全不等于值得如此详写。weakest_middle另含compression={compressed_version,'
    'preserved_fact_ids,choice_response_ids,lost_value}：先真正写出该处可替代的压缩版本，保留必要事实、'
    '限制条件、人物选择与对方回应，再比较原展开独有的阅读价值。不能用“谈妥了”“成功了”这种'
    '删掉关键事实和接招的空摘要证明每段都必要。preserved_fact_ids和choice_response_ids绑定保留的'
    '原段，且都须位于weakest_middle.evidence_ids界定的原段连续区间内；'
    '无选择回应时可为空但须在lost_value解释，不能编造。compressed_version仅为审稿对照，'
    '不是直接交付的新正文。lost_value具体说明原稿多出来的理解、期待、人物体验或无增益负担；'
    '若基本不变，指出局部展开负担；不得靠尾部一处温情为大段操作补证。'
    '必要职业交锋、技术判断和安静关系回应可写足，不按步骤、术语或对白数量机械否决。'
    '仅有局部表达负担时可保留effective并列suggestion；挤掉核心戏、连续无效重复或实质未兑现时，'
    '才依据原段判thin并给出修改范围。'
    'core_scenes每项含role(weakest_core/ending)、start_id、end_id、attempt_ids、response_ids、turn_ids、'
    'payoff_ids，以及相同的verdict、reason、counterevidence_ids、counterevidence、repair_scope。'
    'chunk至少检查本块最弱核心戏；本块只有过渡时可verdict=transition，说明过渡作用，不硬造四拍。'
    'full必须分别检查最弱核心戏和结尾主要回报；短稿可指向同一场，不规定场数。'
    'start_id/end_id标明完整场景范围，四组ID分别绑定尝试、对方回应或阻力、主动应对改写局面、兑现。'
    'effective核心戏四组均须原段；温柔、克制和共同选择可同段承担多个环节，不强制外部灾难。'
    '人物回应可用response_ids；observations.role仍只允许condition/process/outcome，接招过程写process。'
    'repetition含earlier_ids、later_ids及verdict、reason、counterevidence_ids、counterevidence、repair_scope，'
    '对照前后最可能同构的两处；资源、责任、关系、策略或理解有变化只是必要事实，'
    '不能单凭结果不同证明重复展开值得阅读。核对读者已知信息及新增的选择、回应、压力和体验；无同构也须给两端依据。'
    '缺少上下文用uncertain，最弱不等于必须thin；安静、生活细节和必要过渡有效时可effective。'
    'counterevidence写能反驳负面判断的实际原段及解释，没有则明确说明，不为公正硬造问题。'
    'mainline含question、evidence_ids及相同verdict/reason/counterevidence_ids/counterevidence/repair_scope；'
    '用一句具体问题复述仅凭正文读者在等什么，先对照original_entry_context、作者原始受众与实际标题导语，'
    '再核对planned_reader_promise.main_question等方案解释是否保持同一吸引点并在正文实际成立。'
    '主问题写得通不能掩盖原始设定收益或题材欲望退出；以关系成熟代替主要吸引点仍须指出。'
    '计划不证明已经写出，也不能替原文补事实或自行重定义目标读者。'
    '不把“发财、恋爱、逆袭”等方向当成贯穿全篇的具体期待；没有提供作者主问题时由正文判断，不替作者补写。'
    'mainline须结合期待提出处、可能提前回答处及其后原段，说明读者此时仍在等哪个未定问题；'
    '若承诺的结果、关系态度或解决路径已经明确，须判断后续是否实际建立新的方法、选择、回应或代价期待，'
    '不能用换场、办手续、金额入账等已知终点的兑现流程替原期待续命。'
    '结果前置的导语、倒叙及已知结局可以有效：读者仍可期待如何做到、为何如此、付出什么代价或人物怎样选择；'
    '只要这些问题由原段实际建立并持续展开，应据此判有效，不因先透露结果机械判thin，也不强制结局未知或新增灾难。'
    'chapter_handoffs按coverage_plan.chapter_endings逐项输出boundary_id、before_question、'
    'actual_change、next_question、evidence_ids及相同判断字段。必须引用该项tail_ids内的实际章末原段，'
    '说明上一个期待的兑现和下一段具体可读的问题；资源变化、泛泛成功口号或“后面会怎样”不能代替问题。'
    'is_final=true允许next_question为空，以实际解决原问题及结局回报通过，不强行悬崖或增加灾难。'
    '无章节稿按真实稿尾检查一次；chunk只检查实际给到的完整章节尾，分块边界不是章节尾。'
    'thin还用于有据的主问题游移、持续不能形成具体期待，或展开负担挤掉核心戏，给出具体影响和修改方向。'
    '整体同构、设定可删、关键选择无后果或高潮需重建用replan，局部不足用local。'
    '各judgment可含editorial_priority、reading_effect；只有reading_core的thin/uncertain阻断。'
    'copyedit_detail保留原verdict和证据，只留初始校对提醒，repair_scope可为none，不要求闭合手续。'
    'effective/transition用repair_scope=none，核心uncertain不能当通过。字段齐全只证明已回答，不证明语义正确。'
)
PHASES = ('beginning', 'middle', 'ending')
CHAPTER_HEADING = re.compile(r'^(?:第[一二三四五六七八九十百零〇两\d]+[章节回][^\r\n]*|[0-9]{1,3}[、.．]?|[一二三四五六七八九十]+、)$')


def chapter_endings(rows):
    """Locate actual headings and source tails, never equate a reading cut with a chapter."""
    headings = [i for i, row in enumerate(rows) if CHAPTER_HEADING.fullmatch(row['text'].strip())]
    starts = headings or [0]
    result = []
    for number, start in enumerate(starts):
        stop = starts[number + 1] if number + 1 < len(starts) else len(rows)
        content = [row for row in rows[start:stop] if not CHAPTER_HEADING.fullmatch(row['text'].strip())]
        if not content:
            continue
        tail = [row['id'] for row in content[-3:]]
        result.append({'boundary_id': content[-1]['id'], 'tail_ids': tail,
            'is_final': stop == len(rows)})
    return result


def _rows(body):
    return [{'id': f'p{i}', 'text': m.group(), 'span': [m.start(), m.end()]}
            for i, m in enumerate(m for m in re.finditer(r'[^\n]+', body) if m.group().strip())]


def coverage_plan(body, allowed_ids=None, *, scope='full', rows=None, full_rows=None):
    rows = _rows(body) if rows is None else rows
    allowed = set(allowed_ids) if allowed_ids is not None else {r['id'] for r in rows}
    selected = rows if scope == 'full' else [r for r in rows if r['id'] in allowed]
    total = sum(len(r['text']) for r in selected)
    bands = {key: [] for key in PHASES}
    cursor = 0
    for row in selected:
        end = cursor + len(row['text'])
        for index, phase in enumerate(PHASES):
            if cursor < total * (index + 1) / 3 and end > total * index / 3:
                bands[phase].append(row['id'])
        cursor = end
    chapter_rows = rows if full_rows is None else full_rows
    boundaries = [item for item in chapter_endings(chapter_rows)
                  if scope == 'full' or set(item['tail_ids']) <= allowed]
    return {'protocol': PROTOCOL, 'scope': scope, 'phases': {
        key: {'start_id': ids[0], 'end_id': ids[-1]} for key, ids in bands.items() if ids},
        'chapter_endings': boundaries}


def normalize(response, body, allowed_ids=None, *, scope='full'):
    """Missing narrative answers are explicit incompleteness, not fake empty passes."""
    rows = _rows(body)
    bank = {r['id']: r for r in rows}
    available = set(bank) if allowed_ids is None else set(allowed_ids)
    ordered = [r['id'] for r in rows if r['id'] in available]
    plan = coverage_plan(body, ordered, scope=scope)
    missing, findings, reminders = [], [], []
    raw = response.get('narrative_audit') if isinstance(response, dict) else None
    normalized = {'coverage': [], 'mainline': None, 'chapter_handoffs': [],
                  'weakest_middle': None, 'core_scenes': [], 'repetition': None}
    positions = {r['id']: i for i, r in enumerate(rows)}

    def fail(path):
        if path not in missing:
            missing.append(path)

    def text(value, path):
        if not isinstance(value, str) or not value.strip():
            fail(path)
            return ''
        return value.strip()

    def ids(value, path, *, empty=False):
        if (not isinstance(value, list) or (not empty and not value) or len(value) > len(available)
                or any(not isinstance(k, str) or k not in available for k in value)
                or len(value) != len(set(value))):
            fail(path)
            return []
        return deepcopy(value)

    def bound(entry):
        keys = entry.get('evidence_ids', [])
        entry['evidence'] = [deepcopy(bank[k]) for k in keys]
        return entry

    def judgment(raw_item, path, *, transitions=False):
        if not isinstance(raw_item, dict):
            fail(path)
            return None
        start_missing = len(missing)
        priority_fields = editorial_priority.fields(raw_item)
        detail = editorial_priority.is_detail(priority_fields)
        verdict = raw_item.get('verdict') if isinstance(raw_item.get('verdict'), str) else None
        if verdict not in {'effective', 'thin', 'uncertain'} | ({'transition'} if transitions else set()):
            fail(path + '.verdict')
        repair = raw_item.get('repair_scope') if isinstance(raw_item.get('repair_scope'), str) else None
        if repair not in {'none', 'local', 'replan'} or (verdict == 'thin' and repair == 'none' and not detail) or (
                verdict in {'effective', 'transition'} and repair != 'none'):
            fail(path + '.repair_scope')
        item = {'verdict': verdict, 'reason': text(raw_item.get('reason'), path + '.reason'),
                'counterevidence_ids': ids(raw_item.get('counterevidence_ids'), path + '.counterevidence_ids', empty=True),
                'counterevidence': text(raw_item.get('counterevidence'), path + '.counterevidence'),
                'repair_scope': repair, **priority_fields}
        if detail and repair != 'none':
            item.update(model_repair_scope=repair, repair_scope='none')
        item['_valid'] = len(missing) == start_missing
        return item

    def finding(item, path):
        if editorial_priority.is_detail(item):
            if item['verdict'] in {'thin', 'uncertain'} and item.pop('_valid', False) and item.get('evidence_ids'):
                reminders.append({**deepcopy(item), 'origin': 'narrative_audit',
                    'judgment_path': path, 'source_hash': digest(body)})
            item.pop('_valid', None)
            return
        if item['verdict'] == 'uncertain':
            fail(path + '.uncertain')
        if item['verdict'] == 'thin' and item.pop('_valid', False) and item.get('evidence_ids'):
            findings.append({'severity': 'blocker', 'origin': 'narrative_audit',
                'scope': 'global' if item['repair_scope'] == 'replan' else 'local',
                'repair_scope': item['repair_scope'], 'reason': item['reason'],
                'fix_intent': '依据原场景重建承诺、人物选择及后果和实际互动；保留有效安静互动。',
                'evidence_ids': deepcopy(item['evidence_ids']), 'evidence': deepcopy(item['evidence']),
                **editorial_priority.fields(item)})
        item.pop('_valid', None)

    if not isinstance(raw, dict):
        fail('narrative_audit')
        raw = {}
    coverage = raw.get('coverage')
    if not isinstance(coverage, list):
        fail('coverage')
        coverage = []
    for phase in PHASES:
        matches = [x for x in coverage if isinstance(x, dict) and x.get('phase') == phase]
        path = 'coverage.' + phase
        if len(matches) != 1:
            fail(path)
            continue
        row = matches[0]
        item = {'phase': phase, 'evidence_ids': ids(row.get('evidence_ids'), path + '.evidence_ids'),
                'change': text(row.get('change'), path + '.change'),
                'promise_link': text(row.get('promise_link'), path + '.promise_link')}
        band = plan['phases'].get(phase)
        if not band or not any(positions[band['start_id']] <= positions[k] <= positions[band['end_id']]
                               for k in item['evidence_ids']):
            fail(path + '.source_range')
        normalized['coverage'].append(bound(item))
    if len(coverage) != 3:
        fail('coverage.count')
    item = judgment(raw.get('mainline'), 'mainline')
    if item is not None:
        before = len(missing)
        item['question'] = text(raw['mainline'].get('question'), 'mainline.question')
        item['evidence_ids'] = ids(raw['mainline'].get('evidence_ids'), 'mainline.evidence_ids')
        item['_valid'] = item['_valid'] and len(missing) == before
        normalized['mainline'] = bound(item)
        finding(item, 'mainline')
    handoffs = raw.get('chapter_handoffs')
    if not isinstance(handoffs, list):
        fail('chapter_handoffs')
        handoffs = []
    expected = {item['boundary_id']: item for item in plan['chapter_endings']}
    found = []
    for index, handoff in enumerate(handoffs):
        path = f'chapter_handoffs[{index}]'
        before = len(missing)
        item = judgment(handoff, path)
        if item is None:
            continue
        boundary = handoff.get('boundary_id')
        if not isinstance(boundary, str) or boundary not in expected or boundary in found:
            fail(path + '.boundary_id')
            continue
        found.append(boundary)
        item['boundary_id'] = boundary
        item['before_question'] = text(handoff.get('before_question'), path + '.before_question')
        item['actual_change'] = text(handoff.get('actual_change'), path + '.actual_change')
        next_question = handoff.get('next_question')
        item['next_question'] = (next_question.strip() if expected[boundary]['is_final']
            and isinstance(next_question, str) else text(next_question, path + '.next_question'))
        item['evidence_ids'] = ids(handoff.get('evidence_ids'), path + '.evidence_ids')
        if not set(item['evidence_ids']) & set(expected[boundary]['tail_ids']):
            fail(path + '.actual_chapter_tail')
        item['_valid'] = item['_valid'] and len(missing) == before
        normalized['chapter_handoffs'].append(bound(item))
        finding(item, path)
    for boundary in expected:
        if boundary not in found:
            fail('chapter_handoffs.' + boundary)
    item = judgment(raw.get('weakest_middle'), 'weakest_middle')
    if item is not None:
        item['evidence_ids'] = ids(raw['weakest_middle'].get('evidence_ids'), 'weakest_middle.evidence_ids')
        band = plan['phases'].get('middle')
        if not band or not any(positions[band['start_id']] <= positions[k] <= positions[band['end_id']]
                               for k in item['evidence_ids']):
            fail('weakest_middle.source_range')
            item['_valid'] = False
        normalized['weakest_middle'] = bound(item)
        before = len(missing)
        compression = raw['weakest_middle'].get('compression')
        if not isinstance(compression, dict):
            fail('weakest_middle.compression')
        else:
            path = 'weakest_middle.compression'
            item['compression'] = {'compressed_version': text(compression.get('compressed_version'), path + '.compressed_version'),
                'preserved_fact_ids': ids(compression.get('preserved_fact_ids'), path + '.preserved_fact_ids'),
                'choice_response_ids': ids(compression.get('choice_response_ids'), path + '.choice_response_ids', empty=True),
                'lost_value': text(compression.get('lost_value'), path + '.lost_value')}
            # The counterfactual must compress the actual weak passage, not
            # borrow a different successful scene as a defence of its length.
            anchor_positions = [positions[k] for k in item['evidence_ids']]
            if anchor_positions:
                left, right = min(anchor_positions), max(anchor_positions)
                item['source_window_ids'] = [row['id'] for row in rows[left:right + 1]]
                if not set(item['source_window_ids']) <= available:
                    fail(path + '.unprovided_weak_passage')
                for key in ('preserved_fact_ids', 'choice_response_ids'):
                    if any(not left <= positions[k] <= right for k in item['compression'][key]):
                        fail(path + '.' + key + '.outside_weak_passage')
        item['_valid'] = item['_valid'] and len(missing) == before
        finding(item, 'weakest_middle')
    scenes = raw.get('core_scenes')
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 3:
        fail('core_scenes')
        scenes = []
    roles = []
    for index, scene in enumerate(scenes):
        path = f'core_scenes[{index}]'
        before = len(missing)
        item = judgment(scene, path, transitions=scope == 'chunk')
        if item is None:
            continue
        role = scene.get('role') if isinstance(scene.get('role'), str) else None
        if role not in {'weakest_core', 'ending'}:
            fail(path + '.role')
        roles.append(role)
        item['role'] = role
        start = scene.get('start_id') if isinstance(scene.get('start_id'), str) else None
        end = scene.get('end_id') if isinstance(scene.get('end_id'), str) else None
        window = []
        if start not in available or end not in available or positions.get(start, 0) > positions.get(end, -1):
            fail(path + '.source_window')
        else:
            window = [r['id'] for r in rows[positions[start]:positions[end] + 1]]
            if not set(window) <= available:
                fail(path + '.unprovided_window')
                window = []
        item.update(start_id=start, end_id=end, source_window_ids=window)
        selected = {start, end} & available
        for key in ('attempt_ids', 'response_ids', 'turn_ids', 'payoff_ids'):
            value = ids(scene.get(key), path + '.' + key, empty=item['verdict'] != 'effective')
            if not set(value) <= set(window):
                fail(path + '.' + key + '.outside_scene')
                item['_valid'] = False
            item[key] = value
            selected.update(value)
        if role == 'ending' and scope == 'full':
            band = plan['phases'].get('ending')
            if not band or not any(positions[band['start_id']] <= positions[k] <= positions[band['end_id']]
                                   for k in selected):
                fail(path + '.ending_range')
        item['evidence_ids'] = [k for k in ordered if k in selected]
        item['evidence'] = [deepcopy(bank[k]) for k in item['evidence_ids']]
        item['_valid'] = item['_valid'] and len(missing) == before
        normalized['core_scenes'].append(item)
        finding(item, path)
    for role in (('weakest_core', 'ending') if scope == 'full' else ('weakest_core',)):
        if roles.count(role) != 1:
            fail('core_scenes.' + role)
    before = len(missing)
    item = judgment(raw.get('repetition'), 'repetition')
    if item is not None:
        source = raw['repetition']
        earlier = ids(source.get('earlier_ids'), 'repetition.earlier_ids')
        later = ids(source.get('later_ids'), 'repetition.later_ids')
        if earlier and later and (max(positions[k] for k in earlier) >= min(positions[k] for k in later)) and len(available) > 1:
            fail('repetition.order')
            item['_valid'] = False
        item.update(earlier_ids=earlier, later_ids=later,
                    evidence_ids=[k for k in ordered if k in set(earlier + later)])
        item['_valid'] = item['_valid'] and len(missing) == before
        normalized['repetition'] = bound(item)
        finding(item, 'repetition')
    return {'protocol': PROTOCOL, 'validation_version': VALIDATION_VERSION,
            'scope': scope, 'source_hash': digest(body),
            'provided_ids': ordered, 'provided_ids_hash': digest(ordered),
            'coverage_plan': plan, 'coverage_complete': not missing, 'missing': missing,
            'audit': normalized, 'findings': findings, 'initial_copyedit_reminders': reminders}


def source_anchors(normalized):
    """Neutral original IDs only; earlier verdicts never become final facts."""
    if not isinstance(normalized, dict):
        return []
    audit = normalized.get('audit', {})
    groups = audit.get('coverage', []) + audit.get('core_scenes', []) + audit.get('chapter_handoffs', [])
    groups += [x for x in (audit.get('mainline'), audit.get('weakest_middle'), audit.get('repetition'))
               if isinstance(x, dict)]
    ids = set()
    for row in groups:
        ids.update(row.get('evidence_ids', []))
        ids.update(row.get('counterevidence_ids', []))
        ids.update(row.get('source_window_ids', []))
        comparison = row.get('compression') or {}
        ids.update(comparison.get('preserved_fact_ids', []))
        ids.update(comparison.get('choice_response_ids', []))
    return [k for k in normalized.get('provided_ids', []) if k in ids]


def final_gate(final, body, reading_results):
    result = deepcopy(final)
    records = [x.get('narrative_audit') for x in reading_results]
    current = result.get('narrative_audit')
    missing, provisional = [], []
    for index, record in enumerate(records + [current]):
        if (not isinstance(record, dict) or record.get('protocol') != PROTOCOL
                or record.get('validation_version') != VALIDATION_VERSION
                or record.get('source_hash') != digest(body)
                or record.get('provided_ids_hash') != digest(record.get('provided_ids'))):
            missing.append(f'reading[{index}].binding')
        elif not record['coverage_complete']:
            for key in record['missing']:
                # A local reader can honestly need later context. The same
                # editor's full final reading owns that judgment; omitted
                # schema/source coverage can never be replaced this way.
                if index < len(records) and record.get('scope') == 'chunk' and key.endswith('.uncertain'):
                    provisional.append({'reading': index, 'path': key})
                else:
                    missing.append(f'reading[{index}].{key}')
    if not isinstance(current, dict) or current.get('scope') != 'full':
        missing.append('final.full_scope')
    if isinstance(current, dict):
        provided = set(current.get('provided_ids', []))
        for index, record in enumerate(records):
            if not set(source_anchors(record)) <= provided:
                missing.append(f'final.reading[{index}].unprovided_narrative_anchors')
    delivered = {key for record in records if isinstance(record, dict) for key in record.get('provided_ids', [])}
    if delivered != {row['id'] for row in _rows(body)}:
        missing.append('reading.full_source_coverage')
    findings = deepcopy(current.get('findings', [])) if isinstance(current, dict) else []
    result.setdefault('initial_copyedit_reminders', []).extend(deepcopy(item)
        for record in records + [current] if isinstance(record, dict)
        for item in record.get('initial_copyedit_reminders', []))
    for item in findings:
        if item not in result['issues']:
            result['issues'].append(item)
    raw = result['recommendation']
    if any(i['severity'] == 'blocker' for i in result['issues']):
        result['recommendation'] = 'revise'
    elif missing:
        result['recommendation'] = 'uncertain'
    result['narrative_gate'] = {'protocol': PROTOCOL, 'validation_version': VALIDATION_VERSION,
        'source_hash': digest(body),
        'model_recommendation': raw, 'status': 'thin' if findings else 'incomplete' if missing else 'complete',
        'missing': missing, 'coverage_complete': not missing,
        'provisional_uncertainties': provisional, 'reading_count': len(records),
        'meaning': 'source-bound coverage and recorded editorial judgments; not measured narrative quality'}
    return result
