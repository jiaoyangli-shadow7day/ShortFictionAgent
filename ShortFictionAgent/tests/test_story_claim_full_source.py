"""Explicit same-editor full-source rechecks can resolve window metadata limits."""
import copy
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json, digest


class FullSourceRecheckTests(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(['他预测斑鸠将在末段反超。', '预测对应今天的原赛道。', '比赛从发令开始。',
            '翠鸟先领先，斑鸠随后反超。', '斑鸠冲线，裁判正式宣布它夺冠。', '观众安静下来。',
            '朋友对照纸条，再次确认预测命中。', '第二章'])
        self.raw = {'kind': 'prediction', 'subject': '当日赛跑', 'statement': '预测与现场经过和结果一致。',
            'comparison_scope': 'process_and_outcome', 'assertion_ids': ['p0'], 'condition_ids': ['p1'],
            'process_ids': ['p2', 'p3'], 'outcome_ids': ['p4', 'p6'], 'event_start_id': 'p2',
            'event_end_id': 'p4', 'status': 'verified', 'reason': '完整原文核对后认为叙述成立。'}

    def normalized(self, allowed=None, **changes):
        return chief.validate_response({'recommendation': 'ready', 'issues': [], 'claims': [
            {**self.raw, **changes}], 'observations': []}, self.body, allowed_ids=allowed)

    def plan(self, status='verified', outcome=None):
        first = self.normalized(['p0', 'p1', 'p2', 'p3', 'p4', 'p5', 'p6'], status=status,
                                outcome_ids=self.raw['outcome_ids'] if outcome is None else outcome)
        second = chief.validate_response({'recommendation': 'ready', 'issues': [], 'claims': [],
            'observations': []}, self.body, allowed_ids=['p7'])
        plan = audit.build_audit_plan(self.body, [first, second], max_prompt_chars=30000,
                                      render_prompt=canonical_json)
        if plan['requires_reread']:
            checks = audit.validate_reread_response({'recommendation': 'ready', 'issues': [],
                'claim_checks': [{'claim_id': plan['claim_ids'][0], 'status': 'pending',
                    'reason': '事件窗未包含全部结果引用。', 'process_ids': ['p2', 'p3'],
                    'outcome_ids': self.raw['outcome_ids'] if outcome is None else outcome}]}, self.body, plan)
        else:
            checks = None
        return plan, checks

    def final(self, allowed=None, **changes):
        return self.normalized(allowed, **{'recheck': {'reason': '已核对全文中的原承诺、条件、经过和结果。',
            'evidence_ids': ['p0', 'p1', 'p2', 'p3', 'p4', 'p6']}, **changes})

    def test_full_source_explicit_recheck_records_basis_without_changing_any_window_or_raw(self):
        plan, checks = self.plan()
        final = self.final()
        original = copy.deepcopy((plan, checks, final))
        self.assertTrue(final['claim_reading_complete'])
        self.assertEqual(final['claims'][0]['verification_status'], 'pending')
        effective = audit.final_gate(final, plan, checks)
        self.assertEqual(effective['recommendation'], 'ready')
        check = effective['claim_gate']['claim_checks'][0]
        self.assertEqual(check['status'], 'verified')
        self.assertEqual(check['model_status'], 'verified')
        self.assertEqual(check['coverage_basis'], 'full_source_recheck')
        self.assertEqual((check['event_start_id'], check['event_end_id']), ('p2', 'p4'))
        self.assertEqual(check['outcome_ids'], ['p4', 'p6'])
        source = check['coverage_audit']['source']
        self.assertEqual(source, plan['source_proof'])
        self.assertEqual(source['source_hash'], digest(self.body))
        self.assertEqual(source['paragraph_ids'], ['p' + str(i) for i in range(8)])
        self.assertEqual(source['paragraph_bank_hash'], digest([[p['id'], p['text']]
            for p in chief.paragraphs(self.body)]))
        self.assertFalse(check['coverage_audit']['event_anchor_coverage'])
        self.assertEqual(effective['claim_gate']['claim_rechecks'][0]['previous_check']['status'], 'pending')
        self.assertEqual((plan, checks, final), original)
        self.assertNotIn('supplementary_outcome_ids', check)

    def test_partial_source_or_generic_ready_cannot_resolve_window_metadata(self):
        plan, checks = self.plan()
        local = self.final(['p0', 'p1', 'p2', 'p3', 'p4', 'p6'])
        self.assertFalse(local['claim_reading_complete'])
        for final in (local, self.normalized()):
            with self.subTest(full=final['claim_reading_complete'], proof='recheck' in final['claims'][0]):
                effective = audit.final_gate(final, plan, checks)
                self.assertEqual(effective['recommendation'], 'uncertain')
                self.assertEqual(effective['claim_gate']['claim_checks'][0]['status'], 'pending')
                self.assertNotIn('coverage_basis', effective['claim_gate']['claim_checks'][0])

    def test_native_unresolved_or_missing_comparison_cannot_be_approved(self):
        plan, checks = self.plan()
        for changes in ({'status': 'pending'}, {'status': 'conflict'}, {'process_ids': []}, {'outcome_ids': []}):
            with self.subTest(changes=changes):
                effective = audit.final_gate(self.final(**changes), plan, checks)
                self.assertEqual(effective['recommendation'], 'uncertain')
                self.assertEqual(effective['claim_gate']['claim_checks'][0]['status'], 'pending')
        with self.assertRaises(StoryError):
            self.final(assertion_ids=[])

    def test_old_conflict_needs_all_endpoints_and_native_conflict_is_not_washed(self):
        plan, checks = self.plan(status='conflict', outcome=['p4'])
        self.assertIsNone(checks)
        missing = self.final(recheck={'reason': '漏原承诺。', 'evidence_ids': ['p1', 'p2', 'p3', 'p4', 'p6']})
        with self.assertRaisesRegex(StoryError, '先前完整证据端点'):
            audit.final_gate(missing, plan)
        for final in (self.final(status='conflict'), self.normalized()):
            effective = audit.final_gate(final, plan)
            self.assertEqual(effective['recommendation'], 'revise')
            self.assertEqual(effective['claim_gate']['claim_checks'][0]['status'], 'conflict')
        effective = audit.final_gate(self.final(), plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['claim_rechecks'][0]['previous_check']['status'], 'conflict')

    def test_source_endpoint_scope_and_unserved_guards_remain_strict(self):
        plan, checks = self.plan()
        for path in ('claim', 'proof', 'delivered'):
            final = self.final()
            target = (final['claims'][0] if path == 'claim' else final['claims'][0]['recheck']
                      if path == 'proof' else final['claim_reading_source'])
            target['source_hash'] = 'wrong-source'
            with self.subTest(path=path), self.assertRaises(StoryError):
                audit.final_gate(final, plan, checks)
        for field, value in [('paragraph_bank_hash', 'false-bank-hash'), ('paragraph_count', 7),
                             ('paragraph_ids', ['p' + str(i) for i in range(7)])]:
            forged = self.final()
            forged['claim_reading_source'][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(StoryError, '完整来源绑定'):
                audit.final_gate(forged, plan, checks)
        changed_plan = copy.deepcopy(plan)
        changed_plan['source_proof']['paragraph_bank_hash'] = 'false-bank-hash'
        with self.assertRaisesRegex(StoryError, '完整来源绑定'):
            audit.final_gate(self.final(), changed_plan, checks)
        changed_scope = self.final()
        changed_scope['claims'][0]['comparison_scope'] = 'outcome'
        with self.assertRaises(StoryError):
            audit.final_gate(changed_scope, plan, checks)
        unserved = copy.deepcopy(plan)
        unserved['unserved_claim_ids'] = unserved['claim_ids']
        unserved['plan_hash'] = digest({k: v for k, v in unserved.items() if k != 'plan_hash'})
        with self.assertRaisesRegex(StoryError, '未服务'):
            audit.final_gate(self.final(), unserved)

    def test_changed_conditions_do_not_borrow_full_source_approval(self):
        plan, checks = self.plan()
        effective = audit.final_gate(self.final(condition_ids=['p1', 'p6']), plan, checks)
        self.assertEqual(effective['recommendation'], 'uncertain')
        self.assertEqual(effective['claim_gate']['claim_checks'][0]['status'], 'pending')
        self.assertNotIn('coverage_basis', effective['claim_gate']['claim_checks'][0])
        self.assertEqual(effective['claim_gate']['rejected_proposals'][0]['original_condition_ids'], ['p1'])


if __name__ == '__main__':
    unittest.main()
