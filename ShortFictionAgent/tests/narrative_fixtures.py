"""Scripted source-bound positive responses for orchestration tests only.

These invented editorial explanations exercise protocol/coverage. They never
establish that a real manuscript is good or that a live model found a defect.
"""
from copy import deepcopy
import json
import re


def generated_scene_plan_control(plan):
    """Explicit new-plan schema for positive routing controls, not story quality."""
    result = deepcopy(plan)
    result['core_reading_experience'] = '测试桩：亲历人物选择及对方实际回应。'
    result['premise_test'] = '测试桩：作者前提改变人物可选行动与结果。'
    result.setdefault('chapters', [{'chapter_id': f'chapter-{index + 1}',
        'title': f'第{index + 1}章', 'scene_ids': [scene['scene_id']]}
        for index, scene in enumerate(result['scenes'])])
    for scene in result['scenes']:
        scene.setdefault('scene_role', 'core')
        scene['reading_role'] = 'support' if scene['scene_role'] == 'transition' else 'core_experience'
        if scene['scene_role'] == 'core':
            scene['dramatic_chain'] = {
                'attempt': scene['choice'], 'response': scene['consequence'],
                'adjustment': '测试桩：根据本场回应作出下一步选择。',
                'change': scene['new_information'],
                'next_effect': '测试桩：本场选择及回应接续后续行动。',
                'deletion_loss': '测试桩：失去本场明确的行动与回应。'}
        for beat in scene.get('progression_beats', []):
            beat.setdefault('response', scene['consequence'])
    by_id = {scene['scene_id']: scene for scene in result['scenes']}
    for chapter in result['chapters']:
        scene = by_id[chapter['scene_ids'][0]]
        if scene.get('progression_beats'):
            beat = scene['progression_beats'][0]
            chapter.setdefault('reading_points', [
                {'kind': 'hook', 'scene_id': scene['scene_id'], 'beat_index': 0,
                 'note': f'传输控制：本节拍已有具体待答问题“{beat["reader_question"]}”，不证明吸引力。'},
                {'kind': 'emotion', 'scene_id': scene['scene_id'], 'beat_index': 0,
                 'note': f'传输控制：将本节拍实际回应“{beat["response"]}”绑定情绪点，不证明文学质量。'}])
    return result


def chapter_checks_control(plan):
    """Source-bound transport controls, never claims about literary quality."""
    if 'chapters' not in plan:
        return []
    from short_fiction_agent.story_studio.chapter_structure import chapters
    scenes = {scene['scene_id']: scene for scene in plan['scenes']}
    rows = []
    for unit in chapters(plan, allow_legacy=False):
        scene = scenes[unit['scene_ids'][0]]
        beats = scene.get('progression_beats')
        if beats:
            index, source = 0, beats[0]
            quote = next(source[key] for key in ('action', 'response', 'change')
                         if isinstance(source.get(key), str) and len(source[key].strip()) >= 2)
        else:
            index, quote = None, scene['choice']
        rows.append({'chapter_id': unit['chapter_id'], 'status': 'effective',
            'reason': '传输正控制：仅验证逐章预算、分章及重复功能审查存在，不证明预算合理或文学吸引力。',
            'evidence': [{'scene_id': scene['scene_id'], 'beat_index': index, 'quote': quote}]})
    return rows


def premise_checks_control(contract, plan):
    """Bind the real entry and real events; do not certify their literary fit."""
    author = contract.get('author_input') or {}
    candidates = [('contract.author_input.premise', author.get('premise')),
                  ('contract.reader_promise', contract.get('reader_promise')),
                  ('contract.title', contract.get('title'))]
    path, quote = next((path, text) for path, text in candidates
                       if isinstance(text, str) and len(text.strip()) >= 2)
    audience = contract.get('audience') or {}
    expectations = audience.get('expectations') if isinstance(audience, dict) else None
    candidates = [(path, quote)] + [
        (f'contract.audience.expectations[{index}]', text)
        for index, text in enumerate(expectations if isinstance(expectations, list) else [])
        if isinstance(text, str) and text.strip()]
    scene = plan['scenes'][0]
    return [{'entry_path': path, 'entry_quote': quote,
             'expected_payoff': '传输正控制：仅绑定原始入口，不证明提炼充分或有吸引力。',
             'actual_payoff': '传输正控制：引用实际后果，不证明兑现文学期待：' + scene['consequence'],
             'remaining_effect': 'not_applicable',
             'without_remaining': '传输正控制：只验证对照字段存在，不作真实设定优劣判断。',
             'status': 'fulfilled',
             'evidence': [{'scene_id': scene['scene_id'], 'quote': scene['choice']}]}
            for path, quote in candidates]


def narrative_response(prompt, response=None):
    data = prompt
    if isinstance(prompt, str):
        # Workflow adds a JSON-only transport suffix after the actual payload.
        # Parse the complete payload, rather than assuming the last line is it.
        decoder = json.JSONDecoder()
        for match in re.finditer(r'(?m)^\s*\{', prompt):
            try:
                candidate, _ = decoder.raw_decode(prompt[match.end() - 1:])
            except json.JSONDecodeError:
                continue
            if (isinstance(candidate, dict) and 'paragraphs' in candidate
                    and ('coverage_plan' in candidate or 'response_schema' in candidate
                         or str(candidate.get('protocol', '')).startswith('chief-source-first-read-'))):
                data = candidate
                break
        else:
            raise ValueError('Narrative fixture requires the actual complete review payload')
    rows = data['paragraphs']
    ids = [r['id'] if isinstance(r, dict) else r[0] for r in rows]
    if str(data.get('protocol', '')).startswith('chief-source-first-read-'):
        # Invented observations only exercise cold-input and receipt plumbing.
        return {'reading_interest': {'text': '测试桩：当前人物如何回应选择。', 'evidence_ids': [ids[0]]},
            'core_experience': {'text': '测试桩：原文中双方作出具体回应。', 'evidence_ids': [ids[-1]]},
            'burden': {'status': 'effective', 'reason': '测试桩：只验证当前完整原段覆盖。',
                'evidence_ids': [ids[-1]], 'fix_intent': '测试桩：保留原段。'},
            'cumulative': {'status': 'effective', 'reason': '测试桩：不证明累计文学效果。',
                'evidence_ids': [ids[0]], 'fix_intent': '测试桩：保留原段。'}}
    if str(data.get('protocol', '')).startswith('source-bound-expectation-lifecycle-'):
        # Protocol fixture only: these invented explanations must never be
        # counted as a model finding or manuscript quality calibration.
        carry = data.get('carry') or data.get('verified_carry') or {}
        prior = carry.get('questions', [])
        qid = prior[0]['question_id'] if prior else 'fixture-q'
        fresh = not prior
        def interval(keys):
            return {'interval_id': 'fixture-' + keys[0], 'start_id': keys[0], 'end_id': keys[-1],
                'known_at_start': '测试桩：先前状态只用于协议控制。',
                'active_question_ids': [qid], 'local_evidence_ids': [keys[0]],
                'reading_value': '测试桩：该区间实际选择与回应。',
                'counterevidence_ids': [], 'counterevidence': '测试桩：不假造阅读效果。',
                'verdict': 'effective', 'reason': '测试桩：仅验证来源与回执。'}
        windows = [interval(ids[:1]), interval(ids[1:])] if fresh and len(ids) > 1 else [interval(ids)]
        return {'protocol': data['protocol'],
            'questions': [{'question_id': qid, 'question': '测试桩：人物如何回应实际选择？'}] if fresh else [],
            'events': [{'event_id': 'fixture-event-' + ids[0], 'question_id': qid,
                'evidence_ids': [ids[0]], 'type': 'opened',
                'reader_now_knows': '测试桩：仅当前来源。'}] if fresh else [],
            'intervals': windows, 'overall': {'verdict': 'effective',
                'weakest_interval_id': windows[0]['interval_id'], 'reason': '测试桩：协议覆盖完整。',
                'counterevidence': '测试桩：无文学效果证明。', 'limitations': '测试桩：不可用于语义验收。'}}
    phases = data['coverage_plan']['phases']
    phase_ids = {phase: [key for key in ids if int(bound['start_id'][1:]) <= int(key[1:])
                         <= int(bound['end_id'][1:])] for phase, bound in phases.items()}
    result = deepcopy(response if response is not None else {
        'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []})
    if 'reading_turn' in data:
        result.setdefault('facts', [])
    def judgment(**kwargs):
        return {'verdict': 'effective', 'reason': '测试桩：人物选择改变下一步行动。',
                'counterevidence_ids': [], 'counterevidence': '测试桩：安静回应有实际关系作用，不以没有新灾难判拖延。',
                'repair_scope': 'none', **kwargs}
    def scene(role, key):
        return judgment(role=role, start_id=key, end_id=key,
            attempt_ids=[key], response_ids=[key], turn_ids=[key], payoff_ids=[key])
    core = [scene('weakest_core', phase_ids['middle'][0])]
    if data.get('narrative_scope') == 'full':
        core.append(scene('ending', phase_ids['ending'][-1]))
    result['narrative_audit'] = {
        'coverage': [{'phase': phase, 'evidence_ids': [phase_ids[phase][0]],
            'change': '测试桩：原段实际改变选择或关系。',
            'promise_link': '测试桩：变化回应作者承诺。'} for phase, bound in phases.items()],
        'mainline': judgment(question='测试桩：双方能否作出共同生活的具体选择？', evidence_ids=[ids[0]]),
        'chapter_handoffs': [judgment(boundary_id=boundary['boundary_id'],
            before_question='测试桩：双方能否回应此前选择？', actual_change='测试桩：当前原段给出回应。',
            next_question='' if boundary['is_final'] else '测试桩：双方接下来能否履行新的共同决定？',
            evidence_ids=[boundary['boundary_id']]) for boundary in data['coverage_plan']['chapter_endings']],
        'weakest_middle': judgment(evidence_ids=[phase_ids['middle'][0]], compression={
            'compressed_version': '测试桩：保留当段事实和双方选择回应的压缩对照。',
            'preserved_fact_ids': [phase_ids['middle'][0]], 'choice_response_ids': [phase_ids['middle'][0]],
            'lost_value': '测试桩：原展开保留可观察的安静回应及共同选择，不能只按动作数判拖延。'}),
        'core_scenes': core,
        'repetition': judgment(earlier_ids=[ids[0]], later_ids=[ids[-1]])}
    if data.get('narrative_scope') == 'full':
        result.setdefault('issue_dispositions', [{'issue_id': item['issue_id'], 'status': 'context_explained',
            'evidence_ids': [ids[0]], 'reason': '测试桩：原段实际的选择和回应解释本次异议；仅用于协议控制流。'}
            for item in data.get('editorial_issue_registry', [])])
    return result
