"""Independent final source read; prior judgments are audit history, not facts."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.contracts import StoryError, canonical_json, digest


class ClaimRecheckTests(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(['他根据旧记录预测她会说同一句话。', '原记录只对应未改的剧本。',
            '她主动换稿，让同伴先读新台词。', '他承认旧记录不能预知新选择，并履行赌约。',
            '第二章', '他们收起剧本。'])
        self.raw = {'kind': 'prediction', 'subject': '排练回应索引',
            'statement': '他预测同一句话，但正文没有兑现，这是矛盾。',
            'comparison_scope': 'process_and_outcome', 'assertion_ids': ['p0'], 'condition_ids': ['p1'],
            'process_ids': ['p2'], 'outcome_ids': ['p3'], 'event_start_id': 'p2', 'event_end_id': 'p3',
            'status': 'conflict', 'reason': '旧判断：必须预测成功。'}
        self.contract = {'author_input': {'premise': '保留角色改选的自由。'}}
        self.cards = {'cards': [{'source_path': 'whole.rule[0]', 'value': '原文明确解释的失败可以成立。'}]}

    def checked(self, raw, allowed=None, body=None):
        return chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [raw], 'observations': []}, self.body if body is None else body,
            allowed_ids=allowed, require_facts=True)

    def plan(self, status='conflict', capacity=30000):
        first = self.checked({**self.raw, 'status': status}, ['p0', 'p1', 'p2', 'p3'])
        first['issues'] = [{'severity': 'blocker', 'scope': 'local', 'evidence_ids': ['p0', 'p3'],
            'reason': '旧判词要求角色必须成功。', 'fix_intent': '旧处方要求公开隐私。'}]
        first['facts'] = [{'statement': '带旧结论的事实摘要。', 'evidence_ids': ['p0', 'p3']}]
        second = chief.validate_response({'recommendation': 'ready', 'issues': [], 'facts': [],
            'claims': [], 'observations': []}, self.body, allowed_ids=['p4', 'p5'], require_facts=True)
        readings = [first, second]
        plan = audit.build_audit_plan(self.body, readings, max_prompt_chars=capacity,
                                      render_prompt=canonical_json)
        return readings, plan

    def prompt(self, readings, plan, **kwargs):
        return chief.synthesis_prompt(self.body, kwargs.pop('contract', self.contract), readings,
            source_cards=kwargs.pop('source_cards', self.cards), claim_audit={'plan': plan, 'checks': None},
            **kwargs)

    def final(self, allowed=None, **changes):
        raw = {**self.raw, 'status': 'verified', 'statement': '角色根据旧记录打赌，换稿后认输，限制和后果一致。',
               'reason': '新原文核验：实际改变及认输均已出现。',
               'recheck': {'reason': '原文说明她主动改稿，他接受失准及赌约后果，不存在必须预言获胜的要求。',
                           'evidence_ids': ['p0', 'p1', 'p2', 'p3']}}
        raw.update(changes)
        return self.checked(raw, allowed)

    def test_final_prompt_contains_only_registry_anchors_and_complete_source(self):
        readings, plan = self.plan()
        original = copy.deepcopy((readings, plan))
        prompt, allowed = self.prompt(readings, plan)
        data = json.loads(prompt.rsplit('\n', 1)[1])
        registry = data['claim_audit']['registry']
        self.assertEqual(len(registry), 1)
        self.assertEqual(set(registry[0]), {'claim_id', 'kind', 'subject', 'assertion_ids', 'condition_ids',
            'comparison_scope', 'required_evidence_ids', 'event_windows', 'source_windows'})
        for text in ('旧判断', '带旧结论', self.raw['statement'], plan['plan_hash']):
            self.assertNotIn(text, prompt)
        # Prior blockers are objections to close, not frozen claim judgments.
        objections = data['editorial_issue_registry']
        self.assertEqual(len(objections), 1)
        self.assertEqual(objections[0]['reason'], '旧判词要求角色必须成功。')
        self.assertIn('不是已证实的事实', prompt)
        self.assertEqual(data['reading_anchors'], [[['p0', 'p3'], ['p0', 'p3']], []])
        self.assertEqual(data['paragraphs'], [[p['id'], p['text']] for p in chief.paragraphs(self.body)])
        self.assertEqual(allowed, [p['id'] for p in chief.paragraphs(self.body)])
        self.assertEqual(registry[0]['required_evidence_ids'], ['p0', 'p1', 'p2', 'p3'])
        self.assertEqual(registry[0]['event_windows'], [{'start_id': 'p2', 'end_id': 'p3'}])
        self.assertEqual((readings, plan), original)

    def test_old_judgment_changes_do_not_change_final_prompt_or_opaque_binding(self):
        readings, plan = self.plan()
        expected = self.prompt(readings, plan)
        changed_readings, changed_plan = copy.deepcopy((readings, plan))
        for result in changed_readings:
            result['recommendation'] = 'uncertain'
            for item in result['claims'] + result['facts']:
                item['statement'] = '完全不同的旧解释。'
            for issue in result['issues']:
                # A changed semantic blocker should change closure obligations;
                # this control isolates old factual interpretations only.
                issue.update(reason=issue['reason'], fix_intent=issue['fix_intent'])
        for claim in changed_plan['claims']:
            claim.update(statement='别的旧解释', status='pending', reason='别的旧理由')
        for check in changed_plan['initial_checks']:
            check.update(status='pending', model_status='pending', reason='另一个旧理由')
        changed_plan['plan_hash'] = digest({k: v for k, v in changed_plan.items() if k != 'plan_hash'})
        self.assertNotEqual(changed_plan['plan_hash'], plan['plan_hash'])
        self.assertEqual(self.prompt(changed_readings, changed_plan), expected)

    def test_scope_contract_research_and_author_feedback_remain_bound(self):
        readings, plan = self.plan()
        expected = self.prompt(readings, plan)[0]
        for kwargs in ({'contract': {'author_input': {'premise': '另一项作者要求。'}}},
                       {'source_cards': {'cards': [{'source_path': 'whole.rule[0]', 'value': '另一条原研究规则。'}]}},
                       {'editorial_feedback': {'source': 'author', 'reason': '保留结尾的主动行动。'}}):
            self.assertNotEqual(self.prompt(readings, plan, **kwargs)[0], expected)
        changed = copy.deepcopy(plan)
        changed['claims'][0]['comparison_scope'] = 'outcome'
        changed['plan_hash'] = digest({k: v for k, v in changed.items() if k != 'plan_hash'})
        self.assertNotEqual(self.prompt(readings, changed)[0], expected)
        with self.assertRaisesRegex(StoryError, '正文绑定'):
            audit.source_registry(self.body + '\n新原文。', plan)

    def test_recheck_rewrites_old_interpretation_with_full_source_and_keeps_history(self):
        _, plan = self.plan()
        final = self.final()
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['conflict_ids'], [])
        audit_record = effective['claim_gate']['claim_rechecks'][0]
        self.assertEqual(audit_record['previous_check']['status'], 'conflict')
        self.assertEqual(audit_record['old_statement'], self.raw['statement'])
        self.assertEqual(audit_record['new_statement'], final['claims'][0]['statement'])
        self.assertEqual(audit_record['recheck']['evidence'], chief.paragraphs(self.body)[:4])

    def test_plain_verified_still_cannot_erase_old_conflict(self):
        _, plan = self.plan()
        raw = self.final()['claims'][0]
        del raw['recheck']
        self.assertEqual(audit.final_gate({'recommendation': 'ready', 'issues': [], 'claims': [raw]}, plan)
                         ['recommendation'], 'revise')

    def test_missing_old_endpoint_wrong_scope_conditions_and_source_cannot_revoke(self):
        _, plan = self.plan()
        for mutation in ({'comparison_scope': 'outcome'},
                         {'recheck': {'reason': '没有重引原承诺。', 'evidence_ids': ['p1', 'p2', 'p3']}}):
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                audit.final_gate(self.final(**mutation), plan)
        final = self.final()
        final['claims'][0]['recheck']['source_hash'] = 'other-source'
        with self.assertRaises(StoryError):
            audit.final_gate(final, plan)

    def test_full_process_recheck_without_start_to_finish_stays_unresolved(self):
        _, plan = self.plan()
        final = self.final(allowed=['p0', 'p1', 'p2', 'p3'], event_start_id='p3', event_end_id='p3')
        self.assertEqual(final['claims'][0]['verification_status'], 'pending')
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'revise')
        self.assertEqual(effective['claim_gate']['conflict_ids'], plan['claim_ids'])
        self.assertEqual(effective['claim_gate']['claim_rechecks'][0]['effect'], 'retained_unresolved')

    def test_recheck_of_verified_is_no_op_and_cannot_wash_new_conflict(self):
        _, plan = self.plan(status='verified')
        final = self.final(event_start_id='p3', event_end_id='p3')
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'ready')
        self.assertEqual(effective['claim_gate']['claim_checks'], plan['initial_checks'])
        self.assertEqual(effective['claim_gate']['claim_rechecks'][0]['effect'], 'no_op_already_verified')
        final = self.final(status='conflict', reason='本次源读发现另一项实质矛盾。')
        effective = audit.final_gate(final, plan)
        self.assertEqual(effective['recommendation'], 'revise')
        self.assertEqual(effective['claim_gate']['claim_checks'][0]['reason'], final['claims'][0]['reason'])

    def test_unserved_claim_cannot_be_approved_by_recheck(self):
        _, plan = self.plan(status='pending', capacity=10)
        self.assertEqual(plan['unserved_claim_ids'], plan['claim_ids'])
        with self.assertRaisesRegex(StoryError, '未服务'):
            audit.final_gate(self.final(), plan)


if __name__ == '__main__':
    unittest.main()
