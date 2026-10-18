import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio import reader_rewards
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from short_fiction_agent.story_studio.quality import review_decision, review_rubric_hash, DIMENSIONS
from short_fiction_agent.story_studio.contracts import digest


class ReaderRewardTests(unittest.TestCase):
    def brief(self, rewards=None):
        data = {'title': '测试', 'reader_promise': '自主夺权并相爱',
                'author_input': {'premise': '主人公逆转处境'}}
        if rewards is not None:
            data['reader_rewards'] = rewards
        return StoryBrief.from_dict(data)

    def spec(self):
        return [{'id': 'money', 'desire': '获得资源', 'payoff': '获得可支配现金', 'required': True},
                {'id': 'attraction', 'desire': '被主动喜欢', 'payoff': '对方主动告白和亲吻', 'required': True}]

    def checks(self):
        return {'checks': [
            {'id': 'money', 'verdict': 'fulfilled', 'repair_scope': 'none', 'reason': '现金可用', 'fix_intent': '保留', 'quotes': ['收到奖金']},
            {'id': 'attraction', 'verdict': 'missing', 'repair_scope': 'replan', 'reason': '没有主动亲近', 'fix_intent': '展开相互吸引', 'quotes': []}]}

    def test_empty_contract_keeps_existing_hash_shape(self):
        self.assertNotIn('reader_rewards', self.brief().effective_contract())
        self.assertEqual(self.brief().effective_contract().get('reader_rewards', []), [])
        self.assertEqual(reader_rewards.gate('安静的回忆。', {}, None), [])

    def test_author_promise_cannot_silently_skip_reward_review(self):
        contract = {'format': 'short_story', 'reader_promise': '夺回控制，并被主动喜欢'}
        rewards = reader_rewards.effective_spec(contract)
        self.assertEqual(rewards[0]['id'], 'core_promise')
        self.assertEqual(rewards[0]['payoff'], contract['reader_promise'])
        with self.assertRaises(StoryError):
            reader_rewards.gate('他获得了控制。', contract, None)
        response = {'checks': [{'id': 'core_promise', 'verdict': 'missing',
            'repair_scope': 'replan', 'reason': '只有控制没有双向偏爱', 'fix_intent': '建立相互选择', 'quotes': []}]}
        self.assertEqual(reader_rewards.gate('他获得了控制。', contract, response),
                         ['reader_reward:core_promise:missing'])

    def test_each_required_reward_blocks_separately(self):
        contract = self.brief(self.spec()).effective_contract()
        self.assertEqual(reader_rewards.gate('他收到奖金。', contract, self.checks()),
                         ['reader_reward:attraction:missing'])
        # A positive resource result cannot compensate for absent attraction.
        self.assertEqual(len(reader_rewards.issues('他收到奖金。', contract, self.checks())), 1)

    def test_high_scores_cannot_override_missing_required_reward(self):
        body = '他收到奖金。' + '。' * 900
        contract = self.brief(self.spec()).effective_contract()
        report = {'content_hash': digest(body), 'contract_hash': digest(contract),
                  'review_rubric_hash': review_rubric_hash(),
                  'review_coverage': {'complete': True, 'spans': [[0, len(body)]]},
                  'dimensions': {key: 4 for key in DIMENSIONS},
                  'dimension_evidence': {key: '测试高分不能抵消缺失回报' for key in DIMENSIONS},
                  'issues': [], 'reader_reward_review': self.checks()}
        result = review_decision(body, contract, report)
        self.assertEqual(result['decision'], 'revise_required')
        self.assertIn('reader_reward:attraction:missing', result['blockers'])

    def test_missing_or_invented_evidence_cannot_pass(self):
        contract = self.brief(self.spec()).effective_contract()
        with self.assertRaises(StoryError):
            reader_rewards.gate('他收到奖金。', contract, None)
        checks = self.checks()
        checks['checks'][1].update(verdict='fulfilled', quotes=['她主动亲吻了他'])
        with self.assertRaises(StoryError):
            reader_rewards.gate('他收到奖金。', contract, checks)

    def test_optional_reward_is_recorded_without_blocking(self):
        spec = self.spec()
        spec[1]['required'] = False
        self.assertEqual(reader_rewards.gate('收到奖金', self.brief(spec).effective_contract(), self.checks()), [])

    def test_planning_cannot_drop_a_required_reward(self):
        contract = self.brief(self.spec()).effective_contract()
        plan = {'scenes': [{'scene_id': 's1'}, {'scene_id': 's2'}], 'promises': [
            {'reader_reward_id': 'money', 'payoff_scene': 's1', 'promise': '获得奖金'}]}
        with self.assertRaises(StoryError):
            reader_rewards.validate_plan(plan, contract)
        plan['promises'].append({'reader_reward_id': 'attraction', 'payoff_scene': 's2', 'promise': '主动告白'})
        reader_rewards.validate_plan(plan, contract)
        plan['promises'][1]['payoff_scene'] = 's999'
        with self.assertRaises(StoryError):
            reader_rewards.validate_plan(plan, contract)

    def test_repeated_or_malformed_specs_rejected(self):
        spec = self.spec()
        with self.assertRaises(StoryError):
            self.brief(spec + [spec[0]])
        spec[0]['required'] = 'yes'
        with self.assertRaises(StoryError):
            self.brief(spec)

    def test_raw_scene_generation_gets_contract_rewards(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory))
            project.create(self.brief(self.spec()))
            workflow = StoryWorkflow(project, object())
            captured = []
            def generate(llm, prompt, task, *args, **kwargs):
                captured.append(prompt)
                return '真实正文。'
            with patch('short_fiction_agent.story_studio.workflow.generate_with_timeout', generate):
                workflow.call_text('write_story_scene', '写一场戏')
            self.assertIn('获得可支配现金', captured[0])
            self.assertIn('对方主动告白和亲吻', captured[0])
            self.assertIn('不把所有男生都写成同一种人', captured[0])

    def test_missing_reward_chain_returns_to_planning(self):
        contract = self.brief(self.spec()).effective_contract()
        checks = self.checks()
        checks['checks'][1]['repair_scope'] = 'replan'
        issue = reader_rewards.issues('他收到奖金。', contract, checks)[0]
        self.assertEqual(issue['repair_scope'], 'replan')
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory))
            project.create(self.brief(self.spec()))
            with self.assertRaisesRegex(StoryError, '退回规划层'):
                StoryWorkflow(project, object())._edit('他收到奖金。', issues=[issue])

    def test_reward_replan_context_keeps_failed_promise(self):
        from short_fiction_agent.story_studio.whole_story import repair_context
        issue = {'origin': 'reader_reward', 'reward_id': 'attraction',
                 'reason': '缺少互相吸引的前因', 'fix_intent': '重建主动选择链'}
        result = repair_context({'scenes': [], 'ending_resolution': '结束'}, [issue], 'hash')
        self.assertEqual(result['unresolved_issues'][0]['reward_id'], 'attraction')
        self.assertEqual(result['unresolved_issues'][0]['origin'], 'reader_reward')

    def test_fulfilled_reward_cannot_request_replan(self):
        checks = self.checks()
        checks['checks'][0]['repair_scope'] = 'replan'
        with self.assertRaises(StoryError):
            reader_rewards.validate_checks('收到奖金', self.brief(self.spec()).effective_contract(), checks)

    def test_missing_repair_scope_cannot_default_to_local(self):
        checks = self.checks()
        del checks['checks'][1]['repair_scope']
        with self.assertRaisesRegex(StoryError, 'repair_scope'):
            reader_rewards.validate_checks('收到奖金', self.brief(self.spec()).effective_contract(), checks)


if __name__ == '__main__':
    unittest.main()
