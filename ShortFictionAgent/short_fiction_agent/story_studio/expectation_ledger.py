"""Sequential expectation reading by the same chief editor.

Source/order validation is distinct from semantic judgment. The shared chief
continuation owns invocation and immutable receipt replay.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from .contracts import StoryError, digest, canonical_json
from . import submission_guide, editorial_priority, target_reader

PROTOCOL = 'source-bound-expectation-lifecycle-v3-original-reader-experience'
VALIDATION_VERSION = 'chronological-window-and-receipt-chain-v3-original-reader-experience'
DEFAULT_READING_PROMPT_CHARS = 24000
INSTRUCTION = editorial_priority.INSTRUCTION + target_reader.REVIEW_INSTRUCTION + '''你是同一位主审编辑，现在只做一次窄范围的叙事阅读账本，不做事实核查、数额核算、文笔评分或总审稿清单。
仅凭本次给出的作者brief、完整paragraphs和投稿指南语义要求阅读。brief是作者承诺，不能替正文补写问题或事实。先记录实际阅读顺序和原段证据，再判断区间的阅读价值，最后才判断整体；不得先定好稿或坏稿再找证据。

1. questions只登记实际标题、导语与正文建立的主要阅读问题，结合original_entry_context写这类读者在等什么。可以是结果、如何做到、为什么、人物怎样选择/回应、代价或关系理解；“发财、恋爱、逆袭”“后面会怎样”不是具体问题。入场已建立的能力收益、主动追求或其他题材期待，不能因方案改写主问题而漏记，也不能把作者brief里尚未写出的承诺虚构为已打开的问题。不要把每个动作拆成一个问题，也不要把任何尚未做完的手续自动登记为期待。
2. events按原文顺序登记问题的提出、答案预告、态度确认、部分回答、实际解决、重新打开。type只能为opened、answer_preannounced、attitude_confirmed、partly_resolved、resolved、reopened。每项先给evidence_ids，再写reader_now_knows，说明读者到这里到底已经知道什么、还不知道什么。每个事件的最后一个原文证据段必须是该变化实际发生的段落，程序以evidence_ids中原文位置最靠后的段为事件生效段；不能引用更晚的兑现或亮点来挪动生效时刻。明确预告或态度确认不能漏记，也不能冒充实际兑现；它是否削弱某个问题，需在后面的区间结合正文判断。一个结果被预告后，已经成立的“怎么做到/付出什么/为何如此/如何选择”可以继续有效，不必等待后来才重新提出。
3. intervals划分成连续且不重叠的区间，覆盖全部paragraphs。每个事件生效段须单独成为一个区间，同一段的多个事件合用该区间；生效段之前与之后各自判断，因此不能把新问题实际提出段的亮点倒填给它前面的空档。其余部分按实际阅读价值自然划分，可以把同一状态下的连续推进保留为一个区间。不按固定章节、字数或强制数量，也不为了制造切点编造事件。不能把一个问题已回答之后的漫长区间，与远处才出现的新问题合并成一句“关系逐渐变化”而跳过中间。引用active_question_ids时，问题须在区间开始前已经打开，或正在本区间的事件段被提出/重开；resolved事件段可以保留正在兑现的旧问题，但从其后区间开始，该问题不得继续active，除非已发生reopened。新建立的具体how/choice/cost问题使用自己的question_id。answer_preannounced和attitude_confirmed不自动等于resolved，不机械关闭有效的方法、代价或选择期待。此前已知的答案和承诺应在known_at_start说明。
4. 每个区间先写local_evidence_ids及reading_value，具体判断它给原始受众带来的新体验：是否推进仍有效的入场问题，或提供值得读的欲望、主动接招、偏爱、关系回应或必要过渡。再列本区间的counterevidence_ids及反证说明，最后给verdict=effective/thin/uncertain及reason。用于证明本区间价值或反驳问题的证据，全部必须在该区间内；可以在known_at_start说明更早建立的上下文，但尾部亮点不能为中段空档补证。资源、场所、任务和关系阶段不同，不证明同功能展开值得重读；反复解释如何尊重或放手，不因人物成长就自动有效。优势或能力退出也不是收益兑现，须判断余下体验是否接住入场欲望；不要求用完能力次数或强加无关支线。若正文确有不同收益、接招或具体温情，也不能因为终点已知便抹掉。
5. 保护结果前置导语、倒叙、方法与代价驱动的写法。安静关照、共同选择、职业交锋、必要过渡和结局回报都可能有效，不强制每段新灾难、反转或未知结果；结局可以解决问题，不必再制造新钩子。最弱处不一定thin，全文也不一定有thin；无法确认用uncertain，不能猜测。
6. overall只能依据上面的按序账本和区间作结论；说明最弱区间、真正有据的反证及局限。某处thin不等于整篇主线必然不清晰；某处effective也不证明全篇持续有期待。intervals及overall可含editorial_priority、reading_effect，保留实际verdict；copyedit_detail只留初始提醒，不能要求解决或因thin/uncertain最终阻断，连续报账拖延人物交锋仍是reading_core。字段齐全、结论revise或通过校验都不证明语义判断正确。

输出容量要求：只登记主要阅读问题的实质状态变化，普通对白、动作和每次执行不自动增加事件；不能为了缩短输出漏掉真正的提前告知或解决。每项reader_now_knows、known_at_start、reading_value、counterevidence、reason通常各用一句简明说明，尽量80字以内；相同上下文不要反复抄写，证据用ID定位。此为表达指导，不能截断原文证据或已完成内容来凑长度。
只返回一个完整单行紧凑JSON对象，不缩进、不换行、不加Markdown代码围栏，严格使用给定response_schema。不要输出额外核验表或补充故事。'''


def _text():
    return {'type': 'string', 'minLength': 1}


def _ids(*, empty=False):
    return {'type': 'array', 'items': {'type': 'string', 'pattern': '^p[0-9]+$'},
            'uniqueItems': True, 'minItems': 0 if empty else 1}


def _object(properties):
    return {'type': 'object', 'additionalProperties': False,
            'required': list(properties), 'properties': properties}


def _judgment_object(properties):
    schema = _object(properties)
    schema['properties'].update({
        'editorial_priority': {'enum': ['reading_core', 'copyedit_detail']},
        'reading_effect': _text(),
    })
    return schema


RESPONSE_SCHEMA = _object({
    'protocol': {'const': PROTOCOL},
    'questions': {'type': 'array', 'items': _object({
        'question_id': _text(), 'question': _text(),
    })},
    'events': {'type': 'array', 'items': _object({
        'event_id': _text(), 'question_id': _text(),
        'evidence_ids': _ids(),
        'type': {'enum': ['opened', 'answer_preannounced', 'attitude_confirmed',
                          'partly_resolved', 'resolved', 'reopened']},
        'reader_now_knows': _text(),
    })},
    'intervals': {'type': 'array', 'minItems': 1, 'items': _judgment_object({
        'interval_id': _text(), 'start_id': _text(), 'end_id': _text(),
        'known_at_start': _text(),
        'active_question_ids': {'type': 'array', 'items': _text(), 'uniqueItems': True},
        'local_evidence_ids': _ids(), 'reading_value': _text(),
        'counterevidence_ids': _ids(empty=True), 'counterevidence': _text(),
        'verdict': {'enum': ['effective', 'thin', 'uncertain']}, 'reason': _text(),
    })},
    'overall': _judgment_object({
        'verdict': {'enum': ['effective', 'thin', 'uncertain']},
        'weakest_interval_id': _text(), 'reason': _text(),
        'counterevidence': _text(), 'limitations': _text(),
    }),
})


def paragraphs(body):
    if not isinstance(body, str) or not body.strip():
        raise ValueError('The experiment requires a complete nonempty source')
    return [{'id': f'p{i}', 'text': match.group()}
            for i, match in enumerate(match for match in re.finditer(r'[^\n]+', body)
                                      if match.group().strip())]


def _full_prompt(body, brief, guide_context):
    payload = {'protocol': PROTOCOL, 'brief': deepcopy(brief),
               'original_entry_context': target_reader.entry_context(brief, include_author_promise=True),
               'paragraphs': paragraphs(body), 'submission_guide': deepcopy(guide_context),
               'response_schema': RESPONSE_SCHEMA}
    return INSTRUCTION + '\n' + json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def validate_response(response, body, *, ledger_plan=None):
    """Reject unbound/incomplete chronology, without manufacturing quality labels."""
    rows = paragraphs(body)
    positions = {row['id']: i for i, row in enumerate(rows)}
    if ledger_plan is None:
        lo_source, hi_source, carry = 0, len(rows) - 1, empty_carry()
    else:
        _check_plan(ledger_plan, body)
        lo_source = positions[ledger_plan['paragraph_ids'][0]]
        hi_source = positions[ledger_plan['paragraph_ids'][-1]]
        carry = ledger_plan['carry']
    errors = []

    def check(condition, path):
        if not condition:
            errors.append(path)
        return condition

    def validate_schema(value, schema, path):
        if 'const' in schema:
            check(value == schema['const'], path)
        if 'enum' in schema:
            check(value in schema['enum'], path)
        kind = schema.get('type')
        if kind == 'object':
            if not check(isinstance(value, dict), path):
                return
            check(set(schema['required']) <= set(value) <= set(schema['properties']), path + '.keys')
            for key, child in schema['properties'].items():
                if key in value:
                    validate_schema(value[key], child, path + '.' + key)
        elif kind == 'array':
            if not check(isinstance(value, list), path):
                return
            check(len(value) >= schema.get('minItems', 0), path + '.length')
            if schema.get('uniqueItems'):
                check(len({json.dumps(item, sort_keys=True) for item in value}) == len(value),
                      path + '.unique')
            for i, item in enumerate(value):
                validate_schema(item, schema['items'], f'{path}[{i}]')
        elif kind == 'string':
            if not check(isinstance(value, str) and bool(value.strip()), path):
                return
            if 'pattern' in schema:
                check(bool(re.fullmatch(schema['pattern'], value)), path)

    validate_schema(response, RESPONSE_SCHEMA, 'response')
    if errors:
        raise StoryError('Expectation ledger schema invalid: ' + ', '.join(errors))
    for item in response['intervals'] + [response['overall']]:
        editorial_priority.normalize(item)
    all_questions = carry['questions'] + response['questions']
    questions = {item['question_id']: item for item in all_questions}
    check(len(questions) == len(all_questions), 'questions.unique_question_id')
    events = carry['events'] + response['events']
    event_ids = [event['event_id'] for event in events]
    check(len(set(event_ids)) == len(event_ids), 'events.unique_event_id')
    opened_at, previous_end = {}, -1
    state_events, required_cuts = {}, {0, len(rows)}

    def bound_ids(ids, path, lo=0, hi=None):
        hi = len(rows) - 1 if hi is None else hi
        check(all(key in positions and lo <= positions[key] <= hi for key in ids), path)
        return [positions[key] for key in ids if key in positions]

    for index, event in enumerate(events):
        path = f'events[{index}]'
        qid = event['question_id']
        check(qid in questions, path + '.question_id')
        values = bound_ids(event['evidence_ids'], path + '.evidence_ids',
            0 if index < len(carry['events']) else lo_source,
            lo_source - 1 if index < len(carry['events']) else hi_source)
        if not values:
            continue
        end = max(values)
        check(end >= previous_end, path + '.chronology')
        previous_end = end
        if index >= len(carry['events']):
            required_cuts.update((end, end + 1))
        state_events.setdefault(qid, []).append((end, event['type']))
        if event['type'] == 'opened':
            check(qid not in opened_at, path + '.duplicate_opened')
            opened_at.setdefault(qid, end)
        else:
            check(qid in opened_at and opened_at[qid] <= end, path + '.question_not_opened')
    check(set(questions) == set(opened_at), 'questions.source_opened_event')

    cursor, interval_ids = lo_source, []
    for index, interval in enumerate(response['intervals']):
        path = f'intervals[{index}]'
        interval_ids.append(interval['interval_id'])
        lo, hi = positions.get(interval['start_id']), positions.get(interval['end_id'])
        if not check(lo is not None and hi is not None and lo == cursor and hi >= lo,
                     path + '.continuous_source_range'):
            continue
        check(not any(lo < cut < hi + 1 for cut in required_cuts),
              path + '.event_state_boundary_swallowed')
        bound_ids(interval['local_evidence_ids'], path + '.local_evidence_ids', lo, hi)
        bound_ids(interval['counterevidence_ids'], path + '.counterevidence_ids', lo, hi)
        for qid in interval['active_question_ids']:
            active_at_start = False
            newly_opened_here = False
            for point, kind in state_events.get(qid, []):
                if point < lo:
                    if kind in {'opened', 'reopened'}:
                        active_at_start = True
                    elif kind == 'resolved':
                        active_at_start = False
                elif point <= hi and kind in {'opened', 'reopened'}:
                    newly_opened_here = True
            check(qid in opened_at and (active_at_start or newly_opened_here),
                  path + '.future_or_resolved_question_reference')
        cursor = hi + 1
    check(cursor == hi_source + 1, 'intervals.full_source_coverage')
    check(not set(interval_ids).intersection(carry['interval_ids']), 'intervals.previously_used_id')
    check(len(set(interval_ids)) == len(interval_ids), 'intervals.unique_interval_id')
    check(response['overall']['weakest_interval_id'] in interval_ids,
          'overall.weakest_interval_id')
    if errors:
        raise StoryError('Expectation ledger source binding invalid: ' + ', '.join(errors))
    normalized = deepcopy(response)
    normalized['binding'] = {
        'body_sha256': hashlib.sha256(body.encode('utf-8')).hexdigest(),
        'paragraph_count': hi_source - lo_source + 1,
        'paragraph_ids': [row['id'] for row in rows[lo_source:hi_source + 1]],
        'plan_hash': ledger_plan['plan_hash'] if ledger_plan is not None else None,
        'scope': 'chronological_narrative_component; not_semantic_quality_certificate',
        'validation_limit': 'schema_and_source_order_only; independent_semantic_assessment_required',
    }
    return normalized


def criteria_hash():
    return digest({'protocol': PROTOCOL, 'validation_version': VALIDATION_VERSION,
        'instruction': INSTRUCTION, 'schema': RESPONSE_SCHEMA,
        'reading_prompt_chars': DEFAULT_READING_PROMPT_CHARS,
        'submission_guide': submission_guide.criteria_hash()})


def empty_carry():
    # No previous verdict, issue or praise enters the next narrow reading turn.
    return {'questions': [], 'events': [], 'interval_ids': []}


def _body_hash(body):
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _check_plan(plan, body):
    if not isinstance(plan, dict) or plan.get('plan_hash') != digest({
            key: value for key, value in plan.items() if key not in {'plan_hash', 'prompt'}}):
        raise StoryError('期待账本计划绑定损坏')
    rows = paragraphs(body)
    positions = {row['id']: i for i, row in enumerate(rows)}
    ids = plan.get('paragraph_ids')
    if (plan.get('protocol') != PROTOCOL or plan.get('criteria_hash') != criteria_hash()
            or plan.get('body_sha256') != _body_hash(body)
            or not isinstance(ids, list) or not ids or any(key not in positions for key in ids)
            or ids != [row['id'] for row in rows[positions[ids[0]]:positions[ids[-1]] + 1]]
            or plan.get('carry_hash') != digest(plan.get('carry'))):
        raise StoryError('期待账本原文、顺序、规则或前序记录不匹配')
    carry = plan.get('carry')
    if (not isinstance(carry, dict) or set(carry) != {'questions', 'events', 'interval_ids'}
            or any(not isinstance(carry[key], list) for key in carry)):
        raise StoryError('期待账本前序记录结构不匹配')


def _render(body, contract, guide, rows, *, carry, reviewer_id, parent_receipt_hash,
            planned_reader_promise, is_final):
    bank = {row['id']: row for row in paragraphs(body)}
    anchor_ids = {key for event in carry['events'] for key in event['evidence_ids']}
    source_history = [deepcopy(bank[key]) for key in sorted(anchor_ids, key=lambda key: int(key[1:]))]
    omitted = {'market_guidance', 'market_basis', 'publication_guidance'}
    payload = {'protocol': PROTOCOL, 'reviewer_id': reviewer_id,
        'brief': {key: deepcopy(value) for key, value in contract.items() if key not in omitted},
        'original_entry_context': target_reader.entry_context(contract, include_author_promise=True),
        'planned_reader_promise': deepcopy(planned_reader_promise),
        'paragraphs': deepcopy(rows), 'submission_guide': deepcopy(guide),
        'source_history': source_history, 'carry': deepcopy(carry),
        'parent_receipt_hash': parent_receipt_hash, 'body_sha256': _body_hash(body),
        'reading_scope': {'first_id': rows[0]['id'], 'last_id': rows[-1]['id'],
            'is_first': rows[0]['id'] == 'p0', 'is_final': is_final},
        'response_schema': RESPONSE_SCHEMA}
    instruction = INSTRUCTION + (
        '本次是同一主审按顺序阅读的一回。paragraphs是本回完整连续正文，source_history仅是此前事件的原段锚点，'
        'carry是已保存前序账本而非已证实的语义结论。不得把后读信息倒填到此前读者知情中。'
        '只输出本回新增questions/events/intervals；已有question_id可以在新事件和active_question_ids中引用，'
        '不能重定义或重输出旧问题、事件或区间。新ID不得与carry内旧ID重复。'
        '本回intervals只覆盖paragraphs完整范围，证据须来自本回；known_at_start可说明此前已读上下文。'
        'overall仅判断本回给出的范围；未到稿尾不能声称全文问题已解决。'
        'source_history不属于本回区间，不可用它或未给出的未来原段证明本回阅读价值。'
        'original_entry_context保留原始受众及作者承诺；实际入场问题仍须标题、导语和正文原段证明。'
        'planned_reader_promise只是方案对原始承诺的解释，不能覆盖原始基准或当作正文已经建立的问题。'
        '本阶段只用投稿指南的语义要求；格式、资格和数额计算留在其他核验阶段。')
    return instruction + '\n' + canonical_json(payload)


def next_plan(body, contract, *, start=0, carry=None, parent_receipt_hash=None,
              reviewer_id='chief-editor', max_prompt_chars=100000,
              planned_reader_promise=None, reading_prompt_chars=DEFAULT_READING_PROMPT_CHARS):
    """Fit source plus real carry and reserve a bounded reading/output workload."""
    if type(max_prompt_chars) is not int or max_prompt_chars < 2000:
        raise StoryError('期待账本上下文容量不足或无效')
    if type(reading_prompt_chars) is not int or reading_prompt_chars < 2000:
        raise StoryError('期待账本分段目标容量不足或无效')
    reading_limit = min(max_prompt_chars, reading_prompt_chars)
    rows = paragraphs(body)
    if type(start) is not int or not 0 <= start < len(rows):
        raise StoryError('期待账本连续阅读起点无效')
    carry = empty_carry() if carry is None else deepcopy(carry)
    guide = submission_guide.submission_guide_context(contract, 'review')
    if not guide['enabled']:
        raise StoryError('当前投稿路径未启用期待账本')
    if (start == 0) != (parent_receipt_hash is None):
        raise StoryError('期待账本父回执链与阅读起点不匹配')

    def render(stop):
        return _render(body, contract, guide, rows[start:stop], carry=carry,
            reviewer_id=reviewer_id, parent_receipt_hash=parent_receipt_hash,
            planned_reader_promise=planned_reader_promise, is_final=stop == len(rows))

    full = render(len(rows))
    if len(full) <= reading_limit:
        stop, rendered = len(rows), full
    else:
        low, high, stop, rendered = start + 1, len(rows), None, None
        while low <= high:
            candidate = (low + high) // 2
            trial = render(candidate)
            if len(trial) <= reading_limit:
                stop, rendered, low = candidate, trial, candidate + 1
            else:
                high = candidate - 1
        if stop is None:
            whole_paragraph = render(start + 1)
            if len(whole_paragraph) > max_prompt_chars:
                raise StoryError('单段正文及已验证期待账本超过容量；保留回执，不能截断或跳过')
            # A reading target is a workload preference, never permission to
            # split a paragraph or discard verified history.
            stop, rendered = start + 1, whole_paragraph
    plan = {'protocol': PROTOCOL, 'criteria_hash': criteria_hash(),
        'body_sha256': _body_hash(body), 'contract_hash': digest(contract),
        'guide_hash': digest(guide), 'reviewer_id': reviewer_id,
        'planned_reader_promise_hash': digest(planned_reader_promise),
        'paragraph_ids': [row['id'] for row in rows[start:stop]],
        'carry': carry, 'carry_hash': digest(carry),
        'parent_receipt_hash': parent_receipt_hash, 'max_prompt_chars': max_prompt_chars,
        'reading_prompt_chars': reading_prompt_chars, 'reading_limit_chars': reading_limit,
        'prompt_hash': digest(rendered), 'is_final': stop == len(rows)}
    plan['plan_hash'] = digest(plan)
    plan['prompt'] = rendered
    return plan


def run_ledger(body, contract, *, stage, reviewer_id='chief-editor', max_prompt_chars=100000,
               task_prefix='chief_editor', planned_reader_promise=None,
               reading_prompt_chars=DEFAULT_READING_PROMPT_CHARS):
    """Shared deterministic live/replay driver; stage owns raw transport receipts."""
    guide = submission_guide.submission_guide_context(contract, 'review')
    if not guide['enabled']:
        return {'ledger_receipts': [], 'current_ledger': None, 'ledger_plan_hash': None}
    rows, start, carry, parent = paragraphs(body), 0, empty_carry(), None
    receipts, plan_hashes, parts = [], [], []
    while start < len(rows):
        plan = next_plan(body, contract, start=start, carry=carry, parent_receipt_hash=parent,
            reviewer_id=reviewer_id, max_prompt_chars=max_prompt_chars,
            planned_reader_promise=planned_reader_promise, reading_prompt_chars=reading_prompt_chars)
        normalized, receipt = stage(task_prefix + '_expectation_ledger', plan['prompt'],
            plan['paragraph_ids'], ledger_plan=plan)
        # Re-derive carry only from the actual saved raw answer, never a caller's
        # invented normalized result. The outer stage binds its full input hash.
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('response'), dict)
                or receipt.get('response_hash') != digest(receipt['response'])):
            raise StoryError('期待账本实际模型回执缺失或响应绑定损坏')
        checked = validate_response(receipt['response'], body, ledger_plan=plan)
        if checked != normalized:
            raise StoryError('期待账本规范化结果与实际回执不一致')
        receipts.append({'plan_hash': plan['plan_hash'], 'paragraph_ids': plan['paragraph_ids'],
                         'parent_receipt_hash': parent, **deepcopy(receipt)})
        plan_hashes.append(plan['plan_hash'])
        parts.append(checked)
        carry = {'questions': carry['questions'] + deepcopy(checked['questions']),
            'events': carry['events'] + deepcopy(checked['events']),
            'interval_ids': carry['interval_ids'] + [item['interval_id'] for item in checked['intervals']]}
        parent = digest(receipt)
        start += len(plan['paragraph_ids'])
    current = {'protocol': PROTOCOL, 'criteria_hash': criteria_hash(),
        'body_sha256': _body_hash(body), 'contract_hash': digest(contract),
        'guide_hash': digest(guide), 'reviewer_id': reviewer_id,
        'questions': carry['questions'], 'events': carry['events'],
        'intervals': [deepcopy(item) for part in parts for item in part['intervals']],
        'reading_judgments': [deepcopy(part['overall']) for part in parts],
        'paragraph_ids': [key for receipt in receipts for key in receipt['paragraph_ids']],
        'coverage_complete': start == len(rows), 'receipt_chain_hash': digest(receipts),
        'meaning': 'source-ordered reading judgments; field completion is not semantic quality proof'}
    return {'ledger_receipts': receipts, 'current_ledger': current,
            'ledger_plan_hash': digest(plan_hashes)}


def findings(current_ledger, body):
    """Keep adverse interval judgments actionable in the existing repair route."""
    if current_ledger is None:
        return []
    if (current_ledger.get('body_sha256') != _body_hash(body)
            or current_ledger.get('protocol') != PROTOCOL
            or current_ledger.get('criteria_hash') != criteria_hash()
            or current_ledger.get('coverage_complete') is not True
            or current_ledger.get('paragraph_ids') != [row['id'] for row in paragraphs(body)]):
        raise StoryError('期待账本未完整绑定当前正文和规则')
    matches = [match for match in re.finditer(r'[^\n]+', body) if match.group().strip()]
    bank = {f'p{i}': {'id': f'p{i}', 'text': match.group(),
                      'span': [match.start(), match.end()]}
            for i, match in enumerate(matches)}
    result = []
    for interval in current_ledger['intervals']:
        if interval['verdict'] != 'thin' or editorial_priority.is_detail(interval):
            continue
        keys = interval['local_evidence_ids']
        issue = {'severity': 'blocker', 'origin': 'narrative_audit',
            'narrative_source': 'expectation_ledger', 'scope': 'local', 'repair_scope': 'local',
            'interval_id': interval['interval_id'],
            'source_range': [interval['start_id'], interval['end_id']],
            'reason': interval['reason'],
            'fix_intent': '依据原区间恢复实际有效的问题、方法、人物选择或独有体验，保留有据的安静回应。',
            'evidence_ids': deepcopy(keys), 'evidence': [deepcopy(bank[key]) for key in keys],
            **editorial_priority.fields(interval)}
        issue['issue_id'] = 'expectation-' + digest({'body': _body_hash(body), 'issue': issue})[:24]
        result.append(issue)
    return result


def final_gate(final, body, current_ledger, *, contract=None):
    """An unaddressed thin/uncertain ledger cannot be overwritten by ready."""
    if current_ledger is None:
        if contract is not None and submission_guide.submission_guide_context(contract, 'review')['enabled']:
            raise StoryError('适用投稿指南的当前正文缺少期待账本')
        return deepcopy(final)
    adverse = findings(current_ledger, body)
    result, missing, retained = deepcopy(final), [], []
    positions = {row['id']: i for i, row in enumerate(paragraphs(body))}
    source_rows = {row['id']: row for row in paragraphs(body)}
    reminders = []
    for item in current_ledger['intervals']:
        if editorial_priority.is_detail(item) and item['verdict'] in {'thin', 'uncertain'}:
            reminders.append({**deepcopy(item), 'origin': 'expectation_ledger',
                'source_hash': _body_hash(body),
                'evidence': [deepcopy(source_rows[key]) for key in item['local_evidence_ids']]})
    for index, item in enumerate(current_ledger['reading_judgments']):
        if editorial_priority.is_detail(item) and item['verdict'] in {'thin', 'uncertain'}:
            weakest = next(row for row in current_ledger['intervals']
                           if row['interval_id'] == item['weakest_interval_id'])
            reminders.append({**deepcopy(item), 'origin': 'expectation_ledger',
                'judgment_index': index, 'source_hash': _body_hash(body),
                'evidence': [deepcopy(source_rows[key]) for key in weakest['local_evidence_ids']]})
    raw_dispositions = result.get('expectation_dispositions', [])
    if not isinstance(raw_dispositions, list):
        missing.append('expectation_dispositions')
        raw_dispositions = []
    dispositions = {}
    known = {item['issue_id'] for item in adverse}
    for item in raw_dispositions:
        if not isinstance(item, dict) or item.get('issue_id') not in known:
            missing.append('expectation_dispositions.unknown_issue')
        elif item['issue_id'] in dispositions:
            missing.append(item['issue_id'] + '.duplicate')
        else:
            dispositions[item['issue_id']] = item
    for issue in adverse:
        disposition = dispositions.get(issue['issue_id'])
        explained = False
        if not disposition:
            missing.append(issue['issue_id'] + '.unanswered')
        if disposition and (disposition.get('status') == 'context_explained'
                            or editorial_priority.is_detail(disposition)):
            lo, hi = (positions[key] for key in issue['source_range'])
            ids, reason = disposition.get('evidence_ids'), disposition.get('reason')
            source_bound = (disposition.get('status') in {'retained', 'context_explained', 'revision_resolved'}
                and isinstance(ids, list) and bool(ids) and all(isinstance(key, str) for key in ids)
                and len(ids) == len(set(ids))
                and all(key in positions and lo <= positions[key] <= hi for key in ids)
                and isinstance(reason, str) and bool(reason.strip()))
            if not source_bound:
                missing.append(issue['issue_id'] + '.outside_interval_or_missing_explanation')
            elif editorial_priority.is_detail(disposition):
                # The same editor may classify an earlier technical objection
                # as optional without asserting that it was fixed or disproved.
                reminders.append({**deepcopy(issue), **editorial_priority.fields(disposition),
                    'disposition': deepcopy(disposition), 'status': 'retained',
                    'source_hash': _body_hash(body)})
                explained = True  # Closed only for final priority, not truth.
            else:
                explained = True
        elif disposition and disposition.get('status') == 'revision_resolved':
            missing.append(issue['issue_id'] + '.same_source_version')
        if not explained:
            retained.append(deepcopy(issue))
    result.setdefault('issues', []).extend(retained)
    result.setdefault('initial_copyedit_reminders', []).extend(reminders)
    uncertain = [item['interval_id'] for item in current_ledger['intervals']
                 if item['verdict'] == 'uncertain' and not editorial_priority.is_detail(item)]
    uncertain.extend(f'overall:{index}' for index, item in enumerate(current_ledger['reading_judgments'])
        if not editorial_priority.is_detail(item) and (item['verdict'] == 'uncertain' or (item['verdict'] == 'thin' and not any(
            interval['verdict'] == 'thin' for interval in current_ledger['intervals']
            if interval['interval_id'] == item['weakest_interval_id']))))
    if retained or any(item.get('severity') == 'blocker' for item in result['issues']):
        result['recommendation'] = 'revise'
    elif uncertain or missing:
        result['recommendation'] = 'uncertain'
    result['expectation_gate'] = {'protocol': PROTOCOL, 'body_sha256': _body_hash(body),
        'coverage_complete': True, 'retained_issue_ids': [item['issue_id'] for item in retained],
        'uncertain_intervals': uncertain, 'missing': missing,
        'initial_copyedit_reminder_count': len(reminders),
        'nonblocking_detail_interval_ids': [item['interval_id'] for item in current_ledger['intervals']
            if editorial_priority.is_detail(item)],
        'complete': not uncertain and not missing,
        'meaning': 'current source-bound core judgment closure; optional raw verdicts retained; not measured reader performance'}
    return result
