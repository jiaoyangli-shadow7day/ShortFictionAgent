"""Source-bound critical claims and one bounded reread by the same editor.

The program verifies provenance, complete context and unresolved states. It does
not decide literary truth from keywords or replace the editor's semantic work.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .contracts import StoryError, canonical_json, digest
from . import editorial_priority

PROTOCOL = 'chief-claims-v6-reader-priority'
PLANNING_VERSION = 'separate-reading-and-evidence-budgets-v10'
CLAIM_VALIDATION_VERSION = 'full-source-recheck-v17'
INSTRUCTION = (
    editorial_priority.INSTRUCTION +
    '另返回claims和observations数组，没有相应内容也显式返回空数组。claims只记录影响关键行动或结局的'
    '可核对断言：prediction预测/承诺，cashflow实际资金归集，timeline同一事件的日期，constraint能力或条件限制。'
    '明确发生于故事结束之后、且非当前结局成立前提的愿望、日程或约会计划，不列为须在本篇兑现的'
    '关键claims；本篇已达成约定的事实可记observations，叙事审查仍核对当前共同决定与实际回应。'
    '作者要求在篇内兑现、当前关键行动或结局依赖其成立、或正文已作为完成事实使用的承诺，仍须核验，'
    '不能因台词使用未来时就排除，也不能把未来执行改成verified。'
    '被人物选择规避、正文未声称发生且作者不要求发生的条件后果，不列为本篇待完成的结果，'
    '不要求先触发坏结果才能审稿完成；仍核对现实的威胁、政策或能力信息、来源与权限、处理意图及决策依据。'
    '作者要求实际发生、正文声称已发生或结局依赖实际执行的条件后果，仍须登记核验；'
    '不能按若、如果或if字样自动豁免，也不能把未执行的后果写成verified。'
    '次要算账或手续不必登记为关键claims；若已发现并登记，须标copyedit_detail，仅保留初轮提醒，'
    '不要求完整回读或最终闭合。每条含editorial_priority、reading_effect、kind、subject(同一事件的具体对象)、statement、assertion_ids、condition_ids、process_ids、'
    'outcome_ids、status(verified/conflict/pending)、reason；可含search_terms，须是引用原文中的具体词。'
    'prediction另须comparison_scope，按原断言选outcome或process_and_outcome：只预测赢家等结果的'
    '核对原断言、必要条件和实际结果即可，不要求作者补比赛过程；承诺全程、路径或具体经过的须'
    'process_and_outcome，核对完整实际经过及结果。保留原限定，后续回读和汇总不能降维。'
    '四组ID分别绑定原断言、限定条件、实际经过、实际结果；无已见证据用空数组，不能拿结果冒充经过。'
    '已见完整实际事件时另给成对event_start_id/event_end_id，指该次比赛、签约或结算等事件真正起止，'
    '不能把早期承诺到终局之间的无关章节算作事件，也不能从后半程反超才算比赛开始。起止间全部原段'
    '必须确实读过；省略号、横线不是事件重新开始的证据。需核对经过但不能确认实际起点时保留pending。'
    '不把带条件的承诺改写成无条件成功。verified表示条件、经过和结果已实际对照，conflict须说明冲突，'
    '缺证或仍需前后文为pending；初读结论还须由程序安排的完整原文核验约束。'
    '角色吹牛、撒谎、计划落空若叙述明确解释，不是要求每句台词兑现；claims重点是正文作为已兑现'
    '能力、关键因果或结算证据使用的断言。失败已被合理解释也可以verified，表示核对成立而非预言必胜。'
    'observations每条含subject、role(condition/process/outcome)、statement、evidence_ids；保存与关键断言'
    '可能对应的实际事件，尤其尚未配到承诺的过程与结果。每项ID是一个实际输入ID，不重复、不猜测。'
    '普通生活留白、未发生的日常结算、预算与实际成本不同、现金与银行或不同主体的账户，不自动构成'
    '需闭合断言或冲突；先确认对象、口径、时间和实际流向相同。静态背景不必编造一次兑现事件。'
)
REREAD_INSTRUCTION = (
    editorial_priority.INSTRUCTION +
    '你仍是同一名独立主审，这是唯一一次定向回读，不是另一个审核身份。按claim_id逐一核对关键断言，'
    '非结局依赖且明确发生于故事结束之后的愿望或计划，不扩张成本篇已发生结果；叙事审查仍看当前'
    '共同决定与回应是否真实。已有claim若指向尚未发生的长期执行，仍须保留原断言与comparison_scope，'
    '缺证则pending；不能改成仅已达成约定后verified，也不能凭reason或ready豁免待核项。'
    '被选择规避且未声称发生、非作者要求的条件后果，不要求补演其执行；仍核对当前政策、威胁或能力的'
    '来源与权限、现实处理意图及决策依据。若已有claim登记的是实际执行，保留原断言、条件与comparison_scope，'
    '缺证仍pending，不能改弱为仅政策已陈述后verified。作者要求发生、正文声称发生或结局依赖执行的仍须核验，'
    '不能按条件句字样豁免。'
    '用完整原断言、限定条件及所给连续过程对照实际结果；不能只核对赢家、到账或成功标签。'
    '每条claim_checks含claim_id、status(verified/conflict/pending)、reason、process_ids、outcome_ids；'
    '必须覆盖全部claim_ids。full_context_claim_ids已提供完整跨度；其余若能在所给原文内确认实际事件'
    '完整起止，另给成对event_start_id/event_end_id，程序保留其间全部原段并核验，否则只能pending。'
    '沿用原comparison_scope，不能把需完整经过的断言降为只核结果；outcome只核原断言、必要条件与'
    '实际结果两端，不因未展示不必要的过程而pending。若返回comparison_scope必须与原值相同。'
    '起点是实际事件开始，不是省略号之后或仅发生反转的段落；不能只读后半程认证全程。'
    '保留原assertion/condition绑定，不能改成较弱的承诺。结果相同而限定经过相反仍可能conflict。'
    '收支须先区分本金/利润、应收/实收、预算/成本及账户归属；普通省略不自动判矛盾。'
    '同时返回recommendation和issues；这仍是唯一主审的编辑判断，不输出新正文或评分。'
)
KINDS = {'prediction', 'cashflow', 'timeline', 'constraint'}
ROLES = ('assertion', 'condition', 'process', 'outcome')
STATUSES = {'verified', 'conflict', 'pending'}
COMPARISON_SCOPES = {'outcome', 'process_and_outcome'}


def _bank(body):
    from .chief_editor import paragraphs
    rows = paragraphs(body)
    return rows, {row['id']: row for row in rows}


def _ids(value, available, *, empty=False):
    if (not isinstance(value, list) or (not value and not empty) or
            any(not isinstance(key, str) or key not in available for key in value) or
            len(value) != len(set(value))):
        raise StoryError('关键断言包含缺失、重复或本次未读的原文ID')
    return deepcopy(value)


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise StoryError('关键断言缺少' + field)
    return value


def _complete_ids(ids, rows):
    positions = {row['id']: i for i, row in enumerate(rows)}
    if not ids:
        return []
    left, right = min(positions[key] for key in ids), max(positions[key] for key in ids)
    return [row['id'] for row in rows[left:right + 1]]


def _has_comparison(claim):
    return bool(claim['assertion_ids'] and claim['outcome_ids'] and
                (claim['comparison_scope'] == 'outcome' or
                 claim['process_ids']))


def _event_window(raw, available, rows, event_anchors):
    """Bind an editor-declared actual event to its complete source interval."""
    start, end = raw.get('event_start_id'), raw.get('event_end_id')
    if start is None and end is None:
        return {}
    positions = {row['id']: i for i, row in enumerate(rows)}
    if (not isinstance(start, str) or not isinstance(end, str) or
            start not in available or end not in available or positions[start] > positions[end]):
        raise StoryError('实际事件起止须成对绑定本次原文ID且顺序正确')
    ids = [row['id'] for row in rows[positions[start]:positions[end] + 1]]
    covered = set(event_anchors) <= set(ids)
    return {'event_start_id': start, 'event_end_id': end, 'event_ids': ids,
            'event_anchor_coverage': covered,
            'event_context_complete': covered and set(ids) <= available}


def _boundary(text):
    text = text.strip()
    return bool(re.match(r'^第[\d一二三四五六七八九十百]+[章节回]', text) or
                (len(text) <= 60 and re.match(
                    r'^(?:\d{4}年|周[一二三四五六日天]|次日|翌日|当晚).*'
                    r'(?:[\d一二三四五六七八九十]+点|\d{1,2}:\d{2})[,， ]', text)))


def _event_ids(ids, rows, *, to_boundary=False):
    """Whole source scenes around actual events, separately from old promises.

    Strong scene headings are retrieval hints, never proof of an event's start.
    Weak separators cannot hide earlier action in the same actual event.
    """
    positions = {row['id']: i for i, row in enumerate(rows)}
    boundaries = [i for i, row in enumerate(rows) if _boundary(row['text'])]
    selected, groups = set(), {}
    for key in ids:
        index = positions[key]
        left = max((i for i in boundaries if i <= index), default=0)
        groups.setdefault(left, []).append(index)
    for left, anchors in groups.items():
        right = (min((i for i in boundaries if i > max(anchors)), default=len(rows))
                 if to_boundary else max(anchors) + 1)
        selected.update(row['id'] for row in rows[left:right])
    return [row['id'] for row in rows if row['id'] in selected]


def _assertion_ids(ids, rows):
    selected = set(ids)
    positions = {row['id']: i for i, row in enumerate(rows)}
    for key in ids:
        prior = next((row for row in reversed(rows[:positions[key] + 1])
                      if _boundary(row['text']) and not re.fullmatch(r'[.…\-—=*]+', row['text'].strip())), None)
        if prior:
            selected.add(prior['id'])
    return selected


def normalize_reading(response, body, allowed_ids=None):
    """Require explicit structured arrays and attach immutable whole paragraphs."""
    rows, bank = _bank(body)
    available = set(bank) if allowed_ids is None else set(allowed_ids)
    if not available <= set(bank):
        raise StoryError('关键断言输入含未知原文ID')
    if not isinstance(response.get('claims'), list) or not isinstance(response.get('observations'), list):
        raise StoryError('主审须显式返回claims和observations数组')
    claims, seen = [], set()
    for claim_index, raw in enumerate(response['claims']):
        if not isinstance(raw, dict) or raw.get('kind') not in KINDS or raw.get('status') not in STATUSES:
            raise StoryError('关键断言kind或status无效')
        item = {key: _text(raw.get(key), key) for key in ('subject', 'statement', 'reason')}
        item.update(kind=raw['kind'], status=raw['status'], source_hash=digest(body))
        item.update(editorial_priority.fields(raw))
        scope = raw.get('comparison_scope', 'outcome' if item['kind'] != 'prediction' else None)
        if scope not in COMPARISON_SCOPES or (item['kind'] != 'prediction' and scope != 'outcome'):
            raise StoryError('prediction须声明有效comparison_scope；其它断言按outcome核对')
        item['comparison_scope'] = scope
        for role in ROLES:
            item[role + '_ids'] = _ids(raw.get(role + '_ids'), available, empty=role != 'assertion')
        item['claim_id'] = 'claim-' + digest({key: item[key] for key in
            ('kind', 'subject', 'assertion_ids')})[:24]
        if raw.get('claim_id', item['claim_id']) != item['claim_id'] or item['claim_id'] in seen:
            raise StoryError('关键断言ID重复或冒认已有断言')
        seen.add(item['claim_id'])
        item['evidence'] = {role: [deepcopy(bank[key]) for key in item[role + '_ids']] for role in ROLES}
        terms = raw.get('search_terms', [])
        source_text = '\n'.join(row['text'] for evidence in item['evidence'].values() for row in evidence)
        if not isinstance(terms, list) or any(not isinstance(term, str) for term in terms):
            raise StoryError('关键断言检索词须为字符串数组')
        item['search_terms'], discarded = [], []
        for term in terms:
            reason = ('too_short' if len(term.strip()) < 2 else
                      'not_in_claim_evidence' if term not in source_text else
                      'duplicate' if term in item['search_terms'] else None)
            if reason:
                discarded.append({'term': term, 'reason': reason})
            else:
                item['search_terms'].append(term)
        if discarded:
            item['discarded_search_terms'] = discarded
        if 'resolution' in raw:
            resolution = raw['resolution']
            if (not isinstance(resolution, dict) or
                    resolution.get('supersedes_status') not in {'pending', 'conflict'}):
                raise StoryError('断言纠错须明确先前pending或conflict状态')
            resolution_ids = _ids(resolution.get('evidence_ids'), available)
            item['resolution'] = {
                'supersedes_status': resolution['supersedes_status'],
                'reason': _text(resolution.get('reason'), 'resolution.reason'),
                'evidence_ids': resolution_ids,
                'evidence': [deepcopy(bank[key]) for key in resolution_ids],
                'source_hash': digest(body)}
        if 'recheck' in raw:
            recheck = raw['recheck']
            if not isinstance(recheck, dict):
                raise StoryError('原文复核须明确理由及完整证据')
            recheck_ids = _ids(recheck.get('evidence_ids'), available)
            item['recheck'] = {'reason': _text(recheck.get('reason'), 'recheck.reason'),
                'evidence_ids': recheck_ids, 'evidence': [deepcopy(bank[key]) for key in recheck_ids],
                'raw_evidence_ids': deepcopy(recheck_ids),
                'source_hash': digest(body)}
        required_event = (item['outcome_ids'] if scope == 'outcome' else
                          item['process_ids'] + item['outcome_ids'])
        item.update(_event_window(raw, available, rows, required_event))
        item['event_evidence'] = [deepcopy(bank[key]) for key in item.get('event_ids', [])]
        all_ids = [key for role in ROLES for key in item[role + '_ids']]
        if item.get('event_ids'):
            # An old promise need not turn every intervening unrelated chapter
            # into the actual event. Both original anchors and the full event
            # remain mandatory source paragraphs.
            context = set(all_ids + item['event_ids'])
            item['context_ids'] = [row['id'] for row in rows if row['id'] in context]
        elif scope == 'outcome':
            item['context_ids'] = [row['id'] for row in rows if row['id'] in set(all_ids)]
        else:
            item['context_ids'] = _complete_ids(all_ids, rows)
        item['context_complete'] = (set(item['context_ids']) <= available and
                                   item.get('event_context_complete', True))
        item['verification_status'] = (item['status'] if _has_comparison(item) and
            item['context_complete'] and (scope == 'outcome' or available == set(bank) or
                item.get('event_context_complete') is True) else 'pending')
        if 'recheck' in item:
            recheck = item['recheck']
            primary = set(recheck['raw_evidence_ids'])
            normalizations = []
            # An explicit source interval declares every paragraph between its
            # bounds. Expand only a completely delivered interval, regardless of
            # whether its semantic event metadata covers every claimed outcome.
            if item.get('event_ids') and set(item['event_ids']) <= available:
                primary.update(item['event_ids'])
                normalizations.append({'kind': 'explicit_delivered_event_range',
                    'source_path': f'claims[{claim_index}].event_start_id/event_end_id',
                    'declared_range': [raw['event_start_id'], raw['event_end_id']],
                    'expanded_ids': deepcopy(item['event_ids']),
                    'delivered_source_ids': deepcopy(item['event_ids']), 'source_hash': digest(body)})
            # Do not use these additional role references to satisfy old-record
            # coverage. They only remove duplicate transcription of new explicit
            # references already selected by this same model response.
            combined = set(primary)
            for role in ROLES:
                for index, key in enumerate(item[role + '_ids']):
                    if key not in primary:
                        normalizations.append({'kind': 'same_claim_explicit_reference',
                            'source_path': f'claims[{claim_index}].{role}_ids[{index}]',
                            'paragraph_id': key, 'source_hash': digest(body)})
                        combined.add(key)
            recheck['old_binding_evidence_ids'] = [row['id'] for row in rows if row['id'] in primary]
            recheck['evidence_ids'] = [row['id'] for row in rows if row['id'] in combined]
            recheck['evidence'] = [deepcopy(bank[key]) for key in recheck['evidence_ids']]
            recheck['citation_normalizations'] = normalizations
        claims.append(item)
    observations = []
    for raw in response['observations']:
        if not isinstance(raw, dict) or raw.get('role') not in {'condition', 'process', 'outcome'}:
            raise StoryError('关键事件observation角色无效')
        ids = _ids(raw.get('evidence_ids'), available)
        observations.append({'subject': _text(raw.get('subject'), 'observation.subject'),
            'role': raw['role'], 'statement': _text(raw.get('statement'), 'observation.statement'),
            'evidence_ids': ids, 'evidence': [deepcopy(bank[key]) for key in ids], 'source_hash': digest(body)})
    delivered = [[row['id'], row['text']] for row in rows if row['id'] in available]
    return {'claims': claims, 'observations': observations, 'claim_reading_complete': available == set(bank),
            'claim_reading_source': {'source_hash': digest(body), 'paragraph_count': len(rows),
                'paragraph_ids': [row[0] for row in delivered], 'paragraph_bank_hash': digest(delivered)},
            'claim_validation_version': CLAIM_VALIDATION_VERSION}


def claim_view(claim):
    return {key: deepcopy(claim[key]) for key in ('claim_id', 'kind', 'subject', 'statement',
        'assertion_ids', 'condition_ids', 'process_ids', 'outcome_ids', 'status', 'reason',
        'comparison_scope', 'event_start_id', 'event_end_id', 'editorial_priority', 'reading_effect') if key in claim}


def observation_view(observation):
    return {key: deepcopy(observation[key]) for key in ('subject', 'role', 'statement', 'evidence_ids')}


def source_registry(body, plan, checks=None):
    """Only source pointers and immutable identity enter the final source read."""
    rows, bank = _bank(body)
    _verify_plan(body, plan)
    check_map = {item['claim_id']: item for item in plan['initial_checks']}
    if checks is not None:
        if checks.get('source_hash') != plan['source_hash'] or checks.get('plan_hash') != plan['plan_hash']:
            raise StoryError('关键断言核验回执与计划绑定不一致')
        check_map.update({item['claim_id']: item for item in checks['claim_checks']})
    registry = []
    for claim in plan['claims']:
        if editorial_priority.is_detail(claim):
            continue
        check = check_map.get(claim['claim_id'], {})
        selected = {key for role in ROLES for key in claim[role + '_ids']}
        selected.update(claim.get('event_ids', []))
        selected.update(check.get('evidence_ids', []))
        if not selected <= set(bank):
            raise StoryError('待核索引包含未知原文ID')
        required = [row['id'] for row in rows if row['id'] in selected]
        windows, start, end = [], None, None
        for row in rows:
            if row['id'] in selected:
                if start is None:
                    start = row['id']
                end = row['id']
            elif start is not None:
                windows.append({'start_id': start, 'end_id': end})
                start = end = None
        if start is not None:
            windows.append({'start_id': start, 'end_id': end})
        events = []
        for item in (claim, check):
            if 'event_start_id' in item:
                event = {'start_id': item['event_start_id'], 'end_id': item['event_end_id']}
                if event not in events:
                    events.append(event)
        registry.append({**{key: deepcopy(claim[key]) for key in ('claim_id', 'kind', 'subject',
            'assertion_ids', 'condition_ids', 'comparison_scope')}, 'required_evidence_ids': required,
            'event_windows': events, 'source_windows': windows})
    return registry


def build_audit_plan(body, reading_results, *, max_prompt_chars, render_prompt):
    """Plan one batch of whole claim contexts; unserved claims stay pending.

    Matching only retrieves candidate context. It never proves an assertion or
    changes its truth state. Original quote IDs remain immutable.
    """
    rows, bank = _bank(body)
    claims, observations = [], []
    for result in reading_results:
        claims.extend(deepcopy(result['claims']))
        observations.extend(deepcopy(result['observations']))
    if len({item['claim_id'] for item in claims}) != len(claims):
        raise StoryError('跨段关键断言ID重复')
    source_hash = digest(body)
    if any(item['source_hash'] != source_hash for item in claims + observations):
        raise StoryError('关键断言与当前正文版本不一致')
    single = len(reading_results) == 1 and reading_results[0].get('claim_reading_complete') is True
    initial_checks = []
    pending = []
    windows, complete_events = {}, set()
    for claim in claims:
        if editorial_priority.is_detail(claim):
            initial_checks.append(_check_from_claim(claim))
            continue  # Optional details retain their state, without a repair/reread cycle.
        if (claim['verification_status'] in {'verified', 'conflict'} and
                (claim['comparison_scope'] == 'outcome' or single or
                 claim.get('event_context_complete') is True)):
            initial_checks.append(_check_from_claim(claim))
            continue
        assertion_anchors = _assertion_ids(claim['assertion_ids'] + claim['condition_ids'], rows)
        event_anchors = set(claim['process_ids'] + claim['outcome_ids'])
        terms = set(claim.get('search_terms', []))
        if not terms:
            quoted = '\n'.join(bank[key]['text'] for key in claim['assertion_ids'])
            terms.update(match.group(1) for match in re.finditer(r'[“‘《]([^”’》]{2,})[”’》]', quoted))
        related = [item for item in observations if item['subject'] == claim['subject'] or
                   any(term in item['statement'] or any(term in row['text'] for row in item['evidence'])
                       for term in terms)]
        for item in related:
            if item['role'] == 'condition':
                assertion_anchors.update(item['evidence_ids'])
            else:
                event_anchors.update(item['evidence_ids'])
        # If no observation supplied the other end, source-bound literal search
        # retrieves candidates. It is not a keyword verdict or a guessed ID.
        if not claim['outcome_ids'] and not any(item['role'] == 'outcome' for item in related):
            for row in rows:
                if terms and any(term in row['text'] for term in terms):
                    event_anchors.add(row['id'])
        no_event_anchors = not event_anchors
        if no_event_anchors:
            # No extracted observation is not evidence that no process exists.
            # Reopen the assertion's complete scene instead of offering only
            # the single declaration and calling that complete context.
            event_anchors.update(claim['assertion_ids'])
        if claim['comparison_scope'] == 'outcome' and not claim.get('event_ids'):
            outcome_anchors = set(claim['outcome_ids']) | {key for item in related
                if item['role'] == 'outcome' for key in item['evidence_ids']}
            # A result-only prediction needs its actual result, not an invented
            # play-by-play. Without a result anchor, keep scene retrieval as a
            # discovery aid; the reread must still cite an actual result.
            event_context = (outcome_anchors if outcome_anchors else
                set(_event_ids(event_anchors, rows, to_boundary=no_event_anchors)))
            event_context.update(claim['process_ids'])
            complete_events.add(claim['claim_id'])
        elif claim.get('event_ids'):
            event_context = set(claim['event_ids'])
            if claim.get('event_anchor_coverage') is False:
                required_event = (claim['outcome_ids'] if claim['comparison_scope'] == 'outcome' else
                                  claim['process_ids'] + claim['outcome_ids'])
                # Retrieve a complete candidate interval for the one reread,
                # without certifying the editor's inconsistent old boundaries.
                event_context.update(_complete_ids(required_event + claim['event_ids'], rows))
            # Related observations are candidates, not a reason to silently
            # narrow the source-bound event declared by the editor.
            extra = event_anchors - event_context
            event_context.update(extra if claim['comparison_scope'] == 'outcome' else
                                 _event_ids(extra, rows))
            if claim.get('event_anchor_coverage') is not False:
                complete_events.add(claim['claim_id'])
        else:
            event_context = set(_event_ids(event_anchors, rows, to_boundary=no_event_anchors))
        windows[claim['claim_id']] = [row['id'] for row in rows if row['id'] in
                                    (assertion_anchors | event_context)]
        pending.append(claim)
    claim_ids = [claim['claim_id'] for claim in claims]
    served, selected = [], set()
    all_pending = [item['claim_id'] for item in pending]
    payload = {'protocol': PROTOCOL, 'planning_version': PLANNING_VERSION, 'source_hash': source_hash,
        'claims': [claim_view(item) for item in claims],
        'observations': [observation_view(item) for item in observations], 'claim_ids': all_pending}
    def context(ids, full):
        provided = [item['claim_id'] for item in pending if set(windows[item['claim_id']]) <= set(ids)]
        return {**payload, 'full_context_claim_ids': full,
            'served_claim_ids': provided,
            'unserved_claim_ids': [item['claim_id'] for item in pending if item['claim_id'] not in provided],
            'paragraphs': [[row['id'], row['text']] for row in rows if row['id'] in ids]}
    def cost(claim):
        return len(canonical_json([[key, bank[key]['text']] for key in windows[claim['claim_id']]]))
    full_context = []
    if pending and len(render_prompt(context(set(bank), all_pending))) <= max_prompt_chars:
        selected, served, full_context = set(bank), all_pending, all_pending
    else:
        for claim in sorted(pending, key=lambda item: (cost(item), rows.index(bank[item['assertion_ids'][0]]))):
            claim_id = claim['claim_id']
            trial = selected | set(windows[claim_id])
            trial_full = full_context + ([claim_id] if claim_id in complete_events else [])
            if len(render_prompt(context(trial, trial_full))) <= max_prompt_chars:
                selected, served = trial, served + [claim_id]
                full_context = trial_full
    unserved = [item['claim_id'] for item in pending if item['claim_id'] not in served]
    prompt = render_prompt(context(selected, full_context)) if served else None
    plan = {'protocol': PROTOCOL, 'planning_version': PLANNING_VERSION, 'source_hash': source_hash,
        'source_proof': {'source_hash': source_hash, 'paragraph_count': len(rows),
            'paragraph_ids': [row['id'] for row in rows],
            'paragraph_bank_hash': digest([[row['id'], row['text']] for row in rows])},
        'claims': claims, 'observations': observations, 'claim_ids': claim_ids,
        'reading_turn_count': len(reading_results),
        'reread_claim_ids': all_pending,
        'served_claim_ids': served, 'full_context_claim_ids': full_context,
        'unserved_claim_ids': unserved, 'initial_checks': initial_checks,
        'contexts': windows, 'allowed_ids': [row['id'] for row in rows if row['id'] in selected],
        'requires_reread': bool(served), 'prompt': prompt}
    plan['plan_hash'] = digest(plan)
    return plan


def _check_from_claim(claim):
    ids = list(dict.fromkeys([key for role in ROLES for key in claim[role + '_ids']] +
                            claim.get('event_ids', [])))
    evidence = {row['id']: row for items in claim['evidence'].values() for row in items}
    evidence.update({row['id']: row for row in claim.get('event_evidence', [])})
    ids.sort(key=lambda key: evidence[key]['span'][0])
    return {'claim_id': claim['claim_id'], 'status': claim['verification_status'],
        **editorial_priority.fields(claim),
        'model_status': claim['status'], 'reason': claim['reason'],
        'comparison_scope': claim['comparison_scope'],
        'process_ids': deepcopy(claim['process_ids']), 'outcome_ids': deepcopy(claim['outcome_ids']),
        'evidence_ids': ids, 'evidence': [deepcopy(evidence[key]) for key in ids],
        **{key: deepcopy(claim[key]) for key in ('event_start_id', 'event_end_id') if key in claim}}


def _verify_plan(body, plan):
    if (not isinstance(plan, dict) or plan.get('protocol') != PROTOCOL or plan.get('source_hash') != digest(body) or
            plan.get('plan_hash') != digest({k: v for k, v in plan.items() if k != 'plan_hash'})):
        raise StoryError('关键断言回读计划或正文绑定不一致')


def validate_reread_response(raw, body, plan):
    from .chief_editor import validate_response
    _verify_plan(body, plan)
    result = validate_response(raw, body, allowed_ids=plan['allowed_ids'], require_claims=False)
    supplied = raw.get('claim_checks')
    if not isinstance(supplied, list):
        raise StoryError('定向回读须逐条返回claim_checks')
    ids = [item.get('claim_id') for item in supplied if isinstance(item, dict)]
    if (len(ids) != len(supplied) or len(set(ids)) != len(ids) or
            set(ids) != set(plan['reread_claim_ids'])):
        raise StoryError('定向回读claim_checks遗漏、重复或冒认断言ID')
    rows, bank = _bank(body)
    claims = {item['claim_id']: item for item in plan['claims']}
    checks = []
    for raw_check in supplied:
        claim = claims[raw_check['claim_id']]
        if raw_check.get('comparison_scope', claim['comparison_scope']) != claim['comparison_scope']:
            raise StoryError('定向回读不能改变已绑定的comparison_scope')
        status = raw_check.get('status')
        if status not in STATUSES:
            raise StoryError('定向回读状态无效')
        process = _ids(raw_check.get('process_ids'), set(plan['allowed_ids']), empty=True)
        outcome = _ids(raw_check.get('outcome_ids'), set(plan['allowed_ids']), empty=True)
        comparison = {**claim, 'process_ids': process, 'outcome_ids': outcome}
        all_ids = list(dict.fromkeys([key for role in ROLES for key in claim[role + '_ids']] + process + outcome))
        window_anchors = list(outcome) if claim['comparison_scope'] == 'outcome' else process + outcome
        needs_window_repair = claim.get('event_anchor_coverage') is False
        if needs_window_repair:
            window_anchors += (claim['outcome_ids'] if claim['comparison_scope'] == 'outcome' else
                               claim['process_ids'] + claim['outcome_ids'])
        if any(key in raw_check for key in ('event_start_id', 'event_end_id')):
            event = _event_window(raw_check, set(plan['allowed_ids']), rows, window_anchors)
        elif {claim.get('event_start_id'), claim.get('event_end_id')} <= set(plan['allowed_ids']):
            event = _event_window(claim, set(plan['allowed_ids']), rows, window_anchors)
        else:
            event = {}
        required = set(claim['assertion_ids'] + claim['condition_ids']) | set(
            event.get('event_ids', process + outcome if claim['comparison_scope'] == 'outcome'
                      else _event_ids(process + outcome, rows)))
        complete = required <= set(plan['allowed_ids'])
        full_event = (claim['comparison_scope'] == 'outcome' or
                      claim['claim_id'] in plan['full_context_claim_ids'] or
                      (claim['claim_id'] in plan['served_claim_ids'] and event.get('event_context_complete')))
        repaired = not needs_window_repair or event.get('event_context_complete') is True
        effective = status if (full_event and complete and repaired and
            event.get('event_context_complete', True) and _has_comparison(comparison)) else 'pending'
        all_ids = [row['id'] for row in rows if row['id'] in set(all_ids + event.get('event_ids', []))]
        checks.append({'claim_id': claim['claim_id'], 'status': effective, 'model_status': status,
            **editorial_priority.fields(claim),
            'comparison_scope': claim['comparison_scope'],
            'reason': _text(raw_check.get('reason'), 'claim_check.reason'), 'process_ids': process,
            'outcome_ids': outcome, 'evidence_ids': all_ids,
            'evidence': [deepcopy(bank[key]) for key in all_ids],
            **{key: event[key] for key in ('event_start_id', 'event_end_id') if key in event}})
    result.update(claim_checks=checks, source_hash=digest(body), plan_hash=plan['plan_hash'],
                  claim_validation_version=CLAIM_VALIDATION_VERSION)
    return result


def final_gate(final, plan, checks=None):
    """Keep raw verdict visible; unresolved or conflicted claims cannot pass."""
    result = deepcopy(final)
    records = deepcopy(plan.get('initial_checks', [])) if plan else []
    if checks is not None:
        if checks.get('plan_hash') != plan.get('plan_hash') or checks.get('source_hash') != plan.get('source_hash'):
            raise StoryError('关键断言核验回执与计划绑定不一致')
        records = [item for item in records if item['claim_id'] not in {c['claim_id'] for c in checks['claim_checks']}]
        records.extend(deepcopy(checks['claim_checks']))
    by_id = {item['claim_id']: item for item in records}
    all_ids = list(plan.get('claim_ids', [])) if plan else []
    bound_claims = {item['claim_id']: item for item in plan['claims']} if plan else {}
    resolutions, rechecks, rejected_proposals = [], [], []
    for claim in result.get('claims', []):
        claim_id = claim['claim_id']
        if claim_id in bound_claims and claim['comparison_scope'] != bound_claims[claim_id]['comparison_scope']:
            raise StoryError('汇总不能改变原断言已绑定的comparison_scope')
        original = bound_claims.get(claim_id)
        if original is not None and (claim['source_hash'] != plan['source_hash'] or
                any(claim[key] != original[key] for key in
                    ('kind', 'subject', 'assertion_ids'))):
            raise StoryError('汇总不能改变原断言身份、条件或正文绑定')
        if claim_id not in all_ids:
            all_ids.append(claim_id)
        check = _check_from_claim(claim)
        # A final-stage new problem is not swallowed by an old verified result;
        # a new ready assertion cannot erase an earlier unresolved conflict.
        previous = by_id.get(claim_id)
        if original is not None and claim['condition_ids'] != original['condition_ids']:
            # A legal citation can still propose a different condition. Reject
            # that proposal without changing the source-bound original claim or
            # pretending this metadata mismatch is a defect in the story.
            if (plan.get('plan_hash') != digest({k: v for k, v in plan.items() if k != 'plan_hash'}) or
                    original['source_hash'] != plan['source_hash']):
                raise StoryError('关键断言回读计划或正文绑定不一致')
            if claim_id in plan.get('unserved_claim_ids', []):
                raise StoryError('无效条件提案不能批准未服务断言')
            if previous is not None and (
                    len(previous['evidence_ids']) != len(set(previous['evidence_ids'])) or
                    set(previous['evidence_ids']) != {row['id'] for row in previous['evidence']}):
                raise StoryError('先前断言核验缺少完整原文证据')
            if 'recheck' in claim:
                proof = claim['recheck']
                if (previous is None or proof['source_hash'] != plan['source_hash'] or
                        claim_id in plan.get('unserved_claim_ids', [])):
                    raise StoryError('原文复核不能冒认断言、改变正文或批准未服务断言')
                required = set(previous['evidence_ids']) | set(original.get('event_ids', []))
                required.update(key for role in ROLES for key in original[role + '_ids'])
                if not required <= set(proof['old_binding_evidence_ids']):
                    raise StoryError('原文复核须保留原断言、限定及先前完整证据端点')
                proof_bank = {row['id']: row for row in proof['evidence']}
                if any(proof_bank.get(row['id']) != row for row in previous['evidence']):
                    raise StoryError('原文复核与先前核验的实际原文不一致')
            if 'resolution' in claim:
                proof = claim['resolution']
                if (previous is None or claim_id in plan.get('unserved_claim_ids', []) or
                        proof['source_hash'] != plan['source_hash'] or
                        claim['statement'] != original['statement']):
                    raise StoryError('断言纠错不能改变原绑定、批准未服务断言或缺少完整核验')
                if not set(previous['evidence_ids']) <= set(proof['evidence_ids']):
                    raise StoryError('重复断言纠错须保留先前已核验的完整原文证据')
                proof_bank = {row['id']: row for row in proof['evidence']}
                if any(proof_bank.get(row['id']) != row for row in previous['evidence']):
                    raise StoryError('断言纠错与先前核验的实际原文不一致')
            if previous is not None and (previous['status'] != 'verified' or claim['status'] == 'verified'):
                effective = deepcopy(previous)
                effect = ('rejected_condition_binding_retained_verified' if previous['status'] == 'verified'
                          else 'rejected_condition_binding_retained_unresolved')
            else:
                effective = deepcopy(previous if previous is not None else _check_from_claim(original))
                effective.update(status='pending', metadata_pending=True,
                    reason='汇总条件绑定与原断言不一致；未采纳该提案，新的判断仍待核对。')
                effect = ('rejected_condition_binding_pending_new_conflict' if claim['status'] == 'conflict'
                          else 'rejected_condition_binding_pending')
            rejected_proposals.append({'claim_id': claim_id, 'effect': effect,
                'diffs': [{'field': 'condition_ids', 'expected': deepcopy(original['condition_ids']),
                           'received': deepcopy(claim['condition_ids'])}],
                'original_condition_ids': deepcopy(original['condition_ids']),
                'proposed_condition_ids': deepcopy(claim['condition_ids']),
                'retained_claim': deepcopy(original), 'previous_check': deepcopy(previous),
                'proposed_claim': deepcopy(claim), 'proposed_check': deepcopy(check),
                'resolved_check': deepcopy(effective)})
            by_id[claim_id] = effective
            continue
        if (checks is not None and plan.get('reading_turn_count') == 1 and previous is not None
                and claim_id in plan['claim_ids']):
            continue  # This is the initial single response, superseded by reread.
        if 'recheck' in claim:
            recheck = claim['recheck']
            if (original is None or previous is None or
                    recheck['source_hash'] != plan['source_hash'] or
                    claim_id in plan.get('unserved_claim_ids', [])):
                raise StoryError('原文复核不能冒认断言、改变正文或批准未服务断言')
            required = set(previous['evidence_ids']) | set(original.get('event_ids', []))
            required.update(key for role in ROLES for key in original[role + '_ids'])
            if not required <= set(recheck['old_binding_evidence_ids']):
                raise StoryError('原文复核须保留原断言、限定及先前完整证据端点')
            if previous['status'] == 'verified' and claim['status'] == 'verified':
                effective, effect = previous, 'no_op_already_verified'
            else:
                if not set(check['evidence_ids']) <= set(recheck['evidence_ids']):
                    raise StoryError('原文复核须包含本次完整实际事件证据')
                if (result.get('claim_reading_complete') is True and claim['status'] == 'verified' and
                        check['status'] == 'pending' and claim.get('event_anchor_coverage') is False and
                        _has_comparison(claim)):
                    # The same editor explicitly rechecked the whole delivered
                    # source, including every old and new cited endpoint. Keep
                    # its semantic verdict without inventing event boundaries or
                    # classifying outside citations as supplementary evidence.
                    source = result.get('claim_reading_source', {})
                    provided = source.get('paragraph_ids', [])
                    if (plan.get('plan_hash') != digest({k: v for k, v in plan.items() if k != 'plan_hash'}) or
                            original['source_hash'] != plan['source_hash'] or
                            source.get('source_hash') != plan['source_hash'] or
                            source != plan.get('source_proof') or
                            source.get('paragraph_count') != len(provided) or not provided or
                            provided != ['p' + str(index) for index in range(len(provided))] or
                            not isinstance(source.get('paragraph_bank_hash'), str) or
                            not set(check['evidence_ids']) <= set(provided)):
                        raise StoryError('全文原文复核缺少可信的完整来源绑定')
                    check.update(status='verified', coverage_basis='full_source_recheck',
                        coverage_audit={'normalized_status': claim['verification_status'],
                            'event_start_id': claim.get('event_start_id'),
                            'event_end_id': claim.get('event_end_id'),
                            'event_anchor_coverage': claim['event_anchor_coverage'],
                            'event_context_complete': claim.get('event_context_complete'),
                            'source': deepcopy(source)})
                priority = {'verified': 0, 'pending': 1, 'conflict': 2}
                effective = (check if check['status'] in {'verified', 'conflict'} or
                    priority[check['status']] > priority[previous['status']] else previous)
                effect = 'source_rechecked' if check['status'] != 'pending' else 'retained_unresolved'
            rechecks.append({'claim_id': claim_id, 'effect': effect,
                'previous_check': deepcopy(previous), 'old_statement': original['statement'],
                'new_statement': claim['statement'], 'recheck': deepcopy(recheck),
                'proposed_check': deepcopy(check), 'resolved_check': deepcopy(effective)})
            by_id[claim_id] = deepcopy(effective)
            continue
        if 'resolution' in claim:
            resolution = claim['resolution']
            original = bound_claims.get(claim_id)
            if (previous is None or original is None or
                    claim_id in plan.get('unserved_claim_ids', []) or
                    resolution['source_hash'] != plan['source_hash'] or
                    any(claim[key] != original[key] for key in
                        ('kind', 'subject', 'statement', 'assertion_ids', 'condition_ids', 'comparison_scope'))):
                raise StoryError('断言纠错不能改变原绑定、批准未服务断言或缺少完整核验')
            if previous['status'] == 'verified' and claim['status'] == 'verified':
                # The editor may repeat an explanation of initial pending after
                # the reread already verified it. Do not adopt its new, possibly
                # incomplete event metadata or manufacture a fresh truth verdict.
                if not set(previous['evidence_ids']) <= set(resolution['evidence_ids']):
                    raise StoryError('重复断言纠错须保留先前已核验的完整原文证据')
                resolutions.append({'claim_id': claim_id, 'effect': 'no_op_already_verified',
                    'previous_check': deepcopy(previous), 'resolution': deepcopy(resolution),
                    'proposed_check': deepcopy(check), 'resolved_check': deepcopy(previous)})
                continue
            if (previous['status'] != resolution['supersedes_status'] or check['status'] != 'verified'):
                raise StoryError('断言纠错不能改变原绑定、批准未服务断言或缺少完整核验')
            required = set(previous['evidence_ids']) | set(check['evidence_ids'])
            if not required <= set(resolution['evidence_ids']):
                raise StoryError('断言纠错须保留先前冲突端点及本次完整原文证据')
            resolutions.append({'claim_id': claim_id, 'effect': 'resolved', 'previous_check': deepcopy(previous),
                                'resolution': deepcopy(resolution), 'resolved_check': deepcopy(check)})
            by_id[claim_id] = check
            continue
        priority = {'verified': 0, 'pending': 1, 'conflict': 2}
        if previous is None or priority[check['status']] > priority[previous['status']]:
            by_id[claim_id] = check
    priorities = {key: editorial_priority.fields(item) for key, item in bound_claims.items()}
    for item in result.get('claims', []):
        if 'editorial_priority' in item:
            priorities[item['claim_id']] = editorial_priority.fields(item)
    for key, check in by_id.items():
        check.update(priorities.get(key, {}))
    details = [key for key in all_ids if editorial_priority.is_detail(priorities.get(key, {}))]
    pending = [key for key in all_ids if key not in details and
               (key not in by_id or by_id[key]['status'] == 'pending')]
    conflicts = [key for key in all_ids if key not in details and
                 key in by_id and by_id[key]['status'] == 'conflict']
    result.setdefault('initial_copyedit_reminders', []).extend(
        deepcopy(by_id[key]) for key in details if key in by_id and by_id[key]['status'] != 'verified')
    for key in conflicts:
        check = by_id[key]
        evidence = deepcopy(check['evidence'])
        if not evidence:
            raise StoryError('冲突断言缺少实际原文证据')
        if not any(issue.get('claim_id') == key for issue in result['issues']):
            result['issues'].append({'severity': 'blocker', 'scope': 'local', 'origin': 'claim_reread',
                'claim_id': key, 'reason': check['reason'],
                'fix_intent': '统一关键断言、必要条件与实际经过和结果，保留作者固定要求。',
                'evidence_ids': deepcopy(check['evidence_ids']), 'evidence': evidence})
    raw_recommendation = result['recommendation']
    if conflicts or any(item['severity'] == 'blocker' for item in result['issues']):
        result['recommendation'] = 'revise'
    elif pending:
        result['recommendation'] = 'uncertain'
    result['claim_gate'] = {'protocol': PROTOCOL, 'model_recommendation': raw_recommendation,
        'claim_validation_version': CLAIM_VALIDATION_VERSION,
        'status': 'conflict' if conflicts else 'pending' if pending else 'verified',
        'pending_ids': pending, 'conflict_ids': conflicts, 'claim_checks': list(by_id.values()),
        'nonblocking_detail_ids': details,
        'claim_resolutions': resolutions,
        'claim_rechecks': rechecks,
        'rejected_proposals': rejected_proposals,
        'source_hash': plan.get('source_hash') if plan else None,
        'plan_hash': plan.get('plan_hash') if plan else None}
    return result
