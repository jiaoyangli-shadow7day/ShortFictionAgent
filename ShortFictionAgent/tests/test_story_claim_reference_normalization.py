"""Expand explicit source references, without inventing evidence or truth states."""
import copy
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json


class ExplicitReferenceTests(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(['他按旧记录预测斑鸠全程领先。', '他提醒承诺只针对原赛道。',
            '赛道改造后开跑，翠鸟先领先。', '斑鸠中途反超。',
            '斑鸠夺冠。他承认改赛道后的预测失准，照约赔礼。', '次日他再次确认结果。'])
        self.raw = {'kind': 'prediction', 'subject': '本次赛跑索引', 'statement': '旧赛道预测与实际赛况须核对。',
            'comparison_scope': 'process_and_outcome', 'assertion_ids': ['p0'], 'condition_ids': ['p1'],
            'process_ids': ['p2'], 'outcome_ids': ['p4'], 'event_start_id': 'p2', 'event_end_id': 'p4',
            'status': 'conflict', 'reason': '初读未完成语义核对。'}

    def checked(self, raw, allowed=None):
        return chief.validate_response({'recommendation': 'ready', 'issues': [],
            'claims': [raw], 'observations': []}, self.body, allowed_ids=allowed)

    def plan(self, raw=None):
        reading = self.checked(self.raw if raw is None else raw)
        return audit.build_audit_plan(self.body, [reading, {'claims': [], 'observations': []}],
                                      max_prompt_chars=20000, render_prompt=canonical_json)

    def final_raw(self, **changes):
        raw = {**self.raw, 'status': 'verified', 'reason': '原文明示赛道改变和预测失准后果。',
            'recheck': {'reason': '核对原承诺、条件和完整实际经过，失败已有解释。',
                        'evidence_ids': ['p0', 'p1', 'p4']}}
        raw.update(changes)
        return raw

    def test_explicit_delivered_range_expands_with_immutable_raw_and_source_trace(self):
        raw = self.final_raw()
        original = copy.deepcopy(raw)
        final = self.checked(raw)
        claim = final['claims'][0]
        recheck = claim['recheck']
        self.assertEqual(raw, original)
        self.assertEqual(recheck['raw_evidence_ids'], ['p0', 'p1', 'p4'])
        self.assertEqual(recheck['old_binding_evidence_ids'], ['p0', 'p1', 'p2', 'p3', 'p4'])
        self.assertEqual(recheck['evidence_ids'], ['p0', 'p1', 'p2', 'p3', 'p4'])
        trace = recheck['citation_normalizations'][0]
        self.assertEqual(trace['declared_range'], ['p2', 'p4'])
        self.assertEqual(trace['expanded_ids'], ['p2', 'p3', 'p4'])
        self.assertEqual(trace['delivered_source_ids'], ['p2', 'p3', 'p4'])
        self.assertEqual(trace['source_hash'], claim['source_hash'])
        self.assertEqual(recheck['evidence'], chief.paragraphs(self.body)[:5])
        self.assertEqual(audit.final_gate(final, self.plan())['recommendation'], 'ready')

    def test_incomplete_delivery_does_not_expand_or_claim_full_event(self):
        final = self.checked(self.final_raw(), ['p0', 'p1', 'p2', 'p4', 'p5'])
        claim = final['claims'][0]
        self.assertEqual(claim['verification_status'], 'pending')
        self.assertNotIn('p3', claim['recheck']['evidence_ids'])
        self.assertFalse(any(c['kind'] == 'explicit_delivered_event_range'
                             for c in claim['recheck']['citation_normalizations']))
        with self.assertRaisesRegex(StoryError, '先前完整证据'):
            audit.final_gate(final, self.plan())
        for bounds in ({'event_start_id': 'p4', 'event_end_id': 'p2'},
                       {'event_start_id': 'p2', 'event_end_id': 'p999'},
                       {'event_start_id': 'p2', 'event_end_id': None}):
            with self.subTest(bounds=bounds), self.assertRaises(StoryError):
                self.checked(self.final_raw(**bounds))

    def test_explicit_new_role_reference_satisfies_only_new_check_coverage(self):
        initial = {**self.raw, 'comparison_scope': 'outcome'}
        initial.pop('event_start_id'); initial.pop('event_end_id')
        raw = {**initial, 'status': 'verified', 'process_ids': ['p2', 'p3'],
            'recheck': {'reason': '保留旧端点并核对新增实际过程。', 'evidence_ids': ['p0', 'p1', 'p2', 'p4']}}
        final = self.checked(raw)
        recheck = final['claims'][0]['recheck']
        self.assertNotIn('p3', recheck['old_binding_evidence_ids'])
        self.assertIn('p3', recheck['evidence_ids'])
        self.assertTrue(any(c.get('paragraph_id') == 'p3' and c['source_path'] == 'claims[0].process_ids[1]'
                            for c in recheck['citation_normalizations']))
        self.assertEqual(audit.final_gate(final, self.plan(initial))['recommendation'], 'ready')
        raw['recheck']['evidence_ids'].remove('p0')
        missing_old = self.checked(raw)
        self.assertIn('p0', missing_old['claims'][0]['recheck']['evidence_ids'])
        self.assertNotIn('p0', missing_old['claims'][0]['recheck']['old_binding_evidence_ids'])
        with self.assertRaisesRegex(StoryError, '原断言'):
            audit.final_gate(missing_old, self.plan(initial))

    def test_expansion_does_not_repair_outcome_outside_declared_window_or_change_state(self):
        self.body += '\n与赛事无关的后记。'
        initial = {**self.raw, 'status': 'pending'}
        plan = self.plan(initial)
        checks = audit.validate_reread_response({'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'pending', 'reason': '仍未完整核验。',
            'process_ids': ['p2'], 'outcome_ids': ['p4'], 'event_start_id': 'p2', 'event_end_id': 'p4'}]},
            self.body, plan)
        final = self.checked(self.final_raw(outcome_ids=['p4', 'p5'],
            recheck={'reason': '保留全部旧端点及新增结果。', 'evidence_ids': ['p0', 'p1', 'p4', 'p5']}),
            ['p0', 'p1', 'p2', 'p3', 'p4', 'p5'])
        claim = final['claims'][0]
        self.assertEqual(claim['status'], 'verified')
        self.assertEqual(claim['verification_status'], 'pending')
        self.assertEqual(claim['event_end_id'], 'p4')
        effective = audit.final_gate(final, plan, checks)
        self.assertEqual(effective['recommendation'], 'uncertain')
        self.assertEqual(effective['claim_gate']['pending_ids'], plan['claim_ids'])

    def test_complete_explicit_references_do_not_change_a_model_conflict(self):
        final = self.checked(self.final_raw(status='conflict', reason='本次原文核对确认冲突。'))
        effective = audit.final_gate(final, self.plan())
        self.assertEqual(effective['recommendation'], 'revise')
        self.assertEqual(effective['claim_gate']['conflict_ids'], [final['claims'][0]['claim_id']])
        self.assertEqual(effective['issues'][0]['reason'], '本次原文核对确认冲突。')


if __name__ == '__main__':
    unittest.main()
