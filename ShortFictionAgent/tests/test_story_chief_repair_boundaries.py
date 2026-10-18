"""Verify repair routing and author approval before a candidate can be saved."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import lead
from short_fiction_agent.story_studio import chief_editor, run_control, workflow
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM
from tests.narrative_fixtures import narrative_response


class ChiefRepairBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = StoryProject(Path(self.temporary.name) / 'project')

    def test_production_global_issue_stops_local_edit_and_can_reopen_selection(self):
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100, 'max': 1000},
            'author_input': {'premise': '新未来不能被原历史预测'}}))
        self.project.save_draft('手机只存着原历史。\n结局却靠未输入的新未来答案取胜。')
        response = {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
            'severity': 'blocker', 'evidence_ids': ['p0', 'p1'], 'scope': 'global',
            'reason': '传输控制：结局的取胜机制需要重做。',
            'fix_intent': '更换依赖未授权能力的全篇取胜机制。'}]}
        report = StoryReviewer(self.project, lambda task, prompt: narrative_response(prompt, response)).review()
        issue = report['issues'][0]
        self.assertEqual(issue['origin'], 'chief_editor')
        self.assertEqual(issue['repair_scope'], 'replan')
        flow = StoryWorkflow(self.project, None)
        body = self.project.active_draft()[1]
        with patch.object(flow, 'call_json', side_effect=AssertionError('Must not locally rewrite')):
            with self.assertRaisesRegex(StoryError, '退回规划层'):
                flow._edit(body, issues=report['issues'])
        request = read_json(self.project.root / 'structure_repair_request.json')
        self.assertEqual(request['content_hash'], digest(body))
        self.assertEqual(request['issues'], report['issues'])
        with patch.object(flow, 'call_json', side_effect=AssertionError('Must not locally rewrite')):
            self.assertEqual(flow._finish_chief_generation({}, self.project.active_draft()[0], auto_revise=True,
                entry_point='StoryWorkflow.generate', source_receipt_hash=None), report)
        delivered = read_json(self.project.root / 'review_copy/稿件状态.json')
        self.assertEqual(delivered['review_status'], 'revise_required')
        self.assertEqual(delivered['issues'], report['issues'])
        self.assertIsNone(delivered['error'])
        self.assertEqual(delivered['state'], 'needs_replan')
        self.assertEqual(self.project.active_draft()[0]['state'], 'needs_replan')
        self.assertEqual(run_control.load(self.project)['automatic_repairs'], 0)
        self.assertEqual(run_control.load(self.project)['used'], 0)
        self.assertEqual(delivered['content_hash'], digest(body))
        with patch.object(flow, 'develop', return_value={'refresh': True}) as develop:
            self.assertEqual(flow.reselect_after_rejected_draft(), {'refresh': True})
            develop.assert_called_once_with(refresh=True)
        context = read_json(self.project.root / 'reselection_context.json')
        self.assertEqual(context['source_content_hash'], digest(body))
        self.assertEqual(context['structural_failures'][0]['fix_intent'], issue['fix_intent'])

    def test_changed_preflight_or_global_editor_rules_invalidate_saved_passes(self):
        self.project.create(StoryBrief.from_dict({'format': 'short_story',
            'length': {'min': 2500, 'target': 3500, 'max': 4500},
            'author_input': {'premise': '林禾取回一封信，付出自己选择的记忆代价'}}))
        llm = ScriptedLLM('传输控制正文。')
        flow = StoryWorkflow(self.project, llm, max_calls=100)
        flow.develop()
        original = flow.plan()
        calls = len(llm.tasks)
        self.assertEqual(flow.plan(), original)
        self.assertEqual(len(llm.tasks), calls)
        with patch.object(workflow, 'CHIEF_PLAN_INSTRUCTION', workflow.CHIEF_PLAN_INSTRUCTION + '新增金额归属规则。'):
            upgraded = StoryWorkflow(self.project, llm, max_calls=100).plan()
            self.assertNotEqual(upgraded['planning_hash'], original['planning_hash'])
            self.assertEqual(llm.tasks.count('chief_plan_review'), 2)
            with patch.object(chief_editor, 'INSTRUCTION', chief_editor.INSTRUCTION + '新增全局主审规则。'):
                latest = StoryWorkflow(self.project, llm, max_calls=100).plan()
                self.assertNotEqual(latest['planning_hash'], upgraded['planning_hash'])
                self.assertEqual(llm.tasks.count('chief_plan_review'), 3)
                previous_count = len(llm.tasks)
                self.assertEqual(StoryWorkflow(self.project, llm, max_calls=100).plan(), latest)
                self.assertEqual(len(llm.tasks), previous_count)
        records = [read_json(path) for path in (self.project.root / 'plan/chief_preflight').glob('*.json')]
        self.assertEqual(len(records), 3)
        self.assertEqual(len({record['binding']['criteria_hash'] for record in records}), 3)
        self.assertEqual(len({record['input_hash'] for record in records}), 3)
        self.assertTrue(all(record['binding']['prompt_hash'] for record in records))

    def test_changed_prose_keeps_old_report_reference_without_reusing_its_pass(self):
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100, 'max': 1000},
            'author_input': {'premise': '她来取回父亲留下的借条'}}))
        first = self.project.save_draft('她递出借条，我答应明天还钱。')
        report = StoryReviewer(self.project, lambda task, prompt: narrative_response(prompt)).review()
        changed = self.project.save_draft('她收起借条，决定不再等我。',
            artifact_id=first['artifact_id'], parent_revision=first['revision'])
        StoryWorkflow(self.project, None)._save_review_copy(report, error='transport outcome unknown')
        delivered = read_json(self.project.root / 'review_copy/稿件状态.json')
        self.assertEqual(delivered['review_status'], 'pending')
        self.assertEqual(delivered['content_hash'], changed['content_hash'])
        self.assertEqual(delivered['issues'], [])
        self.assertEqual(delivered['previous_review']['review_id'], report['review_id'])
        self.assertEqual(delivered['previous_review']['content_hash'], report['content_hash'])
        self.assertTrue(Path(delivered['previous_review']['path']).is_file())

    def test_length_rewrite_cannot_replace_confirmed_lead_before_save(self):
        self.project.create(StoryBrief.from_dict({'format': 'short_story',
            'opening_mode': 'lead_then_chapter',
            'length': {'min': 200, 'target': 300, 'max': 500},
            'author_input': {'premise': '她拿着旧借条来兑现父亲的承诺'}}))
        lead_text = '她递来旧借条，问我父亲的承诺还算不算数。'
        saved = self.project.save_draft(lead.assemble(lead_text, '她站在门口等我回答。'))
        original = self.project.active_draft()[1]
        contract = self.project.contract()
        write_json(self.project.root / 'approved_lead.json', lead.approve_lead(original, contract))
        bad = lead.assemble('她昨天已经收到了全部欠款。', '她递出借条，我决定偿还。' * 22)
        good = lead.assemble(lead_text, '她递出借条，我决定偿还。' * 22)

        class RewriteLLM:
            def __init__(self):
                self.answer = bad
                self.tasks = []
            def generate(self, prompt, task_type):
                self.tasks.append(task_type)
                return self.answer

        llm = RewriteLLM()
        flow = StoryWorkflow(self.project, llm)
        with self.assertRaisesRegex(StoryError, '确认原文不一致'):
            flow._repair_length(original, contract)
        record, body = self.project.active_draft()
        self.assertEqual(record['revision'], saved['revision'])
        self.assertEqual(body, original)
        self.assertEqual(llm.tasks, ['repair_story_length'])
        self.assertFalse(list((self.project.root / 'drafts' / saved['artifact_id']).glob('2')))
        llm.answer = good
        repaired = flow._repair_length(original, contract)
        self.assertEqual(lead.split(repaired)[0], lead_text)
        self.assertTrue(lead.validate_approved_lead(repaired, contract,
            read_json(self.project.root / 'approved_lead.json'))['needs_fact_review'])
        self.assertEqual(self.project.active_draft()[1], original)


if __name__ == '__main__':
    unittest.main()
