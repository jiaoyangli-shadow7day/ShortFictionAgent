"""Separate optional copyediting from reader-facing acceptance.

The editor supplies the semantic classification with a concrete reading reason.
No money, copyright or contract keyword can waive a genuine narrative problem.
Raw findings and truth states remain in receipts; optional details are never
certified as fixed simply because they do not block delivery.
"""
from __future__ import annotations

from copy import deepcopy

from .contracts import StoryError, digest

PROTOCOL = 'reader-first-editorial-priority-v1'
INSTRUCTION = (
    '审读优先级统一为editorial_priority=reading_core或copyedit_detail，另写reading_effect说明具体读者影响。'
    'reading_core是读者实际能感知的主线不清、期待弱、节奏拖沓、钩子不足、核心回报未兑现、'
    '人物行动明显自相矛盾或理解障碍，仍可阻断。copyedit_detail是与阅读体验关联不大的金额精算、'
    '本金利润尾数、日期小误差、小说内版权持有人或代理授权、合同/银行/登记/通知等手续细节；'
    '最多在初始修改时提醒，不能成为最终blocker、uncertain、强制重规划或反复返修的理由。'
    '例如正文已写双方签字、交易成立、资金到账且主角凭此发财，未讲清各签字人的法律身份只属'
    'copyedit_detail；不能仅用“关键因果”“后续依赖其有效”“金额巨大”或“固定要求”把手续留白包装成阻断。'
    '若正文明确交易失败、钱始终未到，却直接宣称发财完成，或角色明确失去权力却无新行动凭同一权力'
    '赢得高潮，则是读者能直接感知的核心矛盾，应reading_core；要求兑现赢法，不扩写专业审核流程。'
    '同样，反复报账、谈合同使人物交锋或关系回应长期停滞，是节奏问题，仍属reading_core。'
    '不按金额、版权、合同等词语自动降级。真实稿件的原创、独家、首发等投稿资格与小说内手续不同，'
    '仍按实际投稿要求核验。不得为修算账和手续细节扩写说明、挤压导语或核心互动；'
    '未解决的次要细节保留原status和证据，不冒充verified/resolved。'
)


def combine_instructions(*parts: str) -> str:
    """Include the shared policy once when several review schemas are combined."""
    return INSTRUCTION + ''.join(part.replace(INSTRUCTION, '') for part in parts)


def normalize(item: dict) -> dict:
    result = deepcopy(item)
    priority = result.get('editorial_priority', 'reading_core')
    if priority not in {'reading_core', 'copyedit_detail'}:
        raise StoryError('审读优先级无效')
    effect = result.get('reading_effect')
    if ('editorial_priority' in item or 'reading_effect' in item) and (
            not isinstance(effect, str) or not effect.strip()):
        raise StoryError('审读优先级须说明具体读者影响')
    if 'editorial_priority' in item:
        result['editorial_priority'] = priority
    if effect is not None:
        result['reading_effect'] = effect.strip()
    return result


def is_detail(item: dict) -> bool:
    return isinstance(item, dict) and normalize(item).get('editorial_priority') == 'copyedit_detail'


def fields(item: dict) -> dict:
    normalized = normalize(item)
    return {key: normalized[key] for key in ('editorial_priority', 'reading_effect') if key in normalized}


def plan_gate(response: dict) -> dict:
    """Keep optional initial notes out of preflight/structural repair issues."""
    result = deepcopy(response)
    items = [normalize(item) for item in result.get('issues', [])]
    details = [item for item in items if is_detail(item)]
    result['issues'] = [item for item in items if not is_detail(item)]
    if details:
        result['model_decision'] = result.get('model_decision', result.get('decision'))
        result['initial_copyedit_reminders'] = deepcopy(result.get('initial_copyedit_reminders', [])) + details
        result['decision'] = 'revise' if result['issues'] else 'pass'
    return result


def final_gate(final: dict, body: str) -> dict:
    """Project optional reminders away from final blockers, without a new role."""
    result = deepcopy(final)
    previous = result.get('editorial_priority_gate')
    if previous:
        if previous.get('protocol') != PROTOCOL or previous.get('source_hash') != digest(body):
            raise StoryError('审读优先级与当前正文不匹配')
        return result
    reminders = deepcopy(result.get('initial_copyedit_reminders', []))
    effective = []
    for issue in result.get('issues', []):
        (reminders if is_detail(issue) else effective).append(deepcopy(issue))
    result['issues'] = effective
    for claim in result.get('claims', []):
        if is_detail(claim) and claim.get('status') in {'pending', 'conflict'}:
            reminders.append(deepcopy(claim))
    unique, seen = [], set()
    for item in reminders:
        key = digest(item)
        if key not in seen:
            seen.add(key)
            unique.append(item)
    result['initial_copyedit_reminders'] = unique
    raw = result['recommendation']
    # Narrative and evidence coverage stay independent. Optional financial or
    # legal detail cannot erase a thin reading, missing source, or core claim.
    core_blockers = any(item.get('severity') == 'blocker' for item in effective)
    claim_gate = result.get('claim_gate', {})
    first = result.get('first_read_gate', {})
    narrative = result.get('narrative_gate', {})
    ledger = result.get('expectation_gate', {})
    closure = result.get('editorial_closure', {})
    unresolved = (claim_gate.get('pending_ids') or claim_gate.get('conflict_ids') or
        first.get('blockers') or first.get('uncertain') or narrative.get('missing') or
        narrative.get('status') in {'thin', 'incomplete'} or ledger.get('missing') or
        ledger.get('retained_issue_ids') or ledger.get('uncertain_intervals') or closure.get('missing'))
    if core_blockers:
        result['recommendation'] = 'revise'
    elif unique and not unresolved:
        result['recommendation'] = 'ready'
    result['editorial_priority_gate'] = {'protocol': PROTOCOL, 'source_hash': digest(body),
        'model_recommendation': raw, 'initial_reminder_count': len(unique),
        'meaning': 'optional copyedit findings retained; not fixed or verified; not final blockers'}
    return result
