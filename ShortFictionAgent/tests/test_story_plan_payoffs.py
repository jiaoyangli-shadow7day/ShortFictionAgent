import unittest
import tempfile
from pathlib import Path

from short_fiction_agent.story_studio import plan_payoffs
from short_fiction_agent.story_studio.contracts import StoryError, StoryBrief
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow


class PlanPayoffTests(unittest.TestCase):
    def contract(self):
        return {'format': 'short_story', 'reader_rewards': [
            {'id': 'reversal', 'desire': '夺回商业筹码',
             'payoff': '对手实际失去独家合作', 'required': True}]}

    def plan(self):
        return {'scenes': [
            {'scene_id': 's1', 'goal': '保护合作', 'pressure': '对手握有独家合同',
             'choice': '主角泼水', 'consequence': '对手裤子湿了'},
            {'scene_id': 's2', 'goal': '完成反击', 'pressure': '对手叫嚣',
             'choice': '主角当众嘲讽', 'consequence': '客户忽然改选主角'}]}

    def test_missing_major_consequence_requires_replan(self):
        response = {'checks': [{'id': 'reversal', 'verdict': 'missing',
            'reason': '泼水只让对手难堪，客户改选没有依据',
            'missing_link': '缺少客户见到新方案并作出选择的过程',
            'repair_scope': 'replan', 'scene_ids': ['s1', 's2']}]}
        plan_payoffs.validate(response, self.plan(), self.contract())
        self.assertEqual(plan_payoffs.issues(response, self.contract())[0]['repair_scope'], 'replan')
        self.assertIn('不能自动推出合同丢失', plan_payoffs.prompt(self.plan(), self.contract()))

    def test_payoff_review_receives_author_rules_and_checks_real_consequences(self):
        plan = self.plan()
        plan['scenes'][1]['progression_beats'] = [{'action': '现场投屏',
            'change': '声称没收保证金'}]
        contract = self.contract() | {'author_input': {'premise': '主角只会现场调度'}}
        prompt = plan_payoffs.prompt(plan, contract)
        self.assertIn('主角只会现场调度', prompt)
        self.assertIn('声称没收保证金', prompt)
        self.assertIn('谁有权作出决定', prompt)
        self.assertIn('报恩或结尾突然献吻', prompt)
        self.assertIn('“举报系统”不等于向现实警方举报', prompt)

    def test_missing_scope_or_scene_reference_fails_closed(self):
        row = {'id': 'reversal', 'verdict': 'missing', 'reason': '缺少改选',
               'missing_link': '客户没有回应', 'scene_ids': ['s2']}
        with self.assertRaises(StoryError):
            plan_payoffs.validate({'checks': [row]}, self.plan(), self.contract())
        row['repair_scope'] = 'replan'
        row['scene_ids'] = ['unknown']
        with self.assertRaises(StoryError):
            plan_payoffs.validate({'checks': [row]}, self.plan(), self.contract())

    def test_copyright_and_accounting_detail_never_trigger_payoff_replan(self):
        raw = {'checks': [{'id': 'reversal', 'verdict': 'missing',
            'reason': '没有写出海外版权授权文件和精确余额计算',
            'missing_link': '授权文件和余额核算步骤', 'repair_scope': 'replan',
            'scene_ids': ['s1', 's2'], 'editorial_priority': 'copyedit_detail',
            'reading_effect': '读者已经看懂合作与实际取得的筹码，手续与尾数不影响反击爽感'}]}
        result = plan_payoffs.validate(raw, self.plan(), self.contract())
        self.assertEqual(plan_payoffs.issues(result, self.contract()), [])
        self.assertEqual(result['checks'][0]['verdict'], 'missing')
        self.assertEqual(result['checks'][0]['repair_scope'], 'none')
        self.assertEqual(result['checks'][0]['model_repair_scope'], 'replan')
        self.assertEqual(len(result['initial_copyedit_reminders']), 1)
        self.assertEqual(raw['checks'][0]['repair_scope'], 'replan')

    def test_actual_contract_payoff_gap_still_blocks_despite_money_words(self):
        raw = {'checks': [{'id': 'reversal', 'verdict': 'missing',
            'reason': '只有嘲讽，没有客户看到新方案并改选，版权收入凭空发生',
            'missing_link': '客户实际接招和选择', 'repair_scope': 'replan',
            'scene_ids': ['s1', 's2'], 'editorial_priority': 'reading_core',
            'reading_effect': '没有实际对抗与取得过程，读者看不到反击如何赢'}]}
        result = plan_payoffs.validate(raw, self.plan(), self.contract())
        issues = plan_payoffs.issues(result, self.contract())
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['repair_scope'], 'replan')
        self.assertEqual(issues[0]['editorial_priority'], 'reading_core')
        self.assertEqual(result['initial_copyedit_reminders'], [])

    def test_prompt_keeps_actual_payoff_without_full_authorization_chain(self):
        prompt = plan_payoffs.prompt(self.plan(), self.contract())
        self.assertIn('不要求展示完整授权链', prompt)
        self.assertIn('reading_effect', prompt)
        self.assertIn('不触发重规划', prompt)

    def test_short_story_preflight_calls_payoff_gate_before_prose(self):
        rewards = self.contract()['reader_rewards'] + [
            {'id': 'wealth', 'desire': '拿到钱', 'payoff': '实际可支配', 'required': True}]
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story', 'review_mode': 'deep_review', 'title': '测试',
                'reader_promise': '夺回筹码并拿钱', 'reader_rewards': rewards,
                'author_input': {'premise': '对手握着独家合同'}}))
            flow = StoryWorkflow(project, object())
            tasks = []
            flow.call_json = lambda task, prompt: {'decision': 'pass', 'issues': []}
            def review(task, prompt, validator):
                tasks.append(task)
                return {'checks': [
                    {'id': ident, 'verdict': 'missing', 'reason': '只是宣称结果',
                     'missing_link': '缺少取得途径', 'repair_scope': 'replan',
                     'scene_ids': ['s1', 's2']} for ident in ('reversal', 'wealth')]}
            flow._call_validated_json = review
            with self.assertRaisesRegex(StoryError, '写前因果预审发现多处核心硬伤'):
                flow._preflight_plan(self.plan(), project.contract())
            self.assertEqual(tasks, ['plan_payoff_chain_review'])


if __name__ == '__main__':
    unittest.main()
