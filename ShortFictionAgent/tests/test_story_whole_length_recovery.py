"""Editing may change total length even when all individual scenes were valid."""
import json
from pathlib import Path
import tempfile
import unittest
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, visible_count
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class WholeLengthRecoveryTests(unittest.TestCase):
    def project(self, maximum=8500):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        project = StoryProject(Path(temp.name) / 'story')
        project.create(StoryBrief.from_dict({'format': 'short_story',
            'length': {'target': 6800, 'min': 6000, 'max': maximum},
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        return project

    def llm(self, edits):
        class LocalEditLLM(ScriptedLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'story_edit':
                    inner.tasks.append(task_type)
                    inner.prompt = prompt
                    return json.dumps({'back_to_plan': False, 'edits': edits}, ensure_ascii=False)
                return super().generate(prompt, task_type)
        return LocalEditLLM('')

    def test_shortened_long_story_recovers_through_local_edits(self):
        project = self.project()
        body = '甲' * 5860 + '她找回信。'
        llm = self.llm([{'quote': '她找回信。', 'replacement': '她走过长街。' * 60 + '她找回信。'}])
        flow = StoryWorkflow(project, llm)
        repaired = flow._repair_length(body, project.contract())
        self.assertGreaterEqual(visible_count(repaired), 6000)
        self.assertTrue(repaired.startswith('甲' * 5860))
        self.assertEqual(llm.tasks, ['story_edit'])
        self.assertIn('5865', llm.prompt)
        self.assertEqual(flow._repair_length(repaired, project.contract()), repaired)
        self.assertEqual(llm.tasks, ['story_edit'])

    def test_oversized_long_story_uses_grounded_deletion(self):
        project = self.project()
        body = '甲' * 8200 + '重复解释。' * 90 + '她找回信。'
        llm = self.llm([{'quote': '重复解释。' * 90, 'replacement': ''}])
        repaired = StoryWorkflow(project, llm)._repair_length(body, project.contract())
        self.assertLessEqual(visible_count(repaired), 8500)
        self.assertTrue(repaired.strip().endswith('她找回信。'))
        self.assertEqual(llm.tasks, ['story_edit'])

    def test_invented_quote_cannot_recover_length(self):
        project = self.project()
        original = project.save_draft('甲' * 5900)
        body = project.active_draft()[1]
        llm = self.llm([{'quote': '不存在的原文', 'replacement': '甲' * 300}])
        for _ in range(2):
            with self.assertRaisesRegex(StoryError, '不存在的原文'):
                StoryWorkflow(project, llm)._repair_length(body, project.contract())
        self.assertEqual(llm.tasks, ['story_edit'])
        self.assertEqual(project.active_draft()[0]['content_hash'], original['content_hash'])
        self.assertEqual(project.active_draft()[1], body)
        records = list((project.root / 'edit_responses').glob('*.json'))
        self.assertEqual(len(records), 1)
        self.assertEqual(read_json(records[0])['response'], {'back_to_plan': False,
            'edits': [{'quote': '不存在的原文', 'replacement': '甲' * 300}]})

    def test_unbounded_valid_long_story_needs_no_model_call(self):
        project = self.project(maximum=None)
        llm = self.llm([])
        body = '甲' * 9000
        self.assertEqual(StoryWorkflow(project, llm)._repair_length(body, project.contract()), body)
        self.assertEqual(llm.tasks, [])


if __name__ == '__main__':
    unittest.main()
