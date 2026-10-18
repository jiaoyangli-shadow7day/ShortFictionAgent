"""Budget failures retain sources and permit exactly one semantic replan.

Synthetic replies exercise transport, coverage and routing, not prose quality.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import BUDGET_REPLAN_PROTOCOL, StoryWorkflow
from tests.test_story_scene_purpose_budget import example_plan, review_control
from tests.narrative_fixtures import generated_scene_plan_control
from test_story_studio import ScriptedLLM, _prompt_object


class BudgetReplanResumeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = StoryProject(Path(self.temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
            'length': {'min': 3500, 'target': 4000, 'max': 5000},
            'author_input': {'premise': '她想确认昨天取消约会的他是否仍愿意同行'}}))
        self.flow = StoryWorkflow(self.project, None, whole_draft_limit=0)
        self.candidate, self.opening = {'id': 'chosen'}, {'text': '她拿出两张票。'}
        self.original = generated_scene_plan_control(example_plan())
        self.original.update(candidate_hash=digest(self.candidate), opening_hash=digest(self.opening),
                             contract_hash=digest(self.project.contract()), planning_hash='old-planning')
        write_json(self.project.root / 'plan' / 'scenes.json', self.original)
        self.selection = patch.object(self.flow, '_selection', return_value=(self.candidate, self.opening))
        self.selection.start()
        self.addCleanup(self.selection.stop)
        self.engagement = patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False)
        self.engagement.start()
        self.addCleanup(self.engagement.stop)
        self.chapter_reviews = ScriptedLLM('协议控制正文。')

    def fail_budget(self, plan=None, partial='旧稿实际动作。'):
        plan = plan or self.original
        self.flow._save_budget_replan_request(plan, self.project.contract(), plan['scenes'][-1], partial,
            StoryError('结构需退回规划层：缺失必要互动'))
        return read_json(self.project.root / 'structure_repair_request.json')

    def revised(self):
        result = copy.deepcopy(self.original)
        result['scenes'][1]['choice'] = '她把票递回去，问他愿不愿意当面改签'
        result['scenes'][1]['budget'] = 3000
        return result

    def responder(self, task, prompt):
        if task == 'chief_editor_chapter_reader':
            return self.chapter_reviews.generate(prompt, task)
        data = _prompt_object(prompt, ('contract',))
        if task == 'story_plan_repair':
            self.assertEqual(data['contract'], self.project.contract())
            self.assertEqual(data['source_plan'], self.original)
            self.assertIn('仅作诊断', data['partial_source_use'])
            return json.dumps(self.revised(), ensure_ascii=False)
        if task == 'chief_plan_review':
            return json.dumps(review_control(data['plan']), ensure_ascii=False)
        if task == 'write_story_scene':
            self.assertNotIn('旧稿实际动作', data['previous_text'])
            return '字' * data['target_visible_chars']
        self.fail(task)

    def test_plan_consumes_bound_request_once_and_new_draft_starts_from_scratch(self):
        before_contract = self.project.contract()
        request = self.fail_budget()
        with patch.object(self.flow, 'call_text', side_effect=self.responder) as model:
            revised = self.flow.plan()
            self.assertNotEqual(digest(revised), digest(self.original))
            self.assertEqual(self.project.contract(), before_contract)
            self.assertEqual(self.flow.plan(), revised)
            self.flow._draft_text(revised)
        self.assertEqual([item.args[0] for item in model.call_args_list],
                         ['story_plan_repair', 'chief_plan_review']
                         + ['write_story_scene', 'chief_editor_chapter_reader'] * 3)
        saved = read_json(self.project.root / 'structure_repair_request.json')
        self.assertEqual(saved['status'], 'resolved')
        self.assertEqual(saved['request_hash'], request['request_hash'])
        self.assertTrue(Path(request['partial_body_path']).is_file())
        self.assertEqual(read_json(self.project.root / 'plan' / 'budget_replans' / 'state.json')['attempts'], 1)

    def test_second_budget_failure_is_saved_but_cannot_restart_semantic_repair(self):
        first = self.fail_budget()
        with patch.object(self.flow, 'call_text', side_effect=self.responder):
            revised = self.flow.plan()
        second = self.fail_budget(revised, '第二版仍缺必要戏。')
        with patch.object(self.flow, 'call_text') as model:
            with self.assertRaisesRegex(StoryError, '已用尽一次'):
                self.flow.plan()
        model.assert_not_called()
        self.assertNotEqual(first['partial_body_path'], second['partial_body_path'])
        self.assertTrue(Path(first['partial_body_path']).is_file())
        self.assertTrue(Path(second['partial_body_path']).is_file())
        self.assertEqual(read_json(self.project.root / 'structure_repair_request.json')['repair_status'], 'exhausted')

    def test_unknown_generation_resumes_same_single_attempt(self):
        self.fail_budget()
        with patch.object(self.flow, 'call_text', side_effect=StoryError('CLI result pending')):
            with self.assertRaisesRegex(StoryError, 'pending'):
                self.flow.plan()
        state = read_json(self.project.root / 'plan' / 'budget_replans' / 'state.json')
        self.assertEqual(state['status'], 'pending')
        with patch.object(self.flow, 'call_text', side_effect=self.responder):
            self.flow.plan()
        self.assertEqual(read_json(self.project.root / 'plan' / 'budget_replans' / 'state.json')['attempts'], 1)

    def test_unknown_chief_review_reuses_completed_replan_output(self):
        self.fail_budget()
        def pending(task, prompt):
            if task == 'chief_plan_review':
                raise StoryError('CLI chief result pending')
            return self.responder(task, prompt)
        with patch.object(self.flow, 'call_text', side_effect=pending):
            with self.assertRaisesRegex(StoryError, 'chief result pending'):
                self.flow.plan()
        restarted = StoryWorkflow(self.project, None, whole_draft_limit=0)
        with patch.object(restarted, '_selection', return_value=(self.candidate, self.opening)), \
                patch.object(restarted, 'call_text', side_effect=self.responder) as model:
            restarted.plan()
        self.assertEqual([item.args[0] for item in model.call_args_list], ['chief_plan_review'])

    def test_completed_rejection_exhausts_without_another_repair_or_editor(self):
        self.fail_budget()
        def reject(task, prompt):
            response = self.responder(task, prompt)
            if task == 'chief_plan_review':
                data = json.loads(response)
                data['decision'] = 'revise'
                data['scene_checks'][1]['status'] = 'needs_replan'
                data['issues'] = [{'path': 'scenes[1].choice', 'reason': '协议否决控制',
                                   'missing_link': '缺少实际回应'}]
                return json.dumps(data)
            return response
        with patch.object(self.flow, 'call_text', side_effect=reject) as model:
            with self.assertRaisesRegex(StoryError, '写前因果预审'):
                self.flow.plan()
        self.assertEqual([item.args[0] for item in model.call_args_list],
                         ['story_plan_repair', 'chief_plan_review'])
        with patch.object(self.flow, 'call_text') as model:
            with self.assertRaisesRegex(StoryError, '已用尽一次'):
                self.flow.plan()
        model.assert_not_called()

    def test_request_cannot_be_closed_without_reviewed_revised_plan(self):
        request = self.fail_budget()
        request['status'] = 'resolved'
        write_json(self.project.root / 'structure_repair_request.json', request)
        with patch.object(self.flow, 'call_text') as model:
            with self.assertRaisesRegex(StoryError, '关闭记录'):
                self.flow.plan()
        model.assert_not_called()

    def test_closed_repair_remains_history_when_later_guidance_invalidates_plan(self):
        self.fail_budget()
        with patch.object(self.flow, 'call_text', side_effect=self.responder):
            self.flow.plan()
        with patch('short_fiction_agent.story_studio.workflow.guidance_fingerprint', return_value='new-guidance'), \
                patch.object(self.flow, 'call_json', return_value=generated_scene_plan_control(self.revised())) as planner, \
                patch.object(self.flow, '_preflight_plan', side_effect=lambda plan, contract: plan):
            revised = self.flow.plan()
            self.assertEqual(self.flow.plan(), revised)
        planner.assert_called_once()
        self.assertEqual(read_json(self.project.root / 'structure_repair_request.json')['status'], 'superseded')

    def test_completed_invalid_chief_reply_exhausts_without_resending(self):
        self.fail_budget()
        def invalid(task, prompt):
            return 'completed invalid JSON' if task == 'chief_plan_review' else self.responder(task, prompt)
        with patch.object(self.flow, 'call_text', side_effect=invalid):
            with self.assertRaises(StoryError):
                self.flow.plan()
        with patch.object(self.flow, 'call_text') as model:
            with self.assertRaisesRegex(StoryError, '已用尽一次'):
                self.flow.plan()
        model.assert_not_called()

    def test_metadata_only_plan_change_is_not_a_budget_repair(self):
        self.fail_budget()
        metadata_only = copy.deepcopy(self.original)
        metadata_only.update(repair_version=2, premise_test='新版本只改说明',
                             main_question='修改承诺的说法仍不能代替预算所需的实际互动重规划')
        for scene in metadata_only['scenes']:
            scene['response_metadata'] = 'completed'
        with patch.object(self.flow, 'call_text', return_value=json.dumps(metadata_only)) as model:
            with self.assertRaisesRegex(StoryError, '没有改变被否决'):
                self.flow.plan()
        self.assertEqual(model.call_count, 1)
        self.assertEqual(read_json(self.project.root / 'plan' / 'budget_replans' / 'state.json')['status'], 'exhausted')

    def test_budget_repair_rejection_during_generate_cannot_silently_switch_candidate(self):
        self.fail_budget()
        write_json(self.project.root / 'selection.json', {'auto_selected': True, 'candidate_index': 0})
        with patch.object(self.flow, '_consume_budget_replan', side_effect=StoryError('写前因果预审仍有核心问题')), \
                patch.object(self.flow, '_feasible_alternate', return_value=1) as alternative, \
                patch.object(self.flow, 'develop') as development:
            with self.assertRaisesRegex(StoryError, '写前因果预审'):
                self.flow.generate()
        alternative.assert_not_called()
        development.assert_not_called()

    def test_request_partial_and_plan_tampering_fail_closed(self):
        for kind in ('request', 'partial', 'plan'):
            with self.subTest(kind=kind):
                request = self.fail_budget()
                if kind == 'request':
                    request['reason'] = 'other source'
                    write_json(self.project.root / 'structure_repair_request.json', request)
                elif kind == 'partial':
                    Path(request['partial_body_path']).write_text('other draft', encoding='utf-8')
                else:
                    write_json(self.project.root / 'plan' / 'scenes.json', self.revised())
                with patch.object(self.flow, 'call_text') as model:
                    with self.assertRaisesRegex(StoryError, '绑定损坏|哈希不一致'):
                        self.flow.plan()
                model.assert_not_called()
                write_json(self.project.root / 'plan' / 'scenes.json', self.original)

    def test_finite_max_allocation_failure_also_saves_partial_request(self):
        def oversized(task, prompt):
            if task == 'chief_editor_chapter_reader':
                return self.chapter_reviews.generate(prompt, task)
            self.assertEqual(task, 'write_story_scene')
            return '字' * 6000
        with patch.object(self.flow, 'call_text', side_effect=oversized) as model:
            with self.assertRaisesRegex(StoryError, '剩余篇幅'):
                self.flow._draft_text(self.original)
        self.assertEqual([call.args[0] for call in model.call_args_list],
                         ['write_story_scene', 'chief_editor_chapter_reader'])
        request = read_json(self.project.root / 'structure_repair_request.json')
        self.assertEqual(request['protocol'], BUDGET_REPLAN_PROTOCOL)
        self.assertEqual(request['review_status'], 'pending')
        self.assertEqual(request['partial_content_hash'], digest(Path(request['partial_body_path']).read_text()))
        self.assertEqual(request['plan_hash'], digest(self.original))

    def test_narrative_replan_cannot_be_sent_to_local_edit(self):
        issue = {'origin': 'narrative_audit', 'repair_scope': 'replan', 'reason': '中段没有实际接招'}
        with patch.object(self.flow, 'call_json') as model:
            with self.assertRaisesRegex(StoryError, '退回规划层'):
                self.flow._edit('正文。', issues=[issue])
        model.assert_not_called()

    def test_narrative_replan_can_enter_reselection(self):
        record = self.project.save_draft('当前正文。')
        report = {'content_hash': record['content_hash'], 'decision': 'needs_replan', 'review_id': 'r',
            'issues': [{'origin': 'narrative_audit', 'repair_scope': 'replan', 'reason': '核心戏被概述',
                        'quote': '当前正文。', 'fix_intent': '重新策划实际互动'}]}
        with patch('short_fiction_agent.story_studio.workflow.current_review', return_value=report), \
                patch.object(self.flow, 'develop', return_value={'reselected': True}) as development:
            self.assertEqual(self.flow.reselect_after_rejected_draft(), {'reselected': True})
        development.assert_called_once_with(refresh=True)
        self.assertEqual(read_json(self.project.root / 'reselection_context.json')['source_content_hash'],
                         record['content_hash'])

    def test_actual_writer_prompt_keeps_prior_research_alongside_new_expression_guidance(self):
        from short_fiction_agent.story_studio.research_guidance import guidance_fingerprint
        class CapturingModel(ScriptedLLM):
            max_prompt_tokens = 200000
            def __init__(self):
                super().__init__('协议控制正文。')
                self.prompts = []
            def generate(self, prompt, task_type):
                if task_type == 'chief_editor_chapter_reader':
                    return super().generate(prompt, task_type)
                assert task_type == 'write_story_scene'
                self.prompts.append(prompt)
                data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                return '字' * data['target_visible_chars']
        model = CapturingModel()
        self.flow.llm = model
        self.flow._draft_text(self.original)
        self.assertEqual(len(model.prompts), 3)
        for prompt in model.prompts:
            self.assertIn('narrative_expression', prompt)
            self.assertIn(guidance_fingerprint(), prompt)
            for key in ('continuous_pull', 'whole_story_research', 'dramatic_realization_research',
                        'prose_expression_research', 'manuscript_lead_research', 'screenshot_opening_research'):
                self.assertIn('"' + key + '"', prompt)
