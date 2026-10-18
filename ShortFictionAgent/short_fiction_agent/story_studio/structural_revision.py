"""One source-bound structural rewrite, resumed without replaying completed calls.

This orchestrates production gates. A completed rewrite is never literary
approval: only the normal independent manuscript review can approve it.
"""
from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from . import lead, scene_planning, submission_guide, whole_story
from .contracts import StoryError, clean_body, digest
from .research_guidance import guidance_fingerprint
from .storage import now, read_json, write_json, write_text

PROTOCOL = 'chief-structural-revision-v3-explicit-nested-schema'
ACTIVE = {'planning', 'preflight', 'writing', 'lead', 'review'}


def _state_path(flow):
    return flow.project.root / 'structural_revisions' / 'state.json'


def _save(flow, state):
    state['updated_at'] = now()
    state['state_hash'] = digest({k: v for k, v in state.items() if k != 'state_hash'})
    write_json(_state_path(flow), state)
    write_json(Path(state['directory']) / 'state.json', state)


def _load(flow):
    state = read_json(_state_path(flow))
    if state is not None and (state.get('protocol') != PROTOCOL or state.get('attempts') != 1
            or state.get('state_hash') != digest({k: v for k, v in state.items() if k != 'state_hash'})):
        raise StoryError('结构返修检查点绑定损坏，不能重获返修额度')
    return state


def _request(flow, request):
    path = flow.project.root / 'structure_repair_request.json'
    previous = read_json(path)
    if previous is not None and previous != request:
        write_json(flow.project.root / 'structure_repair_history' / f'{uuid4()}.json', previous)
    write_json(path, request)


def _supersede_request(flow, state, reason):
    request = read_json(flow.project.root / 'structure_repair_request.json') or {}
    if (request.get('protocol') == PROTOCOL and request.get('content_hash') in {
            state['binding']['source_content_hash'], state.get('revised_content_hash')}):
        _request(flow, {**request, 'status': 'superseded', 'superseded_at': now(),
            'superseded_by': reason})


def _mark(flow, state, *, error=None):
    current, body = flow.project.active_draft()
    flow.project.update_state(current, 'needs_replan')
    report = state.get('final_report') or state['source_report']
    current_report = report.get('content_hash') == digest(body)
    _request(flow, {'protocol': PROTOCOL, 'created_at': state['created_at'],
        'status': 'resolved' if state['status'] == 'passed' else 'needs_replan',
        'repair_status': state['status'], 'content_hash': digest(body),
        'source_content_hash': state['binding']['source_content_hash'],
        'review_id': report.get('review_id') if current_report else None,
        'issues': report.get('issues', []) if current_report else [],
        **({} if current_report else {'previous_review_id': report.get('review_id'),
            'previous_content_hash': report.get('content_hash')}),
        'state_path': str(_state_path(flow)), **({'reason': error} if error else {})})
    flow._save_review_copy(report, error=error)
    if not current_report:
        # There is no completed review of this new prose yet. Keep the original
        # report as history; do not relabel its verdict or ID as a current review.
        return {'decision': 'revise_required', 'review_status': 'pending',
            'review_id': None, 'content_hash': digest(body), 'artifact_id': current['artifact_id'],
            'revision': current['revision'], 'issues': [], 'blockers': ['structural_revision_pending'],
            'structural_revision': {'status': state['status'], 'previous_review_id': report.get('review_id'),
                'previous_content_hash': report.get('content_hash'), 'error': error}}
    return report


def _verify(flow, state):
    from .workflow import chief_plan_criteria_hash
    from .distribution import valid_generation_lineage
    candidate, opening = flow._selection()
    binding = state['binding']
    expected = {'contract_hash': digest(flow.project.contract()),
        'selection_hash': digest(read_json(flow.project.root / 'selection.json')),
        'candidate_hash': digest(candidate), 'opening_hash': digest(opening),
        'approved_lead_hash': digest(read_json(flow.project.root / 'approved_lead.json')),
        'feedback_hash': digest(read_json(flow.project.root / 'editorial_feedback.json')),
        'criteria_hash': chief_plan_criteria_hash(), 'research_hash': guidance_fingerprint()}
    if any(binding.get(k) != v for k, v in expected.items()):
        raise StoryError('结构返修的作者合同、选择、导语或审查版本已改变，不能复用旧尝试')
    directory = Path(state['directory'])
    body = (directory / 'source-body.md').read_text(encoding='utf-8')
    if (digest(body) != binding['source_content_hash']
            or digest(state['source_plan']) != binding['source_plan_hash']
            or digest(state['source_report']) != binding['source_report_hash']):
        raise StoryError('结构返修原稿、方案或审稿证据绑定损坏')
    current, current_body = flow.project.active_draft()
    allowed = {binding['source_content_hash'], state.get('revised_content_hash')}
    if current['artifact_id'] != state['source_record']['artifact_id'] or digest(current_body) not in allowed:
        raise StoryError('当前正文不属于该次结构返修，不能覆盖其他修订')
    persisted = read_json(flow.project.root / 'plan' / 'scenes.json') or {}
    if digest(persisted) not in {binding['source_plan_hash'], state.get('revised_plan_hash')}:
        raise StoryError('当前方案已被其他规划替换，不能恢复失败候选或旧方案')
    source_hash = state.get('source_receipt_hash')
    if source_hash:
        source = read_json(flow.project.root / 'generation_receipts' / f'{source_hash}.json') or {}
        if digest(source) != source_hash or not valid_generation_lineage(flow.project, source):
            raise StoryError('结构返修缺少原生生成来源，不得补写成功历史')
    return body, candidate, opening


def _superseded_by_author_replan(flow, state):
    """Accept the existing explicit author-replan handoff, never a loose plan edit.

    This retires the old attempt only. It does not reset the automatic allowance,
    approve the new prose, or manufacture an author-replan/model receipt.
    """
    from .workflow import CHIEF_PLAN_PROTOCOL, chief_plan_criteria_hash
    root = flow.project.root / 'plan'
    plan = read_json(root / 'scenes.json') or {}
    if digest(plan) in {state['binding']['source_plan_hash'], state.get('revised_plan_hash')}:
        return False
    receipt = read_json(root / 'author-replan-receipt.json') or {}
    binding = receipt.get('binding') or {}
    request_id = receipt.get('request_id')
    if (binding.get('protocol') != 'source-bound-author-replan-v1'
            or not isinstance(request_id, str) or len(request_id) != 64
            or any(char not in '0123456789abcdef' for char in request_id)):
        return False
    directory = root / 'author_replans' / request_id
    request = read_json(directory / 'author-request.json')
    source_plan = read_json(directory / 'source-plan.json')
    response = read_json(directory / 'raw-response.json') or {}
    audit = read_json(root / 'chief-preflight.json') or {}
    preflight = read_json(root / 'preflight_receipt.json') or {}
    candidate, opening = flow._selection()
    current, body = flow.project.active_draft()
    contract = flow.project.contract()
    if contract['format'] != 'short_story':
        return False
    expected = {'contract_hash': digest(contract),
        'candidate_hash': digest(candidate), 'opening_hash': digest(opening),
        'selection_hash': digest(read_json(flow.project.root / 'selection.json')),
        'research_hash': guidance_fingerprint(), 'chief_plan_criteria_hash': chief_plan_criteria_hash()}
    # Author handoffs append these provenance fields after the actual preflight.
    content = {key: value for key, value in plan.items() if key not in {
        'candidate_hash', 'opening_hash', 'contract_hash', 'planning_hash', 'author_replan_request_id'}}
    if (any(binding.get(key) != value for key, value in expected.items())
            or binding.get('author_request_hash') != request_id or digest(request) != request_id
            or not isinstance(request, dict)
            or request.get('source_plan_hash') != binding.get('source_plan_hash')
            or request.get('original_hash') != binding.get('original_hash')
            or digest(source_plan) != binding.get('source_plan_hash')
            or not isinstance(response.get('response'), dict)
            or response.get('binding') != binding
            or response.get('response_hash') != digest(response.get('response'))
            or receipt.get('model_response_hash') != digest(response.get('response'))
            or current['artifact_id'] != state['source_record']['artifact_id']
            or digest(body) != binding.get('original_hash')
            or binding.get('source_plan_hash') not in {
                state['binding']['source_plan_hash'], state.get('revised_plan_hash')}
            or binding.get('original_hash') not in {
                state['binding']['source_content_hash'], state.get('revised_content_hash')}
            or receipt.get('plan_hash') != digest(plan)
            or receipt.get('actual_chief_preflight_hash') != digest(audit)
            or audit.get('binding', {}).get('protocol') != CHIEF_PLAN_PROTOCOL
            or audit.get('binding', {}).get('criteria_hash') != chief_plan_criteria_hash()
            or audit.get('plan_hash') not in {digest(plan), digest(content)}
            or audit.get('assessment', {}).get('decision') != 'pass'
            or preflight.get('decision') != 'pass' or preflight.get('plan_hash') != digest(plan)
            or plan.get('planning_hash') != flow._short_planning_hash(contract, candidate, opening)
            or preflight.get('planning_hash') != plan.get('planning_hash')
            or preflight.get('actual_chief_preflight_hash') != digest(audit)):
        return False
    flow._validate_compact_plan(plan, flow.project.contract())
    scene_planning.validate_review(audit['assessment'], plan)
    state.update(status='superseded', author_replan_receipt_hash=digest(receipt), superseded_at=now())
    _save(flow, state)
    _supersede_request(flow, state, {'author_replan_receipt_hash': digest(receipt)})
    flow.project.record_run({'event': 'chief_structural_revision_superseded_by_author',
        'author_replan_receipt_hash': digest(receipt), 'plan_hash': digest(plan)})
    return True


def resume(flow, *, auto_revise, entry_point, source_receipt_hash=None):
    """Intercept pending work before normal planning can restore an older plan."""
    if getattr(flow, '_structural_revision_active', False):
        return None
    state = _load(flow)
    if state is None or state['status'] in {'passed', 'superseded'}:
        return None
    if _superseded_by_author_replan(flow, state):
        return None
    _verify(flow, state)
    if state['status'] not in ACTIVE or not auto_revise:
        return _mark(flow, state, error=state.get('last_error'))
    return _run(flow, state, entry_point=entry_point)


def supersede_after_reselection(flow, *, content_hash, review_id):
    """A successful explicit reselection must not resurrect the rejected plan."""
    state = _load(flow)
    if state is None or state['status'] == 'superseded':
        return
    report = state.get('final_report') or state['source_report']
    if report.get('content_hash') != content_hash or report.get('review_id') != review_id:
        raise StoryError('重新选题与旧结构返修的退稿证据不一致')
    state.update(status='superseded', superseded_at=now(),
        superseded_by={'action': 'explicit_candidate_reselection',
            'content_hash': content_hash, 'review_id': review_id,
            'selection_hash': digest(read_json(flow.project.root / 'selection.json'))})
    _save(flow, state)
    _supersede_request(flow, state, state['superseded_by'])


def repair(flow, plan, report, *, auto_revise, entry_point, source_receipt_hash):
    """Begin only for a grounded current review and an existing native plan."""
    from .run_control import reserve_repair
    from .workflow import chief_plan_criteria_hash
    current, body = flow.project.active_draft()
    issues = [i for i in report.get('issues', [])
              if i.get('severity') == 'blocker' and i.get('repair_scope') == 'replan']
    flow.project.update_state(current, 'needs_replan')
    _request(flow, {'protocol': PROTOCOL, 'created_at': now(), 'status': 'needs_replan',
        'content_hash': digest(body), 'review_id': report['review_id'], 'issues': issues})
    flow._save_review_copy(report)
    if not auto_revise or getattr(flow, '_structural_revision_active', False):
        return report
    # Imported prose with no valid plan cannot acquire native generation history
    # merely because an editor reported a structural problem.
    if (not plan or not source_receipt_hash
            or digest(read_json(flow.project.root / 'plan' / 'scenes.json')) != digest(plan)):
        return report
    candidate, opening = flow._selection()
    contract = flow.project.contract()
    if (report.get('content_hash') != digest(body)
            or plan.get('contract_hash') != digest(contract)
            or plan.get('candidate_hash') != digest(candidate)
            or plan.get('opening_hash') != digest(opening)):
        raise StoryError('结构返修审稿、原方案与当前正文绑定不一致')
    for issue in issues:
        span = issue.get('span')
        if (not isinstance(span, list) or len(span) != 2
                or any(type(x) is not int for x in span)
                or not 0 <= span[0] < span[1] <= len(body)
                or body[slice(*span)] != issue.get('quote')):
            raise StoryError('结构问题缺少当前原文证据，不能自动重新创作')
    existing = _load(flow)
    if existing and existing['status'] not in {'passed', 'superseded'}:
        return resume(flow, auto_revise=auto_revise, entry_point=entry_point,
                      source_receipt_hash=source_receipt_hash)
    if not reserve_repair(flow.project, flow.max_calls):
        return report
    directory = flow.project.root / 'structural_revisions' / uuid4().hex
    write_text(directory / 'source-body.md', body)
    binding = {'source_content_hash': digest(body), 'source_plan_hash': digest(plan),
        'source_report_hash': digest(report), 'contract_hash': digest(contract),
        'selection_hash': digest(read_json(flow.project.root / 'selection.json')),
        'candidate_hash': digest(candidate), 'opening_hash': digest(opening),
        'approved_lead_hash': digest(read_json(flow.project.root / 'approved_lead.json')),
        'feedback_hash': digest(read_json(flow.project.root / 'editorial_feedback.json')),
        'criteria_hash': chief_plan_criteria_hash(), 'research_hash': guidance_fingerprint()}
    state = {'protocol': PROTOCOL, 'attempts': 1, 'status': 'planning', 'created_at': now(),
        'directory': str(directory), 'binding': binding, 'source_plan': plan,
        'source_report': report, 'source_record': current, 'issues': issues,
        'source_receipt_hash': source_receipt_hash}
    _save(flow, state)
    flow.project.record_run({'event': 'chief_structural_revision_started', **binding})
    return _run(flow, state, entry_point=entry_point)


def _run(flow, state, *, entry_point):
    from .quality import parse_json_response
    from .workflow import (normalize_scene_plan, validate_continuity_map, PLAN_INSTRUCTION,
                           CHIEF_PLAN_EXECUTABLE_SCHEMA)
    source_body, candidate, opening = _verify(flow, state)
    contract = flow.project.contract()
    directory = Path(state['directory'])
    completed = False
    phase = state['status']
    try:
        if state['status'] == 'planning':
            prompt = ('针对正式主审已经引用原文指出的结构问题，做本次唯一一次集中重规划。'
                '只输出完整新方案JSON；包含main_question、scenes、promises、ending_resolution、continuity_map。'
                '每场保留scene_id、purpose、goal、pressure、choice、consequence、new_information、'
                'emotional_change、budget、dependencies，按适用协议补齐dramatic_chain与progression_beats。'
                '输入中的executable_schema规定输出字段、类型和嵌套层级；按该结构写真实剧情，不照抄类型示意值。'
                '原计划与原稿仅作问题诊断，新正文将按新计划完整重写；不得恢复候选中已被否决的旧解法。'
                '集中改变造成问题的事件顺序、实际交锋、回报和篇幅安排，不靠换字段、加重措辞或扩大金额冒充修复。'
                '保护作者锁定事实、视角、能力边界和核心欲望；目标字数是软目标，max为null不代表新上限。'
                '不得为通过审核增加核验、免责和账目流水。已确认导语保持逐字不变，方案须能兑现它；'
                '如必须改变作者确认内容，不得假装已获授权。\n'
                + scene_planning.PLAN_INSTRUCTION + PLAN_INSTRUCTION
                + (whole_story.PLAN_INSTRUCTION if whole_story.enabled(contract) else '')
                + submission_guide.submission_guide_guidance(contract, 'revision')
                + '\n' + json.dumps({'contract': contract,
                    'executable_schema': CHIEF_PLAN_EXECUTABLE_SCHEMA,
                    'candidate_identity': {k: candidate[k] for k in ('id', 'protagonist') if k in candidate},
                    'opening': opening, 'source_plan': state['source_plan'],
                    'unresolved_core_issues': state['issues'],
                    'source_excerpts': [body for i in state['issues'] for body in
                        [source_body[max(0, i['span'][0] - 180):i['span'][1] + 180]]],
                    'approved_lead': read_json(flow.project.root / 'approved_lead.json'),
                    'submission_guide': submission_guide.submission_guide_context(contract, 'revision')},
                    ensure_ascii=False, sort_keys=True))
            path = directory / 'plan-response.json'
            saved = read_json(path)
            if saved is None:
                raw = flow.call_text('whole_story_replan', prompt)
                saved = {'binding': state['binding'], 'prompt_hash': digest(prompt),
                         'raw_text': raw, 'raw_hash': digest(raw)}
                write_json(path, saved)
            completed = True
            if (saved.get('binding') != state['binding'] or saved.get('prompt_hash') != digest(prompt)
                    or saved.get('raw_hash') != digest(saved.get('raw_text'))):
                raise StoryError('结构重规划原始回答绑定损坏')
            revised = normalize_scene_plan(parse_json_response(saved['raw_text']))
            flow._validate_generated_chief_plan(revised, contract)
            if digest(flow._budget_plan_content(revised)) == digest(flow._budget_plan_content(state['source_plan'])):
                raise StoryError('结构返修没有改变被否决的实际剧情与篇幅安排')
            state.update(status='preflight', candidate_plan=revised)
            _save(flow, state)
        phase, completed = state['status'], False
        if phase == 'preflight':
            def on_completed():
                nonlocal completed
                completed = True
            revised = flow._preflight_plan_chief(state['candidate_plan'], contract,
                revision_context={'scope': 'chief_structural_revision_from_scratch',
                    'source_content_hash': state['binding']['source_content_hash'],
                    'source_review_id': state['source_report']['review_id'], 'issues': state['issues']},
                allow_repair=False, persist_raw_response=True, on_completed_response=on_completed)
            for key in ('candidate_hash', 'opening_hash', 'contract_hash', 'planning_hash'):
                if key in state['source_plan']:
                    revised[key] = state['source_plan'][key]
            state.update(status='writing', revised_plan=revised, revised_plan_hash=digest(revised))
            _save(flow, state)
        phase, completed = state['status'], False
        if phase == 'writing':
            revised = state['revised_plan']
            write_json(flow.project.root / 'plan' / 'scenes.json', revised)
            write_json(flow.project.root / 'plan' / 'facts.json', validate_continuity_map(revised))
            write_json(flow.project.root / 'plan' / 'promises.json', revised.get('promises') or [])
            write_json(flow.project.root / 'plan' / 'preflight_receipt.json', {
                'planning_hash': revised.get('planning_hash'), 'plan_hash': digest(revised), 'decision': 'pass',
                'structural_revision_binding_hash': digest(state['binding'])})
            path = directory / 'rewritten-story.json'
            saved = read_json(path)
            if saved is None:
                story = clean_body(flow._draft_text(revised))
                saved = {'binding': state['binding'], 'plan_hash': digest(revised),
                         'body': story, 'body_hash': digest(story)}
                # The completed prose and its binding are one atomic record.
                # A crash before updating state must not cause another write.
                write_json(path, saved)
            if (saved.get('binding') != state['binding'] or saved.get('plan_hash') != digest(revised)
                    or not isinstance(saved.get('body'), str)
                    or saved.get('body_hash') != digest(saved.get('body'))):
                raise StoryError('结构返修正文检查点损坏')
            story = saved['body']
            text_path = directory / 'rewritten-story.md'
            if text_path.exists() and digest(text_path.read_text(encoding='utf-8')) != digest(story):
                raise StoryError('结构返修正文检查点损坏')
            write_text(text_path, story)
            state.update(status='lead', rewritten_story_hash=digest(story))
            _save(flow, state)
        phase = state['status']
        if phase == 'lead':
            story = (directory / 'rewritten-story.md').read_text(encoding='utf-8')
            if digest(story) != state['rewritten_story_hash']:
                raise StoryError('结构返修正文检查点损坏')
            approved = read_json(flow.project.root / 'approved_lead.json')
            if lead.enabled(contract):
                chapter = lead.CHAPTER.search(story)
                story = story[chapter.start():] if chapter else story
                if approved:
                    body = lead.assemble(approved['lead_text'], story, contract)
                    lead.validate_approved_lead(body, contract, approved)
                else:
                    body = flow._write_single_lead(story, opening)
            else:
                body = story
            body = submission_guide.prepare_submission_text(body, contract)
            # Same literal protection as the existing local-edit path. Semantic
            # preservation additionally remains the independent editor's job.
            for fact in contract.get('author_input', {}).get('locked_facts', []):
                if fact in source_body and fact not in body:
                    raise StoryError(f'结构返修删去了作者锁定事实: {fact}')
            if digest(body) == state['binding']['source_content_hash']:
                raise StoryError('结构返修未改变正文，不能冒充修复')
            state.update(revised_content_hash=digest(body), status='review')
            write_text(directory / 'revised-body.md', body)
            _save(flow, state)
        phase = state['status']
        body = (directory / 'revised-body.md').read_text(encoding='utf-8')
        if digest(body) != state['revised_content_hash']:
            raise StoryError('结构返修待审正文绑定损坏')
        current, active_body = flow.project.active_draft()
        if digest(active_body) != digest(body):
            current = flow.project.save_draft(body, artifact_id=current['artifact_id'],
                                               parent_revision=current['revision'])
        if state.get('source_receipt_hash'):
            flow._save_generation_checkpoint(current, state['source_receipt_hash'])
        flow._structural_revision_active = True
        try:
            report = flow._finish_chief_generation(state['revised_plan'], current, auto_revise=False,
                entry_point=entry_point, source_receipt_hash=state.get('source_receipt_hash'))
        finally:
            flow._structural_revision_active = False
        state.update(status='passed' if report['decision'] == 'passed' else 'exhausted', final_report=report)
        _save(flow, state)
        _request(flow, {'protocol': PROTOCOL, 'created_at': state['created_at'],
            'status': 'resolved' if state['status'] == 'passed' else 'needs_replan',
            'repair_status': state['status'], 'content_hash': digest(body),
            'source_content_hash': state['binding']['source_content_hash'],
            'review_id': report['review_id'], 'issues': report.get('issues', []),
            'state_path': str(_state_path(flow))})
        if report['decision'] != 'passed':
            return _mark(flow, state)
        flow.project.record_run({'event': 'chief_structural_revision_passed',
            'content_hash': digest(body), 'review_id': report['review_id'],
            'commercial_validation': 'unverified'})
        return report
    except Exception as exc:
        budget = read_json(flow.project.root / 'structure_repair_request.json') or {}
        budget_failed = budget.get('protocol') == 'scene-budget-replan-v1'
        terminal = (phase in {'planning', 'preflight'} and completed) or budget_failed or (
            phase == 'lead' and isinstance(exc, StoryError) and
            any(term in str(exc) for term in ('锁定事实', '确认原文', '未改变正文', '检查点损坏')))
        if budget_failed:
            write_json(directory / 'rewrite-budget-failure.json', budget)
        state.update(status='exhausted' if terminal else state['status'], last_error=str(exc))
        _save(flow, state)
        result = _mark(flow, state, error=str(exc))
        if terminal and isinstance(exc, StoryError):
            return result
        raise
