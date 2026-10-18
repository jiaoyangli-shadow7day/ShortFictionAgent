"""Claim protocol/provenance controls; scripted statuses are not semantic evals."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json, digest


def claim(**changes):
    return {**{'kind': 'prediction', 'subject': '山道赛跑', 'statement': '斑鸠会全程领先。',
        'comparison_scope': 'process_and_outcome',
        'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': [], 'outcome_ids': [],
        'status': 'pending', 'reason': '须对照完整比赛过程。'}, **changes}


def response(claims=None, observations=None, **changes):
    return {**{'recommendation': 'ready', 'issues': [], 'claims': claims or [],
               'observations': observations or []}, **changes}


def render(context):
    return '同一主审核对原断言及实际经过。\n' + canonical_json(context)


class ClaimAuditTests(unittest.TestCase):
    def setUp(self):
        self.body = '  他保证斑鸠会全程领先。\n翠鸟从起点领先，斑鸠随后反超。\n斑鸠先到终点。\t'

    def normalized(self, claims=None, observations=None, **changes):
        return chief.validate_response(response(claims, observations, **changes), self.body)

    def plan(self, readings, **changes):
        return audit.build_audit_plan(self.body, readings, max_prompt_chars=changes.get('capacity', 10000),
                                      render_prompt=render)

    def test_all_chief_responses_require_explicit_arrays_and_keep_full_source_binding(self):
        for missing in ('claims', 'observations'):
            raw = response()
            raw.pop(missing)
            with self.subTest(missing=missing), self.assertRaisesRegex(StoryError, 'claims和observations'):
                chief.validate_response(raw, self.body)
        raw = response([claim()])
        original = copy.deepcopy(raw)
        checked = chief.validate_response(raw, self.body)
        evidence = checked['claims'][0]['evidence']['assertion'][0]
        self.assertEqual(evidence, chief.paragraphs(self.body)[0])
        self.assertEqual(self.body[slice(*evidence['span'])], evidence['text'])
        self.assertEqual(checked['claims'][0]['source_hash'], digest(self.body))
        self.assertEqual(raw, original)

    def test_unknown_duplicate_outside_ids_and_claim_id_impersonation_fail(self):
        for mutation in ({'assertion_ids': ['p999']}, {'process_ids': ['p1', 'p1']},
                         {'claim_id': 'some-other-claim'}, {'search_terms': [None]}):
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                self.normalized([claim(**mutation)])
        with self.assertRaisesRegex(StoryError, '本次未读'):
            chief.validate_response(response([claim(outcome_ids=['p2'])]), self.body, allowed_ids=['p0'])

    def test_empty_claims_do_not_force_ordinary_omissions_or_new_calls(self):
        checked = self.normalized()
        plan = self.plan([checked])
        self.assertFalse(plan['requires_reread'])
        self.assertIsNone(plan['prompt'])
        self.assertEqual(audit.final_gate(checked, plan)['recommendation'], 'ready')

    def test_single_complete_verified_claim_finishes_without_extra_call(self):
        checked = self.normalized([claim(status='verified', process_ids=['p1'], outcome_ids=['p2'],
            reason='测试桩：叙述明确交代他此前在吹牛，落败和不可靠已有解释。')])
        plan = self.plan([checked])
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(audit.final_gate(checked, plan)['recommendation'], 'ready')
        self.assertEqual(plan['initial_checks'][0]['status'], 'verified')

    def test_prediction_without_process_evidence_stays_pending(self):
        checked = self.normalized([claim(status='verified', process_ids=[], outcome_ids=['p2'])])
        self.assertEqual(checked['claims'][0]['verification_status'], 'pending')
        plan = self.plan([checked])
        self.assertTrue(plan['requires_reread'])
        self.assertEqual(audit.final_gate(checked, plan)['recommendation'], 'uncertain')

    def test_one_complete_paragraph_can_supply_both_full_process_and_outcome(self):
        self.body = '他保证斑鸠全程领先。\n开闸后斑鸠立即领先，一直未被反超，率先冲过终点夺冠。\n第三章\n他们整理旧账册。'
        fields = {'process_ids': ['p1'], 'outcome_ids': ['p1'],
                  'event_start_id': 'p1', 'event_end_id': 'p1'}
        checked = chief.validate_response(response([claim(status='verified', **fields)]), self.body,
                                          allowed_ids=['p0', 'p1'])
        plan = self.plan([checked, chief.validate_response(response(), self.body, allowed_ids=['p2', 'p3'])])
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(audit.final_gate(self.normalized(), plan)['recommendation'], 'ready')
        pending = self.normalized([claim(**fields)])
        reread_plan = self.plan([pending])
        checks = audit.validate_reread_response({'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': reread_plan['claim_ids'][0], 'status': 'verified', 'reason': '测试桩：同一段完整呈现起跑至夺冠。',
            **fields}]}, self.body, reread_plan)
        self.assertEqual(checks['claim_checks'][0]['status'], 'verified')
        self.assertEqual(checks['claim_checks'][0]['evidence'], chief.paragraphs(self.body)[:2])

    def test_short_pending_reopens_complete_source_even_without_observations(self):
        checked = self.normalized([claim()])
        plan = self.plan([checked])
        payload = json.loads(plan['prompt'].rsplit('\n', 1)[1])
        self.assertEqual(payload['paragraphs'], [[row['id'], row['text']] for row in chief.paragraphs(self.body)])
        self.assertEqual(plan['allowed_ids'], ['p0', 'p1', 'p2'])
        self.assertEqual(plan['full_context_claim_ids'], plan['claim_ids'])

    def test_long_event_context_preserves_start_to_result_without_unrelated_chapters(self):
        parts = ['他保证斑鸠会全程领先。', '第一章'] + ['他们讨论山下的旧仓库。' * 20] * 25
        event_start = len(parts)
        parts += ['第二章', '翠鸟率先跑出起点。', '斑鸠在中途才反超。', '斑鸠先到终点。', '第三章']
        parts += ['他们重新整理另一本旧账册。' * 20] * 25
        body = '\n'.join(parts)
        winner = event_start + 3
        normalized = chief.validate_response(response([claim(outcome_ids=[f'p{winner}'])]), body)
        plan = audit.build_audit_plan(body, [normalized], max_prompt_chars=2300, render_prompt=render)
        self.assertTrue(plan['requires_reread'])
        self.assertLessEqual(len(plan['prompt']), 2300)
        self.assertTrue({f'p{i}' for i in range(event_start, winner + 1)} <= set(plan['allowed_ids']))
        self.assertIn('p0', plan['allowed_ids'])
        self.assertNotIn('p12', plan['allowed_ids'])
        self.assertNotIn(f'p{winner + 10}', plan['allowed_ids'])
        source = {row['id']: row['text'] for row in chief.paragraphs(body)}
        payload = json.loads(plan['prompt'].rsplit('\n', 1)[1])
        self.assertTrue(all(source[key] == text for key, text in payload['paragraphs']))

    def test_reread_conflict_produces_model_grounded_blocker_and_old_pending_cannot_override(self):
        initial = self.normalized([claim()])
        plan = self.plan([initial])
        raw = {'recommendation': 'revise', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'conflict',
            'reason': '全程领先与另一只鸟先领先、后来才反超冲突；叙述却当作完全兑现。',
            'process_ids': ['p1'], 'outcome_ids': ['p2']}]}
        original = copy.deepcopy(raw)
        checks = audit.validate_reread_response(raw, self.body, plan)
        final = audit.final_gate(initial, plan, checks)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['claim_gate']['model_recommendation'], 'ready')
        self.assertEqual(final['issues'][0]['reason'], raw['claim_checks'][0]['reason'])
        self.assertEqual(final['issues'][0]['origin'], 'claim_reread')
        self.assertEqual(final['issues'][0]['evidence'], chief.paragraphs(self.body))
        self.assertEqual(raw, original)
        with self.assertRaisesRegex(StoryError, '没有有据阻断'):
            chief.validate_response(response(recommendation='revise'), self.body)

    def test_reread_rejects_missing_unknown_duplicate_ids_and_changed_source(self):
        plan = self.plan([self.normalized([claim()])])
        valid = {'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'verified', 'reason': '已核对。',
            'process_ids': ['p1'], 'outcome_ids': ['p2']}]}
        for bad in ([], valid['claim_checks'] * 2,
                    [{**valid['claim_checks'][0], 'claim_id': 'unknown-claim'}]):
            with self.subTest(bad=bad), self.assertRaises(StoryError):
                audit.validate_reread_response({**valid, 'claim_checks': bad}, self.body, plan)
        with self.assertRaisesRegex(StoryError, '正文绑定'):
            audit.validate_reread_response(valid, self.body + '\n后来又发生了一件事。', plan)

    def test_new_final_pending_or_conflict_is_not_swallowed_by_existing_verified_checks(self):
        initial = self.normalized([claim(status='verified', process_ids=['p1'], outcome_ids=['p2'])])
        plan = self.plan([initial])
        for status in ('pending', 'conflict'):
            new = self.normalized([claim(subject='另一项需核对的承诺', status=status,
                process_ids=['p1'], outcome_ids=['p2'], reason='测试桩：最终阶段新发现的问题。')])
            final = audit.final_gate(new, plan)
            self.assertEqual(final['recommendation'], 'uncertain' if status == 'pending' else 'revise')
            self.assertEqual(len(final['claim_gate']['claim_checks']), 2)

    def test_capacity_cannot_turn_unserved_claim_into_verified(self):
        initial = self.normalized([claim()])
        plan = self.plan([initial], capacity=50)
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(plan['unserved_claim_ids'], plan['claim_ids'])
        final = audit.final_gate(initial, plan)
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertEqual(final['claim_gate']['pending_ids'], plan['claim_ids'])

    def test_weak_separator_cannot_hide_event_start_or_certify_complete_process(self):
        self.body = '\n'.join(['他保证斑鸠会全程领先。', '翠鸟先跑出起点并领先。', '……',
            '斑鸠在后半程反超。', '斑鸠获胜。', '第三章'] + ['他们整理另一本旧账册。' * 30] * 30)
        initial = chief.validate_response(response([claim(process_ids=['p3'], outcome_ids=['p4'])]),
                                          self.body, allowed_ids=[f'p{i}' for i in range(5)])
        plan = self.plan([initial], capacity=3000)
        self.assertIn('p1', plan['allowed_ids'])
        self.assertNotIn(plan['claim_ids'][0], plan['full_context_claim_ids'])
        check = {'claim_id': plan['claim_ids'][0], 'status': 'verified', 'reason': '测试桩：只引反超和获胜。',
                 'process_ids': ['p3'], 'outcome_ids': ['p4']}
        result = audit.validate_reread_response({'recommendation': 'ready', 'issues': [],
                                                 'claim_checks': [check]}, self.body, plan)
        self.assertEqual(result['claim_checks'][0]['status'], 'pending')
        complete_check = {**check, 'status': 'conflict', 'reason': '测试桩：起步领先者不同，原叙述未解释。',
                          'event_start_id': 'p1', 'event_end_id': 'p4'}
        checked = audit.validate_reread_response({'recommendation': 'revise', 'issues': [],
            'claim_checks': [complete_check]}, self.body, plan)
        self.assertEqual(checked['claim_checks'][0]['status'], 'conflict')
        self.assertEqual(checked['claim_checks'][0]['evidence_ids'], [f'p{i}' for i in range(5)])
        self.assertEqual(checked['claim_checks'][0]['evidence'], chief.paragraphs(self.body)[:5])

    def test_explicit_actual_event_window_fits_without_reopening_whole_long_chapter(self):
        parts = ['他保证斑鸠会赢。', '第一章'] + ['他们讨论与比赛无关的旧仓库。' * 30] * 20
        start = len(parts)
        parts += ['比赛开跑，翠鸟率先冲出。', '斑鸠追上并超越翠鸟。', '斑鸠先到终点。']
        parts += ['他们整理与比赛无关的旧账册。' * 30] * 20
        self.body = '\n'.join(parts)
        initial = self.normalized([claim(statement='斑鸠会赢。', process_ids=[f'p{start}'],
            outcome_ids=[f'p{start + 2}'], event_start_id=f'p{start}', event_end_id=f'p{start + 2}')])
        plan = self.plan([initial], capacity=3000)
        self.assertTrue(plan['requires_reread'])
        self.assertEqual(plan['full_context_claim_ids'], plan['claim_ids'])
        self.assertTrue({f'p{i}' for i in range(start, start + 3)} <= set(plan['allowed_ids']))
        self.assertNotIn('p2', plan['allowed_ids'])
        self.assertNotIn(f'p{start + 3}', plan['allowed_ids'])
        self.assertLessEqual(len(plan['prompt']), 3000)

    def test_multiturn_local_verified_claim_requires_explicit_complete_actual_event(self):
        self.body += '\n第三章\n' + '\n'.join(['他们整理旧账册。' * 30] * 30)
        local = chief.validate_response(response([claim(status='verified', process_ids=['p1'],
            outcome_ids=['p2'], event_start_id='p1', event_end_id='p2')]), self.body,
            allowed_ids=['p0', 'p1', 'p2'])
        other = chief.validate_response(response(), self.body, allowed_ids=['p3', 'p4'])
        plan = self.plan([local, other], capacity=3000)
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(plan['initial_checks'][0]['status'], 'verified')
        without_bounds = chief.validate_response(response([claim(status='verified',
            process_ids=['p1'], outcome_ids=['p2'])]), self.body, allowed_ids=['p0', 'p1', 'p2'])
        unresolved = self.plan([without_bounds, other], capacity=3000)
        self.assertTrue(unresolved['requires_reread'])
        self.assertEqual(unresolved['initial_checks'], [])
        self.assertEqual(unresolved['full_context_claim_ids'], [])

    def test_mixed_initial_and_reread_checks_are_preserved_without_duplicate_review(self):
        self.body = '\n'.join(['他保证斑鸠会赢。', '斑鸠率先跑出。', '斑鸠获胜。', '第二章',
            '她保证翠鸟会赢。', '翠鸟率先跑出。', '翠鸟获胜。'] + ['他们整理旧账册。' * 30] * 30)
        first = chief.validate_response(response([claim(status='verified', process_ids=['p1'],
            outcome_ids=['p2'], event_start_id='p1', event_end_id='p2')]), self.body,
            allowed_ids=['p0', 'p1', 'p2'])
        second = chief.validate_response(response([claim(subject='另一场赛跑', assertion_ids=['p4'],
            process_ids=['p5'], outcome_ids=['p6'], event_start_id='p5', event_end_id='p6')]), self.body,
            allowed_ids=['p3', 'p4', 'p5', 'p6'])
        plan = self.plan([first, second], capacity=4500)
        self.assertEqual(len(plan['claim_ids']), 2)
        self.assertEqual(plan['reread_claim_ids'], [second['claims'][0]['claim_id']])
        checks = audit.validate_reread_response({'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': plan['reread_claim_ids'][0], 'status': 'verified', 'reason': '测试桩：已核对第二场。',
            'process_ids': ['p5'], 'outcome_ids': ['p6']}]}, self.body, plan)
        final = audit.final_gate(self.normalized(), plan, checks)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertEqual(len(final['claim_gate']['claim_checks']), 2)
        prompt, allowed = chief.synthesis_prompt(self.body, {}, [{**first, 'facts': []}, {**second, 'facts': []}],
            claim_audit={'plan': plan, 'checks': checks}, max_prompt_chars=15000)
        supplied = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertEqual({item['claim_id'] for item in supplied['claim_audit']['registry']},
                         set(plan['claim_ids']))
        self.assertTrue({'p0', 'p1', 'p2', 'p4', 'p5', 'p6'} <= set(allowed))

    def test_verified_conflict_cannot_be_downgraded_by_synthesis_pending_or_ready(self):
        initial = self.normalized([claim()])
        plan = self.plan([initial, self.normalized()])
        checks = audit.validate_reread_response({'recommendation': 'revise', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'conflict', 'reason': '测试桩：完整原文确认关键经过相反。',
            'process_ids': ['p1'], 'outcome_ids': ['p2']}]}, self.body, plan)
        for status in ('pending', 'verified'):
            with self.subTest(final_status=status):
                final = audit.final_gate(self.normalized([claim(status=status,
                    process_ids=['p1'], outcome_ids=['p2'])]), plan, checks)
                self.assertEqual(final['recommendation'], 'revise')
                self.assertEqual(final['claim_gate']['conflict_ids'], plan['claim_ids'])
                self.assertEqual(final['issues'][0]['reason'], checks['claim_checks'][0]['reason'])
                self.assertEqual(final['issues'][0]['evidence'], checks['claim_checks'][0]['evidence'])

    def test_event_bounds_reject_unknown_reversed_and_keep_partial_context_pending(self):
        for changes in ({'event_start_id': 'p1'}, {'event_start_id': 'p999', 'event_end_id': 'p2'},
                        {'event_start_id': 'p2', 'event_end_id': 'p1'}):
            with self.subTest(changes=changes), self.assertRaises(StoryError):
                self.normalized([claim(process_ids=['p1'], outcome_ids=['p2'], **changes)])
        partial = chief.validate_response(response([claim(status='verified', process_ids=['p0'],
            outcome_ids=['p2'], event_start_id='p0', event_end_id='p2')]), self.body,
            allowed_ids=['p0', 'p2'])
        self.assertFalse(partial['claims'][0]['event_context_complete'])
        self.assertEqual(partial['claims'][0]['verification_status'], 'pending')

    def test_legal_narrow_process_window_stays_pending_and_one_reread_can_repair_it(self):
        self.body = '\n'.join(['他保证斑鸠全程领先。', '翠鸟率先冲出起点。', '……',
            '斑鸠从后半程反超。', '斑鸠获胜。', '第三章'] + ['他们整理旧账册。' * 30] * 30)
        initial = self.normalized([claim(status='verified', process_ids=['p1', 'p3'], outcome_ids=['p4'],
            event_start_id='p3', event_end_id='p4')])
        item = initial['claims'][0]
        self.assertFalse(item['event_anchor_coverage'])
        self.assertEqual(item['verification_status'], 'pending')
        self.assertEqual(item['process_ids'], ['p1', 'p3'])
        plan = self.plan([initial], capacity=3500)
        self.assertTrue(plan['requires_reread'])
        self.assertNotIn(item['claim_id'], plan['full_context_claim_ids'])
        self.assertTrue({'p0', 'p1', 'p2', 'p3', 'p4'} <= set(plan['allowed_ids']))
        check = {'claim_id': item['claim_id'], 'status': 'verified', 'reason': '测试桩：旧窄窗尚未改正。',
                 'process_ids': ['p3'], 'outcome_ids': ['p4']}
        raw = {'recommendation': 'ready', 'issues': [], 'claim_checks': [check]}
        still_pending = audit.validate_reread_response(raw, self.body, plan)
        self.assertEqual(still_pending['claim_checks'][0]['status'], 'pending')
        self.assertIn('p1', still_pending['claim_checks'][0]['evidence_ids'])
        for fixed in ({'event_start_id': 'p3', 'event_end_id': 'p4'},
                      {'event_start_id': None, 'event_end_id': None}):
            invalid_fix = audit.validate_reread_response({**raw, 'claim_checks': [{**check, **fixed}]},
                                                        self.body, plan)
            self.assertEqual(invalid_fix['claim_checks'][0]['status'], 'pending')
        fixed_check = {**check, 'event_start_id': 'p1', 'event_end_id': 'p4', 'status': 'conflict',
                       'reason': '测试桩：完整起步至夺冠与全程领先承诺冲突。'}
        fixed = audit.validate_reread_response({**raw, 'claim_checks': [fixed_check]}, self.body, plan)
        self.assertEqual(fixed['claim_checks'][0]['status'], 'conflict')
        final = audit.final_gate(initial, plan, fixed)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['issues'][0]['evidence'], chief.paragraphs(self.body)[:5])

    def test_outcome_window_can_keep_supplemental_process_outside_the_actual_result_event(self):
        self.body = '我交出四万元本金。\n会计记下我的投注。\n其他人在操场等候。\n兑奖处开始结算。\n会计收到本金和奖金。\n我签收了全部款项。'
        initial = self.normalized([claim(kind='cashflow', comparison_scope='outcome', status='verified',
            statement='本金和奖金已结清。', process_ids=['p1', 'p4'], outcome_ids=['p5'],
            event_start_id='p3', event_end_id='p5')])
        self.assertEqual(initial['claims'][0]['verification_status'], 'verified')
        self.assertTrue(initial['claims'][0]['event_anchor_coverage'])
        plan = self.plan([initial])
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(plan['initial_checks'][0]['evidence_ids'], ['p0', 'p1', 'p3', 'p4', 'p5'])
        self.assertEqual(audit.final_gate(initial, plan)['recommendation'], 'ready')
        missing_result = self.normalized([claim(kind='cashflow', comparison_scope='outcome', status='verified',
            statement='本金和奖金已结清。', process_ids=['p1', 'p4'], outcome_ids=['p5'],
            event_start_id='p3', event_end_id='p4')])
        self.assertEqual(missing_result['claims'][0]['verification_status'], 'pending')
        pending_plan = self.plan([missing_result])
        raw_check = {'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': pending_plan['claim_ids'][0], 'status': 'verified', 'reason': '测试桩：原结果已核对，修正事件结束位置。',
            'process_ids': ['p4'], 'outcome_ids': ['p5'], 'event_start_id': 'p3', 'event_end_id': 'p5'}]}
        original = copy.deepcopy(raw_check)
        checks = audit.validate_reread_response(raw_check, self.body, pending_plan)
        self.assertEqual(checks['claim_checks'][0]['status'], 'verified')
        self.assertIn('p1', checks['claim_checks'][0]['evidence_ids'])
        self.assertEqual(checks['claim_checks'][0]['outcome_ids'], ['p5'])
        self.assertEqual(checks['claim_checks'][0]['process_ids'], ['p4'])
        self.assertEqual(raw_check, original)

    def test_new_synthesis_claim_cannot_certify_sparse_minimum_span_without_actual_event(self):
        self.body += '\n第三章\n他们整理另一本旧账册。'
        plan = self.plan([self.normalized(), self.normalized()])
        for with_bounds in (False, True):
            fields = {'event_start_id': 'p1', 'event_end_id': 'p2'} if with_bounds else {}
            new = chief.validate_response(response([claim(status='verified', process_ids=['p1'],
                outcome_ids=['p2'], **fields)]), self.body, allowed_ids=['p0', 'p1', 'p2'])
            self.assertEqual(new['claims'][0]['verification_status'], 'verified' if with_bounds else 'pending')
            final = audit.final_gate(new, plan)
            self.assertEqual(final['recommendation'], 'ready' if with_bounds else 'uncertain')

    def test_result_only_prediction_needs_endpoints_without_inventing_a_process(self):
        self.body = '他预测斑鸠会赢。\n赛后公布冠军是斑鸠。\n第三章\n他们整理旧账册。'
        initial = chief.validate_response(response([claim(statement='斑鸠会赢。',
            comparison_scope='outcome', status='verified', outcome_ids=['p1'])]), self.body,
            allowed_ids=['p0', 'p1'])
        other = chief.validate_response(response(), self.body, allowed_ids=['p2', 'p3'])
        plan = self.plan([initial, other])
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(plan['initial_checks'][0]['comparison_scope'], 'outcome')
        self.assertEqual(plan['initial_checks'][0]['process_ids'], [])
        self.assertEqual(audit.final_gate(self.normalized(), plan)['recommendation'], 'ready')

    def test_full_process_prediction_without_process_stays_pending_after_only_reread(self):
        self.body = '他保证斑鸠全程领先。\n斑鸠获胜并接受采访。'
        initial = self.normalized([claim(status='verified', outcome_ids=['p1'])])
        plan = self.plan([initial])
        self.assertTrue(plan['requires_reread'])
        checks = audit.validate_reread_response({'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'verified', 'reason': '测试桩：正文只给冠军结果。',
            'process_ids': [], 'outcome_ids': ['p1']}]}, self.body, plan)
        self.assertEqual(checks['claim_checks'][0]['status'], 'pending')
        self.assertEqual(audit.final_gate(initial, plan, checks)['recommendation'], 'uncertain')

    def test_prediction_requires_scope_and_reread_or_synthesis_cannot_downgrade_it(self):
        missing = claim()
        missing.pop('comparison_scope')
        with self.assertRaisesRegex(StoryError, 'comparison_scope'):
            self.normalized([missing])
        initial = self.normalized([claim()])
        plan = self.plan([initial])
        raw_check = {'claim_id': plan['claim_ids'][0], 'status': 'verified', 'reason': '测试桩：只核对结果。',
            'comparison_scope': 'outcome', 'process_ids': [], 'outcome_ids': ['p2']}
        with self.assertRaisesRegex(StoryError, '不能改变.*comparison_scope'):
            audit.validate_reread_response({'recommendation': 'ready', 'issues': [],
                'claim_checks': [raw_check]}, self.body, plan)
        reduced = self.normalized([claim(comparison_scope='outcome', status='verified', outcome_ids=['p2'])])
        self.assertEqual(reduced['claims'][0]['claim_id'], plan['claim_ids'][0])
        with self.assertRaisesRegex(StoryError, '不能改变.*comparison_scope'):
            audit.final_gate(reduced, plan)

    def test_outcome_scope_keeps_original_conditions_and_nonprediction_defaults(self):
        self.body = '他限定只有不下雨时预测才有效。\n他预测斑鸠会赢。\n他们讨论旧账册。\n晴天赛后公布冠军是斑鸠。'
        original = claim(statement='不下雨时斑鸠会赢。', comparison_scope='outcome',
            assertion_ids=['p1'], condition_ids=['p0'], outcome_ids=['p3'], status='verified')
        checked = chief.validate_response(response([original]), self.body, allowed_ids=['p0', 'p1', 'p3'])
        self.assertEqual(checked['claims'][0]['verification_status'], 'verified')
        plan = self.plan([checked, chief.validate_response(response(), self.body, allowed_ids=['p2'])])
        self.assertFalse(plan['requires_reread'])
        self.assertEqual(plan['initial_checks'][0]['evidence_ids'], ['p0', 'p1', 'p3'])
        other = {**original, 'kind': 'constraint'}
        other.pop('comparison_scope')
        self.assertEqual(self.normalized([other])['claims'][0]['comparison_scope'], 'outcome')


if __name__ == '__main__':
    unittest.main()
