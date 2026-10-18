"""The workflow and provider use the same budget units and keep source bytes."""
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.context_budget import estimate_tokens
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.run_control import load as load_control
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class PromptBudgetTests(unittest.TestCase):
    def project(self, folder):
        project = StoryProject(Path(folder))
        project.create(StoryBrief.from_dict({'title': '预算控制', 'format': 'short_story',
            'channels': ['generic_text'], 'author_input': {'premise': '当面归还一封旧信。'}}))
        return project

    def test_large_json_within_token_budget_reaches_provider_unchanged(self):
        class Capture:
            max_prompt_tokens = 50000

            def generate(self, prompt, task_type):
                self.received = prompt
                return '完整响应'

        prompt = '{"source":"' + 'a' * 36000 + '"}'
        self.assertGreater(len(prompt), 50000 * .7)
        self.assertLess(estimate_tokens(prompt), 50000)
        with tempfile.TemporaryDirectory() as folder:
            project, llm = self.project(folder), Capture()
            flow = StoryWorkflow(project, llm)
            self.assertEqual(flow.call_text('budget_unit_probe', prompt), '完整响应')
            self.assertEqual(llm.received, prompt)

    def test_actual_token_overflow_stops_before_call_or_reservation(self):
        class NeverCall:
            max_prompt_tokens = 1000

            def generate(self, prompt, task_type):
                raise AssertionError('Over-budget input reached the provider')

        prompt = '中' * 680
        self.assertGreater(estimate_tokens(prompt), 1000)
        with tempfile.TemporaryDirectory() as folder:
            project = self.project(folder)
            flow = StoryWorkflow(project, NeverCall())
            with self.assertRaisesRegex(StoryError, '安全上下文预算'):
                flow.call_text('budget_unit_probe', prompt)
            self.assertEqual(load_control(project, flow.max_calls)['used'], 0)

    def test_default_provider_plans_with_full_fanqie_guide_within_actual_budget(self):
        class Capture(ScriptedLLM):
            max_prompt_tokens = 50000

            def generate(self, prompt, task_type):
                if task_type == 'story_plan':
                    self.plan_prompt = prompt
                return super().generate(prompt, task_type)

        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'title': '归还旧信', 'format': 'short_story',
                'channels': ['fanqie_short'], 'length': {'target': 10000},
                'author_input': {'premise': '归还旧信后，她要我正式约她。'}}))
            llm = Capture('测试传输正文。')
            flow = StoryWorkflow(project, llm)
            flow.develop()
            plan = flow.plan()
            self.assertTrue(plan['chapters'])
            self.assertIn('来源执行摘要：', llm.plan_prompt)
            self.assertIn('每章500—1000字', llm.plan_prompt)
            self.assertIn('至少两类阅读价值', llm.plan_prompt, 'Outline reading points were omitted')
            self.assertLessEqual(estimate_tokens(llm.plan_prompt), llm.max_prompt_tokens)


if __name__ == '__main__':
    unittest.main()
