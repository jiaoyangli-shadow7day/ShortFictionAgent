"""Exercise default repair limits with actual generation and chief reports."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import lead, run_control
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import current_review
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, _prompt_object
from tests.narrative_fixtures import narrative_response


class ChiefConcentratedRepairTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def project(self, name='story', **options):
        project = StoryProject(self.root / name)
        project.create(StoryBrief.from_dict({
            'format': 'micro_story', 'channels': ['generic_text'],
            'length': {'min': 200, 'target': 300, 'max': 500},
            'author_input': {'premise': '林禾找回一封信'}, **options}))
        return project

    def test_no_auto_revise_preserves_underlength_and_over_hard_limit_prose(self):
        for name, body in (('short', '林禾拿起信。'), ('long', '林' * 501)):
            with self.subTest(name=name):
                project = self.project(name)
                llm = ScriptedLLM(body)
                flow = StoryWorkflow(project, llm, max_calls=100)
                flow.develop()
                report = flow.generate(auto_revise=False)
                self.assertEqual(report['decision'], 'revise_required')
                self.assertIn('length_contract', report['blockers'])
                self.assertEqual(project.active_draft()[1].strip(), body)
                self.assertEqual(project.active_draft()[0]['state'], 'needs_author')
                self.assertFalse({'story_edit', 'repair_story_length', 'repair_story_scene_length'} & set(llm.tasks))
                self.assertEqual(run_control.load(project)['automatic_repairs'], 0)
                copy = project.root / 'review_copy'
                self.assertEqual((copy / '当前完整稿.txt').read_text(), project.active_draft()[1])
                status = read_json(copy / '稿件状态.json')
                self.assertFalse(status['length']['within_range'])
                self.assertIn('length_contract', status['blockers'])
                self.assertEqual(status['length']['max'], 500)

    def test_short_scene_outputs_reach_one_chief_without_per_scene_rewrites(self):
        project = self.project(format='short_story',
            length={'min': 6000, 'target': 7600, 'max': 9500})
        class ShortSceneLLM(ScriptedLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'write_story_scene':
                    inner.tasks.append(task_type)
                    inner.scene_number += 1
                    marker = f'第{inner.scene_number}场，林禾找到另一条线索。'
                    # A modest whole-story deficit still belongs to one chief
                    # review. A missing main story is covered separately by the
                    # explicit needs-replan regression, not padded at the end.
                    return marker + '字' * (1400 - len(marker))
                return super().generate(prompt, task_type)
        llm = ShortSceneLLM('不会使用的整篇稿')
        flow = StoryWorkflow(project, llm, max_calls=100, whole_draft_limit=0)
        flow.develop()
        report = flow.generate(auto_revise=False)
        self.assertIn('length_contract', report['blockers'])
        self.assertGreater(llm.tasks.count('write_story_scene'), 1)
        self.assertEqual(llm.tasks.count('chief_editor_review'), 1)
        self.assertEqual(llm.tasks.count('repair_story_scene_length'), 0)
        self.assertEqual(llm.tasks.count('repair_story_length'), 0)
        self.assertEqual(llm.tasks.count('story_edit'), 0)
        self.assertIn('林禾找到另一条线索', project.active_draft()[1])

    def test_length_and_fact_problems_share_one_persistent_edit_allowance(self):
        project = self.project()
        initial = '林禾找错了信。'
        repaired = '林禾核对署名，找回父亲的信，也决定承担失去记忆的代价。' * 11
        class CombinedLLM(ScriptedLLM):
            edit_inputs = []
            def generate(inner, prompt, task_type):
                if task_type == 'chief_editor_review':
                    inner.tasks.append(task_type)
                    source = _prompt_object(prompt, ('paragraphs',))['paragraphs']
                    if initial in source[0]['text']:
                        return json.dumps(narrative_response(prompt, {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
                            'severity': 'blocker', 'evidence_ids': [source[0]['id']], 'scope': 'local',
                            'reason': '传输控制：署名与归还对象不一致。', 'fix_intent': '核对署名后归还。'}]}))
                    return json.dumps(narrative_response(prompt))
                if task_type == 'story_edit':
                    inner.tasks.append(task_type)
                    data = _prompt_object(prompt, ('body', 'review_issues'))
                    inner.edit_inputs.append(data)
                    return json.dumps({'back_to_plan': False, 'edits': [
                        {'quote': data['body'], 'replacement': repaired}]})
                return super().generate(prompt, task_type)
        llm = CombinedLLM(initial)
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(llm.tasks.count('story_edit'), 1)
        self.assertEqual(llm.tasks.count('repair_story_length'), 0)
        self.assertEqual(run_control.load(project)['automatic_repairs'], 1)
        self.assertEqual({row['category'] for row in llm.edit_inputs[0]['review_issues']}, {'coherence', 'length'})
        self.assertTrue(report['length']['within_range'])
        self.assertEqual(report['length']['max'], 500)
        # A new workflow instance must not silently obtain another automatic edit.
        again = StoryWorkflow(project, llm, max_calls=100).generate()
        self.assertEqual(again['decision'], 'revise_required')
        self.assertIn('length_contract', again['blockers'])
        self.assertEqual(llm.tasks.count('story_edit'), 1)
        self.assertEqual(run_control.load(project)['automatic_repairs'], 1)

    def test_invalid_completed_edit_is_saved_and_not_semantically_retried(self):
        invalids = ({'back_to_plan': False}, {'back_to_plan': False, 'edits': [
            {'quote': '不存在的原文', 'replacement': '补造的正文'}]})
        for index, answer in enumerate(invalids):
            with self.subTest(answer=answer):
                project = self.project(str(index))
                body = '林禾递出收据，店主把信交给她。' * 18
                class InvalidEditLLM(ScriptedLLM):
                    def generate(inner, prompt, task_type):
                        if task_type == 'chief_editor_review':
                            inner.tasks.append(task_type)
                            row = _prompt_object(prompt, ('paragraphs',))['paragraphs'][0]
                            return json.dumps({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
                                'severity': 'blocker', 'evidence_ids': [row['id']], 'scope': 'local',
                                'reason': '传输控制：须核对原文动作。', 'fix_intent': '修订已指出动作。'}]})
                        if task_type == 'story_edit':
                            inner.tasks.append(task_type)
                            return json.dumps(answer)
                        return super().generate(prompt, task_type)
                llm = InvalidEditLLM(body)
                flow = StoryWorkflow(project, llm, max_calls=100)
                flow.develop()
                with self.assertRaisesRegex(StoryError, '原回答已保留'):
                    flow.generate()
                self.assertEqual(llm.tasks.count('story_edit'), 1)
                self.assertEqual(project.active_draft()[1].strip(), body)
                report = current_review(project, require_passed=False)
                cached = read_json(next((project.root / 'edit_responses').glob('*.json')))
                self.assertEqual(cached['response'], answer)
                self.assertEqual(cached['response_hash'], digest(answer))
                with self.assertRaisesRegex(StoryError, '原回答已保留'):
                    StoryWorkflow(project, llm)._edit(project.active_draft()[1], issues=report['issues'])
                self.assertEqual(llm.tasks.count('story_edit'), 1)
                self.assertEqual(run_control.load(project)['format_repairs'], 0)
                delivered = read_json(project.root / 'review_copy/稿件状态.json')
                self.assertEqual(delivered['issues'], report['issues'])
                self.assertIn('原回答已保留', delivered['error'])

    def approved_project(self):
        project = self.project(format='short_story', opening_mode='lead_then_chapter',
                               length={'min': 200, 'target': 300, 'max': 1000})
        original = lead.assemble('作者确认：她拿着旧借条，等我亲口答复。', '林禾把信还给了父亲。' * 20)
        record = project.save_draft(original)
        approved = lead.approve_lead(project.active_draft()[1], project.contract())
        write_json(project.root / 'approved_lead.json', approved)
        return project, record, approved

    def test_editor_replan_response_preserves_reason_and_returns_current_report(self):
        project = self.project()
        body = '林禾递出收据，店主把信交给她。' * 18
        class ReplanLLM(ScriptedLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'chief_editor_review':
                    inner.tasks.append(task_type)
                    row = _prompt_object(prompt, ('paragraphs',))['paragraphs'][0]
                    return json.dumps({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
                        'severity': 'blocker', 'evidence_ids': [row['id']], 'scope': 'local',
                        'reason': '传输控制：取信动作有因果问题。', 'fix_intent': '修复取信动作。'}]})
                if task_type == 'story_edit':
                    inner.tasks.append(task_type)
                    return json.dumps({'back_to_plan': True,
                                       'reason': '跨段取信机制需要重新规划', 'edits': []})
                return super().generate(prompt, task_type)
        llm = ReplanLLM(body)
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(project.active_draft()[0]['state'], 'needs_replan')
        self.assertEqual(project.active_draft()[1].strip(), body)
        request = read_json(project.root / 'structure_repair_request.json')
        self.assertEqual(request['content_hash'], digest(project.active_draft()[1]))
        self.assertEqual(request['review_id'], report['review_id'])
        self.assertIn('跨段取信机制需要重新规划', request['reason'])
        self.assertEqual(llm.tasks.count('story_edit'), 1)
        self.assertEqual(run_control.load(project)['automatic_repairs'], 1)
        delivered = read_json(project.root / 'review_copy/稿件状态.json')
        self.assertEqual(delivered['state'], 'needs_replan')
        self.assertEqual(delivered['issues'], report['issues'])
        self.assertIn('跨段取信机制需要重新规划', delivered['error'])

    def test_regeneration_keeps_confirmed_lead_before_switching_active_draft(self):
        project, _, approved = self.approved_project()
        class ConfirmedRegenerationLLM(ScriptedLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'write_story_scene':
                    inner.tasks.append(task_type)
                    # This short contract now uses the default scene path too.
                    # Keep each transport response within its small whole budget.
                    return '林禾核对署名后拆开信，决定亲自回去解释。' * 5
                return super().generate(prompt, task_type)
        llm = ConfirmedRegenerationLLM('不会使用的整篇稿件')
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        save = project.save_draft
        def checked_save(body, **kwargs):
            self.assertEqual(lead.split(body)[0], approved['lead_text'])
            return save(body, **kwargs)
        with patch.object(project, 'save_draft', side_effect=checked_save):
            report = flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(lead.split(project.active_draft()[1])[0], approved['lead_text'])
        self.assertNotIn('lead_draft', llm.tasks)
        self.assertTrue(lead.validate_approved_lead(project.active_draft()[1], project.contract(),
                                                  approved)['needs_fact_review'])

    def test_failed_regeneration_or_invalid_approval_cannot_switch_active_draft(self):
        project, record, approved = self.approved_project()
        original = project.active_draft()[1]
        llm = ScriptedLLM('新的正文。')
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        with patch.object(flow, '_draft_text', return_value=''):
            with self.assertRaises(StoryError):
                flow.generate()
        self.assertEqual(project.active_draft()[0]['content_hash'], record['content_hash'])
        self.assertEqual(project.active_draft()[1], original)
        approved['lead_text'] = '被外层改掉的导语'
        write_json(project.root / 'approved_lead.json', approved)
        count = len(llm.tasks)
        with self.assertRaisesRegex(StoryError, '确认记录无效'):
            StoryWorkflow(project, llm).generate()
        self.assertEqual(len(llm.tasks), count)
        self.assertEqual(project.active_draft()[1], original)


if __name__ == '__main__':
    unittest.main()
