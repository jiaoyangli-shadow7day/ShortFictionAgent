import unittest

from short_fiction_agent.story_studio import plan_outcome_challenge as challenge
from short_fiction_agent.story_studio.contracts import StoryError


class PlanOutcomeChallengeTests(unittest.TestCase):
    def contract(self):
        return {'format': 'short_story', 'author_input': {'premise': '商业反击须有依据'},
                'reader_rewards': [{'id': 'reversal', 'desire': '打脸',
                                    'payoff': '对手实际失去合同', 'required': True}]}

    def plan(self):
        return {'scenes': [{'scene_id': 's1', 'choice': '我抓住他的助理碰电闸。',
                            'consequence': '主办方当场没收他的保证金并解除冠名权。'}],
                'ending_resolution': '对手失去冠名权。'}

    def test_trigger_and_missing_links(self):
        plan = self.plan()
        self.assertTrue(challenge.enabled(plan, self.contract()))
        prompt = challenge.prompt(plan, self.contract())
        self.assertIn('主办方当场没收', prompt)
        response = {'decision': 'revise',
                    'claim_quote': '主办方当场没收他的保证金并解除冠名权。',
                    'checks': [{'link': key, 'status': 'missing',
                                'source_quote': '未写', 'reason': '缺少取得途径'}
                               for key in challenge.LINKS]}
        challenge.validate(response, plan)
        self.assertEqual(len(challenge.issues(response)), 4)

    def test_claim_cannot_prove_itself(self):
        plan = self.plan()
        response = {'decision': 'pass',
                    'claim_quote': '主办方当场没收他的保证金并解除冠名权。',
                    'checks': [{'link': key, 'status': 'supported',
                                'source_quote': '主办方当场没收他的保证金并解除冠名权。',
                                'reason': '声称如此'} for key in challenge.LINKS]}
        with self.assertRaises(StoryError):
            challenge.validate(response, plan)

    def detailed_response(self, *, core_link=None):
        return {'decision': 'revise',
                'claim_quote': '主办方当场没收他的保证金并解除冠名权。',
                'checks': [{'link': key, 'status': 'missing', 'source_quote': '未写',
                            'reason': ('只有泼水，对手失去全部筹码却没有任何实际接招'
                                if key == core_link else '未写具体授权、通知和结算手续'),
                            'editorial_priority': 'reading_core' if key == core_link else 'copyedit_detail',
                            'reading_effect': ('只有泼水，核心商业筹码却凭空转移，反击没有取得过程'
                                if key == core_link else '反击与实际取得已看懂，缺少的只是文件手续')}
                           for key in challenge.LINKS]}

    def test_missing_procedures_are_reminders_without_replan(self):
        raw = self.detailed_response()
        result = challenge.validate(raw, self.plan())
        self.assertEqual(result['model_decision'], 'revise')
        self.assertEqual(result['decision'], 'pass')
        self.assertEqual(len(result['initial_copyedit_reminders']), 4)
        self.assertTrue(all(row['status'] == 'missing' for row in result['checks']))
        self.assertEqual(challenge.issues(result), [])
        self.assertEqual(raw['decision'], 'revise')
        self.assertNotIn('initial_copyedit_reminders', raw)
        self.assertEqual(challenge.validate(result, self.plan()), result)

    def test_actual_payoff_gap_still_requires_replan(self):
        result = challenge.validate(self.detailed_response(core_link='decision_maker'), self.plan())
        self.assertEqual(result['decision'], 'revise')
        self.assertEqual(len(result['initial_copyedit_reminders']), 3)
        issues = challenge.issues(result)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['repair_scope'], 'replan')
        self.assertEqual(issues[0]['editorial_priority'], 'reading_core')
        self.assertIn('核心商业筹码', issues[0]['reading_effect'])

    def test_core_gap_cannot_be_passed_despite_detail_reminders(self):
        raw = self.detailed_response(core_link='decision_maker')
        raw['decision'] = 'pass'
        with self.assertRaises(StoryError):
            challenge.validate(raw, self.plan())

    def test_prompt_teaches_reader_effect_without_procedural_expansion(self):
        prompt = challenge.prompt(self.plan(), self.contract())
        self.assertIn('reading_effect', prompt)
        self.assertIn('只有reading_core的missing', prompt)
        self.assertIn('金额巨大本身不增加', prompt)


if __name__ == '__main__':
    unittest.main()
