"""Completed but malformed comparisons may be repaired once; transport errors stop."""
import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class MalformedComparisonLLM(ScriptedLLM):
    def __init__(self, task, failures=1, transport=False):
        super().__init__('林禾找到了信。')
        self.target, self.failures, self.transport = task, failures, transport
        self.attempts = 0
        self.comparison_prompts = []

    def generate(self, prompt, task_type):
        result = super().generate(prompt, task_type)
        if task_type == self.target:
            self.attempts += 1
            self.comparison_prompts.append(prompt)
            if self.transport:
                raise RuntimeError('transport outcome unknown')
            if self.attempts <= self.failures:
                result = json.loads(result)
                result['comparisons'][1].pop('rank')
                return json.dumps(result, ensure_ascii=False)
        return result


class ComparisonRecoveryTests(unittest.TestCase):
    def flow(self, task, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        project = StoryProject(Path(temp.name) / 'story')
        project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'target': 900, 'min': 600, 'max': 1100},
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        llm = MalformedComparisonLLM(task, **kwargs)
        return project, llm, StoryWorkflow(project, llm)

    def test_missing_rank_recovers_both_comparison_stages(self):
        for task, filename in (('candidate_comparative_review', 'candidate_comparison.json'),
                               ('opening_comparative_review', 'opening_comparison.json')):
            with self.subTest(task=task):
                project, llm, flow = self.flow(task)
                flow.develop()
                self.assertEqual(llm.attempts, 2)
                self.assertIn('上次响应字段校验失败', llm.comparison_prompts[1])
                self.assertIsNotNone(read_json(project.root / filename)['recommended_id'])
                rejected = list((project.root / 'invalid_responses').glob('*.json'))
                self.assertEqual(len(rejected), 1)
                self.assertNotIn('rank', read_json(rejected[0])['comparisons'][1])
                flow._selection()

    def test_two_invalid_responses_do_not_create_selection(self):
        project, llm, flow = self.flow('opening_comparative_review', failures=2)
        with self.assertRaisesRegex(StoryError, '排名必须唯一且完整'):
            flow.develop()
        self.assertEqual(llm.attempts, 2)
        self.assertFalse((project.root / 'selection.json').exists())
        self.assertEqual(len(list((project.root / 'invalid_responses').glob('*.json'))), 2)

    def test_unknown_transport_is_not_schema_retry(self):
        project, llm, flow = self.flow('opening_comparative_review', transport=True)
        with self.assertRaisesRegex(RuntimeError, 'outcome unknown'):
            flow.develop()
        self.assertEqual(llm.attempts, 1)
        self.assertFalse((project.root / 'selection.json').exists())
        self.assertFalse((project.root / 'invalid_responses').exists())


if __name__ == '__main__':
    unittest.main()
