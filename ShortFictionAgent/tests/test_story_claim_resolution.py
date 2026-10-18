"""Same-editor source-bound correction and optional retrieval metadata."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json


def raw_claim(**changes):
    return {**{'kind': 'prediction', 'subject': '排练中的回应',
        'statement': '她会按旧剧本回答。', 'comparison_scope': 'outcome',
        'assertion_ids': ['p0'], 'condition_ids': ['p1'], 'process_ids': ['p2'],
        'outcome_ids': ['p3'], 'event_start_id': 'p2', 'event_end_id': 'p3',
        'status': 'conflict', 'reason': '实际回答不同于旧记录。'}, **changes}


def response(claim):
    return {'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [claim], 'observations': []}


class ClaimResolutionTests(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(['他根据旧剧本预测她会照原句回答。', '旧记录只适用于无人改稿。',
            '她主动换了剧本，问他要不要试一句新台词。', '她说出新台词，他承认旧记录不能预测新选择。',
            '第二章', '他们离开排练室。'])

    def normalized(self, claim, allowed=None):
        return chief.validate_response(response(claim), self.body, allowed_ids=allowed, require_facts=True)

    def plan(self, status='conflict', capacity=20000, **changes):
        first = self.normalized(raw_claim(status=status, **changes), ['p0', 'p1', 'p2', 'p3'])
        second = chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [], 'observations': []}, self.body, allowed_ids=['p4', 'p5'], require_facts=True)
        return [first, second], audit.build_audit_plan(self.body, [first, second],
            max_prompt_chars=capacity, render_prompt=canonical_json)

    def resolved(self, status='conflict', **changes):
        fields = {'status': 'verified', 'reason': '新选择与能力限制一致。',
            'resolution': {'supersedes_status': status,
                'reason': '先前把预测失败当作矛盾；完整场景明确女方换稿，旧记录不再适用，男方也承认预测失效。',
                'evidence_ids': ['p0', 'p1', 'p2', 'p3']}}
        fields.update(changes)
        return self.normalized(raw_claim(**fields))

    def test_optional_search_terms_discard_invalid_without_changing_claim_or_input(self):
        raw = response(raw_claim(search_terms=['旧剧本', '零碎记录', '旧剧本', '他', '换了剧本']))
        original = copy.deepcopy(raw)
        checked = chief.validate_response(raw, self.body)['claims'][0]
        self.assertEqual(checked['search_terms'], ['旧剧本', '换了剧本'])
        self.assertEqual(checked['discarded_search_terms'], [
            {'term': '零碎记录', 'reason': 'not_in_claim_evidence'},
            {'term': '旧剧本', 'reason': 'duplicate'}, {'term': '他', 'reason': 'too_short'}])
        self.assertEqual(checked['status'], 'conflict')
        self.assertEqual(checked['verification_status'], 'conflict')
        self.assertEqual(checked['statement'], raw['claims'][0]['statement'])
        self.assertEqual(chief.validate_response(raw, self.body)['claim_validation_version'],
                         audit.CLAIM_VALIDATION_VERSION)
        self.assertEqual(raw, original)
        for malformed in ('旧剧本', [None], {'term': '旧剧本'}):
            with self.subTest(malformed=malformed), self.assertRaises(StoryError):
                self.normalized(raw_claim(search_terms=malformed))

    def test_discarded_search_terms_do_not_search_unrelated_source_or_force_pending(self):
        checked = self.normalized(raw_claim(status='verified', search_terms=['离开排练室']),
                                  ['p0', 'p1', 'p2', 'p3'])
        item = checked['claims'][0]
        self.assertEqual(item['search_terms'], [])
        self.assertEqual(item['verification_status'], 'verified')
        self.assertNotIn('p5', item['context_ids'])

    def test_explicit_source_bound_resolution_corrects_false_conflict_and_keeps_history(self):
        _, plan = self.plan()
        final = self.resolved()
        original = copy.deepcopy(final)
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['issues'], [])
        self.assertEqual(effective['claim_gate']['conflict_ids'], [])
        self.assertEqual(effective['claim_gate']['claim_validation_version'], audit.CLAIM_VALIDATION_VERSION)
        record = effective['claim_gate']['claim_resolutions'][0]
        self.assertEqual(record['previous_check']['status'], 'conflict')
        self.assertEqual(record['previous_check']['model_status'], 'conflict')
        self.assertEqual(record['resolved_check']['model_status'], 'verified')
        self.assertEqual(record['resolution']['evidence'], chief.paragraphs(self.body)[:4])
        self.assertEqual(final, original)

    def test_generic_verified_cannot_erase_race_conflict(self):
        _, plan = self.plan()
        final = self.normalized(raw_claim(status='verified', reason='最后成功。'))
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'revise')
        self.assertEqual(effective['claim_gate']['claim_resolutions'], [])
        self.assertEqual(len(effective['issues']), 1)

    def test_repeated_resolution_of_latest_verified_claim_is_source_bound_no_op(self):
        _, plan = self.plan(status='verified', comparison_scope='process_and_outcome')
        previous = copy.deepcopy(plan['initial_checks'][0])
        final = self.resolved(status='pending', comparison_scope='process_and_outcome',
                              event_start_id='p3', event_end_id='p3')
        self.assertEqual(final['claims'][0]['status'], 'verified')
        self.assertEqual(final['claims'][0]['verification_status'], 'pending')
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['claim_checks'], [previous])
        record = effective['claim_gate']['claim_resolutions'][0]
        self.assertEqual(record['effect'], 'no_op_already_verified')
        self.assertEqual(record['previous_check'], previous)
        self.assertEqual(record['resolved_check'], previous)
        self.assertEqual(record['proposed_check']['status'], 'pending')
        self.assertEqual(record['proposed_check']['event_start_id'], 'p3')
        self.assertEqual(record['resolved_check']['event_start_id'], 'p2')
        self.assertEqual(plan['initial_checks'][0], previous)

    def test_no_op_rejects_wrong_binding_and_missing_previous_evidence(self):
        _, plan = self.plan(status='verified')
        for mutation in ({'statement': '她可能按旧剧本回答。'},
                         {'comparison_scope': 'process_and_outcome'},
                         {'resolution': {'supersedes_status': 'pending', 'reason': '重复初读解释。',
                                         'evidence_ids': ['p0', 'p2', 'p3']}}):
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                audit.final_gate(self.resolved(status='pending', **mutation), plan)
        wrong_source = self.resolved(status='pending')
        wrong_source['claims'][0]['resolution']['source_hash'] = 'another-source-version'
        with self.assertRaisesRegex(StoryError, '原绑定'):
            audit.final_gate(wrong_source, plan)

    def test_no_op_never_approves_previous_pending_conflict_or_washes_new_conflict(self):
        for status in ('pending', 'conflict'):
            _, plan = self.plan(status=status, comparison_scope='process_and_outcome')
            final = self.resolved(status=status, comparison_scope='process_and_outcome',
                                  event_start_id='p3', event_end_id='p3')
            with self.subTest(previous=status), self.assertRaisesRegex(StoryError, '完整核验'):
                audit.final_gate(final, plan)
        _, plan = self.plan(status='verified')
        changed = self.normalized(raw_claim(status='conflict', reason='本次发现新的实际矛盾。'))
        effective = audit.final_gate(changed, plan)
        self.assertEqual(effective['recommendation'], 'revise')
        self.assertEqual(effective['claim_gate']['conflict_ids'], plan['claim_ids'])
        invalid = raw_claim(status='conflict', resolution={'supersedes_status': 'pending',
            'reason': '不能用旧pending解释洗掉新冲突。', 'evidence_ids': ['p0', 'p1', 'p2', 'p3']})
        with self.assertRaises(StoryError):
            audit.final_gate(self.normalized(invalid), plan)

    def test_resolution_requires_original_assertion_conditions_scope_and_all_old_evidence(self):
        _, plan = self.plan()
        mutations = [
            {'statement': '她可能会按旧剧本回答。'},
            {'comparison_scope': 'process_and_outcome'},
            {'resolution': {'supersedes_status': 'conflict', 'reason': '完整原文解释了失败。',
                            'evidence_ids': ['p0', 'p2', 'p3']}},
            {'resolution': {'supersedes_status': 'pending', 'reason': '完整原文解释了失败。',
                            'evidence_ids': ['p0', 'p1', 'p2', 'p3']}}]
        for mutation in mutations:
            fields = {'status': 'verified', 'resolution': {'supersedes_status': 'conflict',
                'reason': '完整原文说明女方改稿后预测失效。', 'evidence_ids': ['p0', 'p1', 'p2', 'p3']}}
            fields.update(mutation)
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                audit.final_gate(self.normalized(raw_claim(**fields)), plan)
        with self.assertRaises(StoryError):
            self.resolved(resolution={'supersedes_status': 'conflict', 'reason': '未知证据。',
                                     'evidence_ids': ['p999']})

    def test_pending_can_be_completed_only_after_served_source_bound_verification(self):
        _, plan = self.plan(status='pending')
        supplied = {'recommendation': 'ready', 'issues': [], 'claim_checks': [{
            'claim_id': plan['claim_ids'][0], 'status': 'pending', 'reason': '先前还未完整核验。',
            'process_ids': ['p2'], 'outcome_ids': ['p3'], 'event_start_id': 'p2', 'event_end_id': 'p3'}]}
        checks = audit.validate_reread_response(supplied, self.body, plan)
        effective = audit.final_gate(self.resolved(status='pending'), plan, checks)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['pending_ids'], [])
        _, unserved = self.plan(status='pending', capacity=50)
        self.assertEqual(unserved['unserved_claim_ids'], unserved['claim_ids'])
        with self.assertRaisesRegex(StoryError, '未服务'):
            audit.final_gate(self.resolved(status='pending'), unserved)

    def test_partial_event_cannot_be_resolved_by_endpoints_only(self):
        _, plan = self.plan(comparison_scope='process_and_outcome')
        final = self.normalized(raw_claim(comparison_scope='process_and_outcome', status='verified',
            event_start_id='p3', event_end_id='p3',
            resolution={'supersedes_status': 'conflict', 'reason': '只看到最后回答。',
                        'evidence_ids': ['p0', 'p1', 'p2', 'p3']}))
        with self.assertRaisesRegex(StoryError, '完整核验'):
            audit.final_gate(final, plan)

    def test_synthesis_explains_source_recheck_without_changing_reading_instruction(self):
        readings, plan = self.plan()
        prompt, allowed = chief.synthesis_prompt(self.body, {}, readings,
            claim_audit={'plan': plan, 'checks': None})
        self.assertIn('recheck', prompt)
        self.assertIn('每条已有claim', prompt)
        self.assertIn('未服务断言不能批准', prompt)
        self.assertIn('不要求所有提议到账', prompt)
        self.assertNotIn('recheck', audit.INSTRUCTION)
        self.assertNotIn('recheck', audit.REREAD_INSTRUCTION)
        payload = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertEqual(payload['claim_audit']['registry'][0]['required_evidence_ids'],
                         plan['initial_checks'][0]['evidence_ids'])
        self.assertEqual(set(allowed), {key for key, _ in payload['paragraphs']})
        self.assertTrue({'p0', 'p1', 'p2', 'p3'} <= set(allowed))

    def test_synthesis_binds_existing_claim_ids_and_keeps_contract_length_out_of_claims(self):
        readings, plan = self.plan()
        prompt, _ = chief.synthesis_prompt(self.body, {}, readings,
            claim_audit={'plan': plan, 'checks': None})
        for rule in ('已有claim_id原样保留', '新增claim省略claim_id', '不能自拟ID',
                     'assertion_ids须非空并绑定原文', '作者合同完成度放issues',
                     '字数由程序实算', '不输出字数超限claims或issues',
                     '角色对外话术与实际不同不自动是叙事矛盾', '完整场景目的、私下事实和对方回应',
                     '不强迫旁白声明', '不因偏好道德坦诚要求公开他人隐私'):
            self.assertIn(rule, prompt)
            self.assertNotIn(rule, audit.INSTRUCTION)
            self.assertNotIn(rule, audit.REREAD_INSTRUCTION)
        allocated = self.normalized(raw_claim(status='verified'))['claims'][0]['claim_id']
        self.assertEqual(allocated, plan['claim_ids'][0])
        for mutation in ({'claim_id': 'claim-final-invented'}, {'assertion_ids': []}):
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                self.normalized(raw_claim(**mutation))


if __name__ == '__main__':
    unittest.main()
