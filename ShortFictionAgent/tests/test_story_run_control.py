"""Persistent run limits and the default chief-editor production path.

Scripted judgments exercise orchestration and source coverage, not prose quality.
"""
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio import chief_editor, run_control
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import current_review
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow, scene_budget
from test_story_studio import ScriptedLLM, _prompt_object


class RecordingLLM(ScriptedLLM):
    def __init__(self, body):
        super().__init__(body)
        self.prompts = []

    def generate(self, prompt, task_type):
        self.prompts.append((task_type, prompt))
        return super().generate(prompt, task_type)


class StoryRunControlTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.project = StoryProject(Path(directory.name) / 'story')
        self.project.create(StoryBrief.from_dict({
            'format': 'micro_story',
            'genre': {'primary': '县城科幻推理',
                      'custom_description': '只能交易亲历过的记忆'},
            'length': {'min': 600, 'target': 900, 'max': 1100},
            'story_engine': 'mystery_reveal',
            'author_input': {'premise': '林禾找一封信', 'locked_facts': []},
            'world': {'rule': '只能转移亲历过的记忆'},
        }))
        self.body = ('林禾发现信不见了。\n'
                     + '她追查记忆交易的线索，决定用自己的记忆换回那封信。' * 31
                     + '\n她找回信，也记得代价。')
        self.llm = RecordingLLM(self.body)

    def reopened(self):
        return StoryProject(self.project.root)

    def test_model_budget_accumulates_across_workflow_and_project_instances(self):
        first = StoryWorkflow(self.project, self.llm, max_calls=2)
        self.assertEqual(first.call_text('write_micro_story', '本地测试第一次调用'), self.body)
        second = StoryWorkflow(self.reopened(), self.llm, max_calls=200)
        self.assertEqual(second.calls, 1)
        second.call_text('write_micro_story', '本地测试恢复后调用')
        third = StoryWorkflow(self.reopened(), self.llm, max_calls=200)
        self.assertEqual(third.calls, 2)
        with self.assertRaisesRegex(StoryError, '累计模型调用预算'):
            third.call_text('write_micro_story', '新进程不能重置额度')
        self.assertEqual(self.llm.tasks, ['write_micro_story', 'write_micro_story'])
        self.assertEqual(run_control.load(self.reopened())['limit'], 2)
        self.assertEqual(run_control.load(self.reopened())['used'], 2)

    def test_stop_blocks_new_model_calls_but_keeps_local_draft_export_available(self):
        flow = StoryWorkflow(self.project, self.llm)
        flow.call_text('write_micro_story', '停止前已有一次调用')
        self.project.save_draft(self.body)
        _, saved = self.project.active_draft()
        stopped = run_control.stop(self.project)
        resumed = StoryWorkflow(self.reopened(), self.llm)
        count = len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError, '已停止'):
            resumed.call_text('write_micro_story', '停止后禁止调用')
        with self.assertRaisesRegex(StoryError, '已停止'):
            resumed.reviewer.review()
        package = StoryExporter(self.reopened(), resumed.call_json).export('generic_text', draft=True)
        self.assertEqual((package / 'manuscript.txt').read_text(encoding='utf-8'), saved)
        submission = read_json(package / 'submission_info.json')
        self.assertEqual(submission['delivery_status'], 'review_copy')
        self.assertEqual(submission['source_content_hash'], digest(saved))
        self.assertEqual(len(self.llm.tasks), count)
        self.assertEqual(run_control.load(self.reopened())['used'], stopped['used'])
        self.assertTrue(run_control.load(self.reopened())['stopped'])

    def test_one_automatic_repair_allowance_survives_resume_and_stop(self):
        StoryWorkflow(self.project, self.llm)
        self.assertTrue(run_control.reserve_repair(self.project))
        resumed = StoryWorkflow(self.reopened(), self.llm)
        self.assertFalse(run_control.reserve_repair(resumed.project))
        run_control.stop(resumed.project)
        self.assertFalse(run_control.reserve_repair(self.reopened()))
        run_control.authorize_continue(self.reopened(), extra_calls=3)
        self.assertFalse(run_control.reserve_repair(self.reopened()))
        state = run_control.load(self.reopened())
        self.assertEqual(state['automatic_repairs'], 1)
        self.assertEqual(state['used'], 0)
        self.assertEqual(self.llm.tasks, [])

    def test_json_syntax_repair_is_shared_across_tasks_and_resumes(self):
        class InvalidJSONLLM(ScriptedLLM):
            def generate(self, prompt, task_type):
                self.tasks.append(task_type)
                if task_type == 'json_format_repair':
                    return '{"recommendation":"ready","issues":[]}'
                return '{"recommendation":"ready","issues":[],}'

        llm = InvalidJSONLLM(self.body)
        first = StoryWorkflow(self.project, llm)
        self.assertEqual(first.call_json('chief_editor_review', '已完成判断，只需修复语法'),
                         {'recommendation': 'ready', 'issues': []})
        resumed = StoryWorkflow(self.reopened(), llm)
        with self.assertRaisesRegex(StoryError, 'JSON格式修复额度已用尽'):
            resumed.call_json('chief_editor_synthesis', '另一任务不能再申请语法修复')
        self.assertEqual(llm.tasks, ['chief_editor_review', 'json_format_repair', 'chief_editor_synthesis'])
        self.assertEqual(run_control.load(self.reopened())['format_repairs'], 1)
        self.assertEqual(run_control.load(self.reopened())['used'], 3)
        self.assertEqual(len(list((self.project.root / 'invalid_responses').glob('*.txt'))), 2)

    def test_final_scene_keeps_useful_budget_after_soft_target_is_exceeded(self):
        length = {'min': 21000, 'target': 24000, 'max': None}
        budget = scene_budget(length, count=8, index=7, written=25000)
        self.assertEqual(budget['min'], 1)
        self.assertIsNone(budget['max'])
        self.assertGreaterEqual(budget['target'], 2250)
        bounded = scene_budget({**length, 'max': 30000}, count=8, index=7, written=25000)
        self.assertEqual(bounded['max'], 5000)
        self.assertGreaterEqual(bounded['target'], 2250)
        self.assertLessEqual(bounded['target'], bounded['max'])

    def test_default_generate_reads_full_manuscript_with_one_editor_and_no_unprompted_edit(self):
        self.assertEqual(self.project.contract()['review_mode'], 'chief_editor')
        flow = StoryWorkflow(self.project, self.llm)
        flow.develop()
        report = flow.generate()
        _, body = self.project.active_draft()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(report['review_mode'], 'chief_editor')
        self.assertEqual(current_review(self.reopened())['review_id'], report['review_id'])
        self.assertEqual(report['content_hash'], digest(body))
        self.assertEqual(report['review_coverage']['spans'], [[0, len(body)]])
        old_tasks = {'story_edit', 'reader_experience_review', 'reader_basis_grounding',
                     'reader_basis_repair', 'cold_read_review', 'continuity_review',
                     'story_fact_review', 'causal_challenge', 'market_review',
                     'reader_reward_review', 'resource_state_review', 'whole_story_review',
                     'whole_story_challenge', 'lead_comparative_review', 'manuscript_lead_review'}
        self.assertFalse(old_tasks.intersection(self.llm.tasks), self.llm.tasks)
        readings = [_prompt_object(prompt, ('protocol', 'paragraphs'))
                    for task, prompt in self.llm.prompts if task == 'chief_editor_review']
        self.assertEqual(len(readings), 1)
        self.assertEqual(readings[0]['paragraphs'], [{'id': row['id'], 'text': row['text']}
                                                   for row in chief_editor.paragraphs(body)])
        self.assertEqual(readings[0]['author_contract']['author_input']['premise'], '林禾找一封信')
        self.assertEqual(run_control.load(self.reopened())['used'], len(self.llm.tasks))
        self.assertEqual(run_control.load(self.reopened())['automatic_repairs'], 0)
        delivered = read_json(self.project.root / 'review_copy' / '稿件状态.json')
        self.assertEqual(delivered['review_status'], 'passed')
        self.assertEqual((self.project.root / 'review_copy' / '当前完整稿.txt').read_text(encoding='utf-8'), body)


if __name__ == '__main__':
    unittest.main()
