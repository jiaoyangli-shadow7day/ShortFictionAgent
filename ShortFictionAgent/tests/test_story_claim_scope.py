"""Claim-scope transport controls; model semantic extraction needs live review."""
import copy
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit as audit
from short_fiction_agent.story_studio.chief_review import finish_review
from short_fiction_agent.story_studio.contracts import StoryError
from tests.narrative_fixtures import narrative_response


class ClaimScopeTests(unittest.TestCase):
    def setUp(self):
        self.body = ('她说：“下周我定地点，以后我们轮流安排。”\n'
                     '他收好便签，答应负责下次晚饭。\n'
                     '他问她愿不愿意恋爱，她答应，两人牵手离开。')
        self.prompt = chief.prompt(self.body, {})

    def reading(self, claims=None, observations=None, issues=None):
        raw = narrative_response(self.prompt, {'recommendation': 'ready', 'issues': issues or [],
            'claims': claims or [], 'observations': observations or [], 'facts': []})
        return raw, chief.validate_response(raw, self.body, require_facts=True, narrative_scope='full')

    def future_claim(self):
        return {'kind': 'prediction', 'subject': '恋爱后轮流安排',
            'statement': '以后两人轮流安排约会。', 'comparison_scope': 'process_and_outcome',
            'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': ['p1'],
            'outcome_ids': [], 'status': 'pending',
            'reason': '长期执行在本篇结束之后，不妨碍当前关系结局闭合。'}

    def test_actual_reading_synthesis_and_reread_prompts_define_scope(self):
        _, reading = self.reading([self.future_claim()])
        synthesis, _ = chief.synthesis_prompt(self.body, {}, [reading])
        plan = chief.claim_reread_plan(self.body, {}, [reading])
        for prompt in (self.prompt, synthesis):
            self.assertIn('非当前结局成立前提', prompt)
            self.assertIn('不列为须在本篇兑现的关键claims', prompt)
            self.assertIn('叙事审查仍核对当前共同决定与实际回应', prompt)
            self.assertIn('作者要求在篇内兑现', prompt)
            self.assertIn('正文已作为完成事实使用的承诺，仍须核验', prompt)
            self.assertIn('被人物选择规避、正文未声称发生且作者不要求发生的条件后果', prompt)
            self.assertIn('不要求先触发坏结果才能审稿完成', prompt)
            self.assertIn('来源与权限、处理意图及决策依据', prompt)
            self.assertIn('作者要求实际发生、正文声称已发生或结局依赖实际执行', prompt)
            self.assertIn('不能按若、如果或if字样自动豁免', prompt)
        self.assertIn('不能改成仅已达成约定后verified', plan['prompt'])
        self.assertIn('不能凭reason或ready豁免待核项', plan['prompt'])
        self.assertIn('被选择规避且未声称发生、非作者要求的条件后果', plan['prompt'])
        self.assertIn('保留原断言、条件与comparison_scope', plan['prompt'])
        self.assertIn('不能改弱为仅政策已陈述后verified', plan['prompt'])

    def test_actual_current_agreement_can_be_observation_without_future_execution_claim(self):
        raw, reading = self.reading(observations=[{'subject': '共同决定下次安排', 'role': 'process',
            'statement': '她确定地点，他接住邀请并负责晚饭。', 'evidence_ids': ['p0', 'p1']}])
        original = copy.deepcopy(raw)

        def no_extra_call(*args, **kwargs):
            self.fail('A scripted complete agreement should not invent a future audit call')

        final, receipts = finish_review(self.body, {}, [reading], stage=no_extra_call)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertEqual(final['observations'][0]['evidence'][1]['text'], self.body.splitlines()[1])
        self.assertTrue(final['narrative_gate']['coverage_complete'])
        self.assertIsNone(receipts['claim_reread'])
        self.assertEqual(raw, original)

    def test_old_future_pending_is_not_waived_by_reason_or_native_ready(self):
        raw, reading = self.reading([self.future_claim()])
        original = copy.deepcopy(raw)
        calls = []

        def stage(task, prompt, allowed, *, facts=False, audit_plan=None):
            calls.append(task)
            response = {'recommendation': 'ready', 'issues': [], 'claim_checks': [{
                'claim_id': audit_plan['claim_ids'][0], 'status': 'pending',
                'reason': '未来安排不妨碍当前闭合，但长期执行未发生。',
                'process_ids': ['p1'], 'outcome_ids': []}]}
            return audit.validate_reread_response(response, self.body, audit_plan), {'response': response}

        final, _ = finish_review(self.body, {}, [reading], stage=stage)
        self.assertEqual(calls, ['chief_editor_claim_reread'])
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertEqual(len(final['claim_gate']['pending_ids']), 1)
        self.assertEqual(raw, original)

    def test_claimed_completed_process_conflict_still_blocks(self):
        self.body = ('旁白称两人已轮流安排过多次约会。\n'
                     '实际从第一次到最后一次都由她安排，他从未提出计划。\n'
                     '两人结束最后一次约会。')
        self.prompt = chief.prompt(self.body, {})
        claim = {**self.future_claim(), 'statement': '两人已轮流安排过多次约会。',
            'outcome_ids': ['p2'], 'status': 'conflict',
            'reason': '已兑现的轮流安排与实际始终单方安排冲突。'}
        _, reading = self.reading([claim])
        final, _ = finish_review(self.body, {}, [reading], stage=lambda *a, **k: self.fail('Already complete'))
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(len(final['claim_gate']['conflict_ids']), 1)
        self.assertEqual(final['issues'][-1]['severity'], 'blocker')

    def test_explicit_author_payoff_requirement_is_not_waived_by_future_plan(self):
        raw, _ = self.reading()
        raw['recommendation'] = 'revise'
        raw['issues'] = [{'severity': 'blocker', 'scope': 'global', 'evidence_ids': ['p0', 'p2'],
            'reason': '作者要求篇内展开双方各自安排一次约会，正文却只约定未来执行后结束。',
            'fix_intent': '在篇内展开双方主动安排与实际回应。'}]
        reading = chief.validate_response(raw, self.body, require_facts=True, narrative_scope='full')
        contract = {'constraints': {'must_keep': ['篇内展开双方各自安排一次约会。']}}
        final, _ = finish_review(self.body, contract, [reading], stage=lambda *a, **k: self.fail('No claims'))
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(chief.legacy_issues(final, self.body)[0]['repair_scope'], 'replan')

    def avoided_branch(self):
        self.body = ('对方说：“如果收购完成，我们就停止低价服务。”\n'
                     '他出示决策授权，确认这确是公司的现行收购政策。\n'
                     '她确认出售和保留原店的成本。\n'
                     '她拒绝收购，没有交付门店。\n'
                     '旧店继续按原价格服务。')
        self.prompt = chief.prompt(self.body, {})
        return {'kind': 'prediction', 'subject': '收购后的服务政策',
            'statement': '如果收购完成，公司就停止低价服务。',
            'comparison_scope': 'process_and_outcome', 'assertion_ids': ['p0'],
            'condition_ids': ['p1'], 'process_ids': ['p3'], 'outcome_ids': [],
            'status': 'pending', 'reason': '收购被拒，前置条件未发生，未展示收购后的执行结果。'}

    def test_avoided_branch_checks_actual_policy_and_choice_without_inventing_execution(self):
        self.avoided_branch()
        claim = {'kind': 'constraint', 'subject': '实际收购政策与决策权限',
            'statement': '获授权的决策者确认收购后停止低价服务的现行政策。',
            'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': ['p1'],
            'outcome_ids': ['p1'], 'status': 'verified',
            'reason': '实际陈述与授权已对照；没有把未发生的收购后执行当完成事实。'}
        raw, reading = self.reading([claim], observations=[{'subject': '拒售决定',
            'role': 'outcome', 'statement': '她拒绝出售，原店继续服务。',
            'evidence_ids': ['p3', 'p4']}])
        original = copy.deepcopy(raw)
        final, receipts = finish_review(self.body, {}, [reading],
            stage=lambda *a, **k: self.fail('Complete current policy needs no invented execution call'))
        self.assertEqual(final['recommendation'], 'ready')
        self.assertFalse(final['claim_gate']['pending_ids'])
        self.assertIsNone(receipts['claim_reread'])
        self.assertEqual(final['claims'][0]['outcome_ids'], ['p1'])
        self.assertEqual(final['observations'][0]['evidence_ids'], ['p3', 'p4'])
        self.assertEqual(raw, original)

    def test_registered_avoided_branch_pending_is_not_waived_or_downgraded(self):
        claim = self.avoided_branch()
        raw, reading = self.reading([claim])
        original = copy.deepcopy(raw)
        calls = []

        def stage(task, prompt, allowed, *, facts=False, audit_plan=None):
            calls.append(task)
            check = {'claim_id': audit_plan['claim_ids'][0], 'status': 'pending',
                'reason': '前置条件被选择规避，并不把缺失的执行证据改成verified。',
                'process_ids': ['p3'], 'outcome_ids': []}
            with self.assertRaises(StoryError):
                audit.validate_reread_response({'recommendation': 'ready', 'issues': [],
                    'claim_checks': [{**check, 'comparison_scope': 'outcome'}]}, self.body, audit_plan)
            response = {'recommendation': 'ready', 'issues': [], 'claim_checks': [check]}
            return audit.validate_reread_response(response, self.body, audit_plan), {'response': response}

        final, _ = finish_review(self.body, {}, [reading], stage=stage)
        self.assertEqual(calls, ['chief_editor_claim_reread'])
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertEqual(len(final['claim_gate']['pending_ids']), 1)
        for field in ('statement', 'assertion_ids', 'condition_ids', 'comparison_scope'):
            self.assertEqual(final['claims'][0][field], claim[field])
        self.assertEqual(raw, original)

    def test_triggered_conditional_consequence_conflict_still_blocks(self):
        claim = self.avoided_branch()
        self.body = ('对方承诺：“如果收购完成，我们就保留低价服务。”\n'
                     '双方签字确认全部交付。\n'
                     '收购方正式接手，随即停止低价服务。\n'
                     '原店顾客被拒绝，收购方确认已停止该项目。\n'
                     '交接完成。')
        self.prompt = chief.prompt(self.body, {})
        claim.update(statement='如果收购完成，就保留低价服务。', condition_ids=['p1'],
            process_ids=['p2'], outcome_ids=['p3'], status='conflict',
            reason='条件已触发，实际停止服务与明确承诺相反。')
        _, reading = self.reading([claim])
        final, _ = finish_review(self.body, {}, [reading],
            stage=lambda *a, **k: self.fail('Source-complete conflict must remain blocking'))
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(len(final['claim_gate']['conflict_ids']), 1)

    def test_avoided_branch_does_not_waive_current_policy_conflict(self):
        self.avoided_branch()
        self.body = self.body.replace('他出示决策授权，确认这确是公司的现行收购政策。',
            '他出示的有效授权明确禁止停止低价服务；旁白却说他已获停止服务的权限。')
        self.prompt = chief.prompt(self.body, {})
        claim = {'kind': 'constraint', 'subject': '现实收购授权',
            'statement': '他已获停止低价服务的权限。', 'assertion_ids': ['p1'],
            'condition_ids': [], 'process_ids': [], 'outcome_ids': ['p1'],
            'status': 'conflict', 'reason': '授权明确禁止该行为，旁白却断言已经授权。'}
        _, reading = self.reading([claim])
        final, _ = finish_review(self.body, {}, [reading],
            stage=lambda *a, **k: self.fail('Complete actual policy conflict remains blocking'))
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(len(final['claim_gate']['conflict_ids']), 1)

    def test_explicit_author_branch_execution_requirement_remains_blocking(self):
        self.avoided_branch()
        contract = {'constraints': {'must_keep': ['篇内展开收购后的政策执行及代价。']}}
        raw, _ = self.reading()
        raw.update(recommendation='revise', issues=[{'severity': 'blocker', 'scope': 'global',
            'evidence_ids': ['p0', 'p3', 'p4'],
            'reason': '作者要求篇内展开收购后的政策执行，正文却以拒售跳过这一必写事件。',
            'fix_intent': '按作者要求重做关键事件及代价。'}])
        reading = chief.validate_response(raw, self.body, require_facts=True, narrative_scope='full')
        final, _ = finish_review(self.body, contract, [reading],
            stage=lambda *a, **k: self.fail('Explicit author requirement is not waived'))
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(chief.legacy_issues(final, self.body)[0]['repair_scope'], 'replan')

    def test_scope_protocol_and_prompts_invalidate_prior_criteria(self):
        current = chief.criteria_hash()
        with patch.object(audit, 'PROTOCOL', 'chief-claims-v3'):
            self.assertNotEqual(chief.criteria_hash(), current)
        with patch.object(audit, 'PROTOCOL', 'chief-claims-v4-body-scope'):
            self.assertNotEqual(chief.criteria_hash(), current)
        with patch.object(audit, 'REREAD_INSTRUCTION', audit.REREAD_INSTRUCTION + '不同范围规则'):
            self.assertNotEqual(chief.criteria_hash(), current)


if __name__ == '__main__':
    unittest.main()
