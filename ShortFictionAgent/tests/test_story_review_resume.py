"""An interrupted review resumes completed stages without reusing stale evidence."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.quality import REVIEW_RUBRIC, StoryReviewer
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class ReviewResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / 'story')
        self.project.create(StoryBrief.from_dict({
            'review_mode': 'deep_review', 'format': 'micro_story', 'length': {'target': 900, 'min': 600, 'max': 1100},
            'author_input': {'premise': '林禾找信', 'locked_facts': []},
        }))
        self.body = ('林禾发现信不见了。'
                     + '她追查记忆交易的线索，决定用自己的记忆换回那封信。' * 31
                     + '她找回信，也记得代价。')
        self.project.save_draft(self.body)
        self.llm = ScriptedLLM(self.body)
        self.flow = StoryWorkflow(self.project, self.llm)

    def test_transport_failure_reuses_completed_stage_then_clears_checkpoint(self):
        original = self.flow.reviewer.call_json
        failed = False

        def interrupt(task, prompt):
            nonlocal failed
            if task == 'continuity_review' and not failed:
                failed = True
                raise RuntimeError('result unknown')
            return original(task, prompt)

        self.flow.reviewer.call_json = interrupt
        with self.assertRaisesRegex(RuntimeError, 'result unknown'):
            self.flow.reviewer.review()
        self.assertEqual(self.llm.tasks.count('cold_read_review'), 1)
        self.assertTrue(list((self.project.root / 'review_resume').rglob('*.json')))

        self.flow.reviewer.call_json = original
        self.assertEqual(self.flow.reviewer.review()['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('cold_read_review'), 1)
        self.assertFalse(list((self.project.root / 'review_resume').rglob('*.json')))

        self.flow.reviewer.review()
        self.assertEqual(self.llm.tasks.count('cold_read_review'), 2)

    def test_changed_body_or_rubric_does_not_reuse_incomplete_review(self):
        original = self.flow.reviewer.call_json

        def interrupt(task, prompt):
            if task == 'continuity_review':
                raise RuntimeError('result unknown')
            return original(task, prompt)

        self.flow.reviewer.call_json = interrupt
        with self.assertRaises(RuntimeError):
            self.flow.reviewer.review()
        self.flow.reviewer.call_json = original

        record, _ = self.project.active_draft()
        self.project.save_draft(self.body + '后来，她又找到一张收据。',
                                artifact_id=record['artifact_id'],
                                parent_revision=record['revision'])
        self.flow.reviewer.review()
        self.assertEqual(self.llm.tasks.count('cold_read_review'), 2)

        with patch.dict(REVIEW_RUBRIC, {'language': '新的审读标准'}):
            self.flow.reviewer.review()
        self.assertEqual(self.llm.tasks.count('cold_read_review'), 3)

    def test_invalid_stage_is_evicted_before_repair(self):
        calls = []

        def generate(task, prompt):
            calls.append(prompt)
            return {'valid': len(calls) > 1}

        reviewer = StoryReviewer(self.project, generate)
        reviewer._stage_cache_dir = self.project.root / 'review_resume' / 'test'

        def validate(response):
            if not response['valid']:
                raise StoryError('字段无效')

        self.assertTrue(reviewer._checked_response('sample', '原任务', validate)['valid'])
        self.assertTrue(reviewer._checked_response('sample', '原任务', validate)['valid'])
        self.assertEqual(len(calls), 3)  # Original invalid response was not cached.


if __name__ == '__main__':
    unittest.main()
