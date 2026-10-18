"""Keep editorial objections answerable without freezing old verdicts as truth.

The same chief editor can withdraw a mistaken finding, but has to show the
current text that explains it. These records check closure, not literary merit.
"""
from __future__ import annotations

import re
from copy import deepcopy

from .contracts import digest
from . import editorial_priority

PROTOCOL = 'source-bound-editorial-closure-v3-optional-reminders'
INSTRUCTION = (
    editorial_priority.INSTRUCTION +
    'editorial_issue_registry是本次必须回应的既有异议，不是已证实的事实。'
    '终审返回issue_dispositions数组，每项含issue_id、status、evidence_ids、reason。'
    'status为retained/context_explained/revision_resolved：仍成立则retained；完整上下文说明误报则'
    'context_explained；当前改稿已实际解决则revision_resolved。后二者须指出当前原段如何解除原问题，'
    '不能只说主题一致、已经修改、全文精彩或事实成立。未修改的同一稿不能声称revision_resolved。'
    'retained须说明剩余问题和修法，只有reading_core保留为blocker。copyedit_detail可仍retained，'
    '只留初始提醒，不要求解决，不因未回应而阻断。disposition可另给editorial_priority和reading_effect，'
    '说明完整上下文下的读者影响；不能将专业手续留白再升级为核心因果。缺上下文不强行撤销。'
    '对真实作者或编辑退稿意见逐项核对；旧机器建议不自动成为作者硬要求。'
    '每项引用本次paragraphs实际提供的当前正文ID。chunk不负责提前关闭整篇异议；full必须回应全部核心异议，'
    '可选细节不要求回应或解决。无效原文ID、未知或重复条目仍属记录完整性错误。'
)


def feedback_registry(feedback, body, *, source_hash=None):
    if not isinstance(feedback, dict) or feedback.get('decision') != 'reject':
        return []
    source = feedback.get('source', 'external_editor')
    if source in {'chief_editor', 'model', 'machine', 'agent', 'automated_review'}:
        return []
    reason = feedback.get('reason')
    if not isinstance(reason, str) or not reason.strip():
        return []
    # Retain the actual supplied words; this is not a model-generated diagnosis.
    parts = [part.strip() for part in re.split(r'[\n，,；;。]+', reason) if part.strip()]
    result = []
    for index, part in enumerate(parts):
        result.append({'issue_id': 'external-' + digest({'feedback': feedback, 'index': index,
            'objection': part})[:24], 'origin': 'external_feedback', 'source': source,
            'reason': part, 'evidence_ids': [],
            'same_source_version': feedback.get('content_hash') == (source_hash or digest(body)),
            'changed_source_confirmed': isinstance(feedback.get('content_hash'), str)
                and bool(re.fullmatch(r'[a-f0-9]{64}', feedback['content_hash']))
                and feedback['content_hash'] != (source_hash or digest(body))})
    return result


def reading_registry(readings):
    result, seen = [], set()
    for index, reading in enumerate(readings):
        items = reading.get('issues', []) + (reading.get('narrative_audit') or {}).get('findings', [])
        for item in items:
            if item.get('severity') != 'blocker':
                continue
            view = {key: deepcopy(item.get(key)) for key in
                ('origin', 'reason', 'fix_intent', 'evidence_ids', 'scope', 'repair_scope')}
            view.update(editorial_priority.fields(item))
            issue_id = 'reading-' + digest(view)[:24]
            if issue_id in seen:
                continue
            seen.add(issue_id)
            result.append({**view, 'issue_id': issue_id, 'origin': 'reading_objection',
                'reading': index, 'same_source_version': True, 'changed_source_confirmed': False})
    return result


def registry(body, readings=(), feedback=None):
    return reading_registry(readings) + feedback_registry(feedback, body)


def final_gate(final, body, readings, feedback=None):
    """An omitted objection makes acceptance incomplete; it never becomes ready."""
    result = deepcopy(final)
    expected = registry(body, readings if len(readings) > 1 else (), feedback)
    raw = result.get('issue_dispositions')
    if raw is None and (not expected or all(editorial_priority.is_detail(item) for item in expected)):
        raw = []
    missing, dispositions, reminders = [], [], []
    rows = (result.get('narrative_audit') or {}).get('provided_ids', [])
    available = set(rows)
    bank = {f'p{i}': {'id': f'p{i}', 'text': match.group(), 'span': [match.start(), match.end()]}
        for i, match in enumerate(m for m in re.finditer(r'[^\n]+', body) if m.group().strip())}
    if not isinstance(raw, list):
        missing.append('issue_dispositions')
        raw = []
    wanted = {item['issue_id']: item for item in expected}
    found = {}
    for index, item in enumerate(raw):
        if not isinstance(item, dict) or item.get('issue_id') not in wanted:
            missing.append(f'issue_dispositions[{index}].unknown_issue')
            continue
        key = item['issue_id']
        if key in found:
            missing.append(key + '.duplicate')
            continue
        found[key] = item
        status, ids, reason = item.get('status'), item.get('evidence_ids'), item.get('reason')
        if (status not in {'retained', 'context_explained', 'revision_resolved'}
                or not isinstance(ids, list) or not ids
                or any(not isinstance(k, str) or k not in available for k in ids)
                or len(ids) != len(set(ids))
                or not isinstance(reason, str) or not reason.strip()):
            missing.append(key + '.source_bound_disposition')
            continue
        priority_fields = editorial_priority.fields(item if 'editorial_priority' in item else wanted[key])
        detail = editorial_priority.is_detail(priority_fields)
        unconfirmed_resolution = None
        if status == 'revision_resolved' and not wanted[key].get('changed_source_confirmed'):
            unconfirmed_resolution = ('unchanged_source' if wanted[key].get('same_source_version')
                                      else 'unknown_source_version')
            if not detail:
                missing.append(key + '.' + unconfirmed_resolution)
                continue
        normalized = {'issue_id': key, 'status': status, 'evidence_ids': deepcopy(ids),
            'reason': reason.strip(), 'evidence': [deepcopy(bank[k]) for k in ids]}
        normalized.update(priority_fields)
        if unconfirmed_resolution:
            normalized.update(model_status=status, status='retained',
                              unconfirmed_resolution=unconfirmed_resolution)
        dispositions.append(normalized)
        if detail:
            reminders.append({**deepcopy(wanted[key]), **deepcopy(normalized),
                'source_hash': digest(body), 'origin': 'editorial_closure'})
        elif status == 'retained':
            result['issues'].append({'severity': 'blocker', 'origin': 'editorial_closure',
                'scope': wanted[key].get('scope') or 'global',
                'reason': reason.strip(), 'fix_intent': wanted[key].get('fix_intent') or
                    '依据本次原段解决仍成立的编辑异议：' + wanted[key]['reason'],
                'evidence_ids': deepcopy(ids), 'evidence': deepcopy(normalized['evidence'])})
            result['issues'][-1].update(editorial_priority.fields(normalized))
    for key in wanted:
        if key not in found:
            if editorial_priority.is_detail(wanted[key]):
                reminders.append({**deepcopy(wanted[key]), 'status': 'unanswered',
                    'source_hash': digest(body), 'origin': 'editorial_closure',
                    'evidence': [deepcopy(bank[k]) for k in wanted[key].get('evidence_ids', []) if k in bank]})
            else:
                missing.append(key + '.unanswered')
    result.setdefault('initial_copyedit_reminders', []).extend(reminders)
    if any(item.get('severity') == 'blocker' and not editorial_priority.is_detail(item)
           for item in result['issues']):
        result['recommendation'] = 'revise'
    elif missing:
        result['recommendation'] = 'uncertain'
    result['editorial_closure'] = {'protocol': PROTOCOL, 'source_hash': digest(body),
        'registry': expected, 'dispositions': dispositions, 'missing': missing,
        'initial_copyedit_reminder_count': len(reminders),
        'complete': not missing, 'meaning': 'source-bound disposition coverage; not independent quality proof'}
    return result
