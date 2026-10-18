"""Honor a CLI transport's wait without issuing a replacement model call."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import run_control
from short_fiction_agent.story_studio.contracts import StoryBrief
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow


class LocalTransport:
    def __init__(self):
        self.tasks = []
        self.outcome = {'status': 'unknown', 'chatId': None}
        self.error = None

    def generate(self, prompt, task_type):
        self.tasks.append(task_type)
        if self.error:
            raise self.error
        return '本地传输完成响应'


class StoryProviderTimeoutTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = StoryProject(Path(directory.name) / 'story')
        self.project.create(StoryBrief.from_dict({
            'format': 'micro_story', 'channels': ['generic_text'],
            'length': {'min': 20, 'target': 200, 'max': 400},
            'author_input': {'premise': '林禾找回一封信'},
        }))
        self.client = LocalTransport()

    def call_with_simulated_duration(self, elapsed):
        def wait(client, prompt, task, timeout_sec, *, config_dir):
            self.assertGreater(timeout_sec, elapsed,
                               'The workflow abandoned a still-running CLI transport')
            self.assertEqual(config_dir, str(self.project.root))
            return client.generate(prompt, task)
        with patch('short_fiction_agent.story_studio.workflow.generate_with_timeout',
                   side_effect=wait) as generate:
            result = StoryWorkflow(self.project, self.client).call_text(
                'chief_editor_review', '本地超时边界控制')
        self.assertEqual(result, '本地传输完成响应')
        return generate.call_args.args[3]

    def test_cli_wait_can_complete_after_the_old_180_second_deadline(self):
        self.client.timeout = 900
        self.assertEqual(self.call_with_simulated_duration(899), 915)
        self.assertEqual(self.client.tasks, ['chief_editor_review'])
        self.assertEqual(run_control.load(self.project)['used'], 1)

    def test_explicit_wall_clock_limit_takes_precedence_over_transport_option(self):
        self.client.wall_clock_timeout_seconds = 1200.1
        self.client.timeout = 30
        self.assertEqual(self.call_with_simulated_duration(1200.1), 1216)

    def test_ordinary_or_non_wall_clock_timeout_keeps_180_second_default(self):
        for value in (None, True, 0, -1, float('nan'), float('inf'), (10, 900), '900'):
            with self.subTest(timeout=value):
                self.client.timeout = value
                self.assertEqual(self.call_with_simulated_duration(1), 180)

    def test_unknown_transport_exception_is_preserved_without_retry(self):
        self.client.timeout = 900
        error = RuntimeError('unknown: submitted CLI response is unresolved')
        self.client.error = error
        original_outcome = dict(self.client.outcome)
        def wait(client, prompt, task, timeout_sec, **kwargs):
            self.assertEqual(timeout_sec, 915)
            return client.generate(prompt, task)
        with patch('short_fiction_agent.story_studio.workflow.generate_with_timeout', side_effect=wait):
            with self.assertRaises(RuntimeError) as caught:
                StoryWorkflow(self.project, self.client).call_text('chief_editor_review', '本地未知状态控制')
        self.assertIs(caught.exception, error)
        self.assertEqual(self.client.tasks, ['chief_editor_review'])
        self.assertEqual(self.client.outcome, original_outcome)
        self.assertEqual(run_control.load(self.project)['used'], 1)


if __name__ == '__main__':
    unittest.main()
