"""Invalid condition proposals cannot replace source-bound claim checks."""
import copy
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json, digest


class ClaimProposalTests(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(['双方约定向制作账户支付两万元。', '双方验款后银行才能划款。',
            '买家修改交付日期，双方签字。', '银行确认两万元已经到账。', '两万元划入制作账户，授权书交付。',
            '第二章'])
        self.raw = {'kind': 'cashflow', 'subject': '制作账户首期款', 'statement': '首期制作款按约完成交割。',
            'assertion_ids': ['p0'], 'condition_ids': ['p1'], 'process_ids': ['p2', 'p3'],
            'outcome_ids': ['p4'], 'event_start_id': 'p3', 'event_end_id': 'p4',
            'status': 'verified', 'reason': '验款和制作账户到账均已出现。'}

    def normalized(self, **changes):
        return chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [{**self.raw, **changes}], 'observations': []}, self.body)

    def plan(self, status='verified'):
        first = chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [{**self.raw, 'status': status}], 'observations': []}, self.body,
            allowed_ids=['p0', 'p1', 'p2', 'p3', 'p4'], require_facts=True)
        second = chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [], 'observations': []}, self.body, allowed_ids=['p5'], require_facts=True)
        plan = audit.build_audit_plan(self.body, [first, second], max_prompt_chars=30000,
                                      render_prompt=canonical_json)
        return plan, audit._check_from_claim(first['claims'][0])

    def proposal(self, **changes):
        fields = {'condition_ids': ['p1', 'p2'], 'statement': '汇总提出的新条件解释不应替换原断言。',
            'recheck': {'reason': '重新核对原文各端点。', 'evidence_ids': ['p0', 'p1', 'p2', 'p3', 'p4']}}
        fields.update(changes)
        return self.normalized(**fields)

    def test_verified_condition_proposal_is_rejected_and_original_check_is_unchanged(self):
        plan, previous = self.plan()
        final = self.proposal()
        unchanged = copy.deepcopy((plan, final))
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['claim_checks'], [previous])
        self.assertEqual(effective['issues'], [])
        rejected = effective['claim_gate']['rejected_proposals'][0]
        self.assertEqual(rejected['effect'], 'rejected_condition_binding_retained_verified')
        self.assertEqual(rejected['original_condition_ids'], ['p1'])
        self.assertEqual(rejected['proposed_condition_ids'], ['p1', 'p2'])
        self.assertEqual(rejected['diffs'], [{'field': 'condition_ids', 'expected': ['p1'],
                                              'received': ['p1', 'p2']}])
        self.assertEqual(rejected['retained_claim'], plan['claims'][0])
        self.assertEqual(rejected['retained_claim']['statement'], self.raw['statement'])
        self.assertEqual(rejected['resolved_check'], previous)
        self.assertEqual((plan, final), unchanged)

    def test_pending_and_conflict_cannot_be_cleared_by_changed_conditions(self):
        for status, expected in [('pending', 'uncertain'), ('conflict', 'revise')]:
            with self.subTest(status=status):
                plan, previous = self.plan(status)
                checks = {'source_hash': plan['source_hash'], 'plan_hash': plan['plan_hash'],
                          'claim_checks': [previous]}
                effective = audit.final_gate(self.proposal(), plan, checks)
                self.assertEqual(effective['recommendation'], expected)
                self.assertEqual(effective['claim_gate']['claim_checks'], [previous])
                self.assertEqual(effective['claim_gate']['rejected_proposals'][0]['effect'],
                                 'rejected_condition_binding_retained_unresolved')

    def test_new_conflict_or_pending_is_not_swallowed_by_old_verified_check(self):
        plan, previous = self.plan()
        for status in ('conflict', 'pending'):
            with self.subTest(status=status):
                final = self.proposal(status=status)
                effective = audit.final_gate(final, plan)
                self.assertEqual(effective['recommendation'], 'uncertain')
                self.assertEqual(effective['issues'], [])
                check = effective['claim_gate']['claim_checks'][0]
                self.assertEqual(check['status'], 'pending')
                self.assertTrue(check['metadata_pending'])
                self.assertEqual(check['evidence'], previous['evidence'])
                self.assertEqual(final['claims'][0]['status'], status)

    def test_condition_isolation_does_not_relax_identity_scope_or_source(self):
        plan, _ = self.plan()
        for field, changed in [('kind', 'constraint'), ('subject', '另一个账户'),
                               ('assertion_ids', ['p1']), ('comparison_scope', 'process_and_outcome'),
                               ('source_hash', 'wrong-source')]:
            final = self.proposal()
            final['claims'][0][field] = changed
            with self.subTest(field=field), self.assertRaises(StoryError):
                audit.final_gate(final, plan)
        for field in ('recheck', 'resolution'):
            final = self.proposal()
            if field == 'resolution':
                final = self.normalized(condition_ids=['p1', 'p2'], resolution={
                    'supersedes_status': 'pending', 'reason': '复核',
                    'evidence_ids': ['p0', 'p1', 'p2', 'p3', 'p4']})
            final['claims'][0][field]['source_hash'] = 'wrong-source'
            with self.subTest(field=field), self.assertRaises(StoryError):
                audit.final_gate(final, plan)
        for bad_conditions in (['p1', 'p1'], ['p1', 'p999'], 'p1'):
            with self.subTest(conditions=bad_conditions), self.assertRaises(StoryError):
                self.normalized(condition_ids=bad_conditions)

    def test_condition_isolation_still_requires_old_evidence_served_source_and_plan(self):
        plan, previous = self.plan()
        final = self.proposal(recheck={'reason': '漏掉原承诺。', 'evidence_ids': ['p1', 'p2', 'p3', 'p4']})
        with self.assertRaisesRegex(StoryError, '先前完整证据端点'):
            audit.final_gate(final, plan)
        unserved = copy.deepcopy(plan)
        unserved['unserved_claim_ids'] = unserved['claim_ids']
        unserved['plan_hash'] = digest({k: v for k, v in unserved.items() if k != 'plan_hash'})
        with self.assertRaisesRegex(StoryError, '未服务'):
            audit.final_gate(self.proposal(), unserved)
        without_proof = self.normalized(condition_ids=['p1', 'p2'])
        with self.assertRaisesRegex(StoryError, '未服务'):
            audit.final_gate(without_proof, unserved)
        wrong_plan = copy.deepcopy(plan)
        wrong_plan['claims'][0]['reason'] = '改过的计划但没有重绑定。'
        with self.assertRaisesRegex(StoryError, '计划或正文绑定'):
            audit.final_gate(self.proposal(), wrong_plan)
        for field in ('source_hash', 'plan_hash'):
            checks = {'source_hash': plan['source_hash'], 'plan_hash': plan['plan_hash'],
                      'claim_checks': [previous]}
            checks[field] = 'wrong-binding'
            with self.subTest(field=field), self.assertRaises(StoryError):
                audit.final_gate(self.proposal(), plan, checks)
        changed_source = self.proposal()
        changed_source['claims'][0]['recheck']['evidence'][0]['text'] = '不同的原文。'
        with self.assertRaisesRegex(StoryError, '实际原文不一致'):
            audit.final_gate(changed_source, plan)


if __name__ == '__main__':
    unittest.main()
