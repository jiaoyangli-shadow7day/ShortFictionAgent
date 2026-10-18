"""Regression coverage for actual production failures and feedback prompt wiring."""
import json
import tempfile
import unittest
import io
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.feedback import record_observation, use_editorial_feedback, load_editorial_context
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow, normalize_scene_plan
from test_story_studio import ScriptedLLM, _prompt_object
from test_story_opening_binding import OpeningLLM
from test_story_feedback import observation


class AgentLearningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = StoryProject(self.root / 'source')
        self.project = StoryProject(self.root / 'new')
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'target': 900, 'min': 600, 'max': 1100},
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        self.body = '林禾发现信不见了。' + '她追查记忆交易的线索，决定用自己的记忆换回那封信。' * 31 + '她找回信，也记得代价。'

    def choose(self, metrics=None):
        row = observation()
        if metrics is not None:
            row['metrics'] = metrics
        _, saved = record_observation(self.source, row)
        return use_editorial_feedback(self.project, self.source, saved['observation_id'])

    def test_funnel_snapshot_preserves_counts_and_unknowns(self):
        snap = self.choose(dict(display_count=191, read_count=37, read_15s=19,
                                read_30s=15, read_60s=11, bottom_count=7))
        data = snap['payload']
        self.assertEqual(data['sample_counts']['bottom_count'], 7)
        self.assertAlmostEqual(data['rates']['ctr']['value'], 37/191)
        self.assertEqual(data['rates']['read_15s_per_read']['denominator'], 37)
        self.assertEqual(data['experiment_options'][0]['observed_gap']['count_difference'], 18)
        self.assertFalse(data['automatic_learning'])
        self.assertEqual(data['commercial_validation'], 'unverified')
        snap = self.choose({'display_count': 191, 'read_count': 37})
        self.assertIsNone(snap['payload']['rates']['read_15s_per_read']['value'])
        self.assertEqual(snap['payload']['experiment_options'], [])

    def test_snapshot_is_explicit_and_bound_to_source(self):
        self.assertEqual(load_editorial_context(self.project), {})
        snap = self.choose()
        self.assertEqual(load_editorial_context(self.project), snap['payload'])
        records = self.source.root / 'feedback/platform_records.jsonl'
        row = json.loads(records.read_text())
        row['metrics']['display_count'] = 99
        records.write_text(json.dumps(row))
        with self.assertRaisesRegex(StoryError, '版本已更改'):
            load_editorial_context(self.project)

    def test_public_cli_dispatches_feedback_selection(self):
        from short_fiction_agent.cli import main
        _, saved = record_observation(self.source, observation())
        args = ['short-fiction-agent', 'use-feedback', '--project', str(self.project.root),
                '--source-project', str(self.source.root), '--observation-id', saved['observation_id']]
        output=io.StringIO()
        with patch('sys.argv',args), redirect_stdout(output), self.assertRaises(SystemExit) as exit:
            main()
        self.assertEqual(exit.exception.code,0)
        self.assertEqual(json.loads(output.getvalue())['payload']['observation_id'],saved['observation_id'])

    def test_damaged_snapshot_and_unknown_id_rejected(self):
        self.choose()
        with self.assertRaisesRegex(StoryError, 'ID不存在'):
            use_editorial_feedback(self.project, self.source, 'missing')
        path = self.project.root / 'feedback/editorial_context.json'
        snap = read_json(path); snap['payload']['sample_counts']['read_count'] = 999
        write_json(path, snap)
        with self.assertRaisesRegex(StoryError, '快照损坏'):
            load_editorial_context(self.project)

    def test_real_generation_receives_feedback_but_reviews_do_not(self):
        self.choose()
        llm = OpeningLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop(); result = flow.generate()
        self.assertEqual(result['decision'], 'passed')
        for task in ('story_candidates', 'opening_candidates', 'story_blueprint', 'write_micro_story'):
            prompts = [p for t,p in llm.prompts if t == task]
            self.assertTrue(prompts, task)
            self.assertTrue(all('experiment_options' in p for p in prompts), task)
        for task,prompt in llm.prompts:
            if task in ('cold_read_review', 'continuity_review', 'causal_challenge', 'market_review'):
                self.assertNotIn('experiment_options', prompt)
        old_count = sum(t == 'story_candidates' for t,p in llm.prompts)
        self.choose({'display_count': 100, 'read_count': 20})
        with self.assertRaisesRegex(StoryError, '快照已更改'):
            flow._selection()
        flow.develop()
        self.assertEqual(sum(t == 'story_candidates' for t,p in llm.prompts), old_count+1)
        path=self.project.root/'feedback/editorial_context.json'
        path.write_text('{"damaged":true}')
        # Independent review has no dependency on feedback availability or content.
        self.assertEqual(flow.reviewer.review()['decision'],'passed')

    def test_wrapped_initial_and_repair_plans_validate_without_duplicate_calls(self):
        self.project = StoryProject(self.root / 'short')
        self.project.create(StoryBrief.from_dict({'format':'short_story', 'review_mode':'deep_review',
            'length':{'target':7600,'min':6000,'max':9500},
            'author_input':{'premise':'找回钥匙','locked_facts':[]}}))
        class Wrapped(ScriptedLLM):
            audits = 0
            def generate(self, prompt, task_type):
                if task_type == 'story_plan':
                    return json.dumps({'contract': {}, 'plan': json.loads(super().generate(prompt, task_type))})
                if task_type == 'blueprint_audit':
                    self.tasks.append(task_type); self.audits += 1
                    return json.dumps({'decision':'revise' if self.audits==1 else 'pass',
                        'issues':[{'path':'knowledge','reason':'知情时间矛盾','missing_link':'同步时间'}] if self.audits==1 else []})
                if task_type == 'story_plan_repair':
                    self.tasks.append(task_type)
                    data = _prompt_object(prompt, ('plan',))
                    return json.dumps({'contract':{}, 'plan':data['plan']})
                return super().generate(prompt, task_type)
        llm=Wrapped('正文'); flow=StoryWorkflow(self.project,llm)
        flow.develop()
        with self.assertRaisesRegex(StoryError, '仍有核心硬伤'):
            flow.plan()
        self.assertEqual(llm.tasks.count('story_plan_repair'),1)
        self.assertIn('plan',read_json(self.project.root/'plan/repair-1.json'))
        self.assertEqual(llm.audits, 1)

    def test_ambiguous_wrapper_cannot_choose_or_bypass_validation(self):
        with self.assertRaisesRegex(StoryError, '相互冲突'):
            normalize_scene_plan({'scenes':[], 'plan':{'scenes':[]}})
        with self.assertRaises(StoryError):
            normalize_scene_plan({'plan':'not a plan'})
        flow=StoryWorkflow(self.project,OpeningLLM(self.body))
        with self.assertRaises(StoryError):
            flow._validate_compact_plan(normalize_scene_plan({'plan':{'scenes':[]}}), self.project.contract())


if __name__ == '__main__':
    unittest.main()
