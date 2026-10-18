"""Production branches must retain current promises and applicable guide inputs.

The scripted text exercises transport, not literary quality.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import scene_planning, submission_guide, lead, whole_story, workflow
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow, normalize_scene_plan
from short_fiction_agent.story_studio.engagement import PLAN_INSTRUCTION as PROGRESSION_PLAN_INSTRUCTION
from tests.test_story_scene_purpose_budget import example_plan, review_control
from tests.narrative_fixtures import generated_scene_plan_control
from test_story_studio import _prompt_object


class SubmissionHandoffTests(unittest.TestCase):
    def project(self, target, review_mode='chief_editor'):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        project = StoryProject(Path(folder.name) / 'story')
        project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['fanqie_short'],
            'review_mode': review_mode,
            'length': {'min': target - 1000, 'target': target, 'max': target + 3000},
            'author_input': {'premise': '取消过约会的他，这次愿意主动留下吗？'}}))
        return project

    def progression_plan(self):
        plan = copy.deepcopy(example_plan())
        for scene in plan['scenes']:
            scene['progression_beats'] = [{
                'reader_question': plan['main_question'], 'action': scene['choice'],
                'response': scene['consequence'],
                'change': scene['consequence'], 'reward': scene['new_information'],
                'next_expectation': '她会主动邀请他一起出门吗？'}]
        return generated_scene_plan_control(plan)

    def assert_repair_guide(self, prompt, contract):
        self.assertIn(PROGRESSION_PLAN_INSTRUCTION, prompt)
        self.assertIn(scene_planning.PLAN_INSTRUCTION, prompt)
        self.assertIn(whole_story.PLAN_INSTRUCTION, prompt)
        payload = _prompt_object(prompt, ('submission_guide',))
        expected = submission_guide.submission_guide_context(contract, 'revision')
        self.assertEqual(payload['submission_guide'], expected)
        self.assertIn(submission_guide.submission_guide_guidance(contract, 'revision'), prompt)
        return payload

    def test_missing_main_question_stops_before_any_prose_call(self):
        project = self.project(12000)
        flow = StoryWorkflow(project, None)
        plan = example_plan()
        plan.pop('main_question')
        opening = {'text': '她把票递回去。'}
        plan['opening_hash'] = digest(opening)
        with patch.object(flow, '_selection', return_value=({'id': 'selected'}, opening)), \
                patch.object(flow, 'call_text') as model, \
                patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False):
            with self.assertRaisesRegex(StoryError, '缺少明确main_question'):
                flow._draft_text(plan)
        model.assert_not_called()

    def test_whole_and_long_scene_paths_receive_same_current_promise_and_guide(self):
        for target in (12000, 24000):
            with self.subTest(target=target):
                # These are the retained deep-review whole/scenes branches.
                # Default chapter routing has separate source/guide controls.
                project = self.project(target, review_mode='deep_review')
                flow = StoryWorkflow(project, None)
                plan = example_plan()
                plan['premise_test'] = '去掉昨天取消约会，今天递票与改签就失去试探作用。'
                opening = {'text': '她把多带的票藏进了口袋。'}
                plan['opening_hash'] = digest(opening)
                calls = []
                def generate(task, prompt):
                    payload = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                    calls.append((task, payload))
                    count = target if task == 'write_short_story' else payload['target_visible_chars']
                    return '字' * count
                with patch.object(flow, '_selection', return_value=({'id': 'selected',
                        'read_question': '旧候选的问题不能覆盖修订后的方案'}, opening)), \
                        patch.object(flow, 'call_text', side_effect=generate), \
                        patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False):
                    flow._draft_text(plan)
                self.assertEqual(calls[0][0], 'write_short_story' if target == 12000 else 'write_story_scene')
                for _, payload in calls:
                    self.assertEqual(payload['main_question'], plan['main_question'])
                    self.assertEqual(payload['premise_test'], plan['premise_test'])
                    self.assertEqual(payload['whole_story_orientation']['main_question'], plan['main_question'])
                    self.assertEqual(payload['submission_guide'],
                                     submission_guide.submission_guide_context(project.contract(), 'draft'))
                    self.assertIn('主线', payload['submission_guide_instruction'])
                    self.assertNotIn('read_question', payload['candidate'])
                if target == 12000:
                    allocation = calls[0][1]['scene_allocations']
                    self.assertEqual(sum(row['target'] for row in allocation),
                                     lead.story_length(project.contract())['target'])
                    self.assertLess(allocation[0]['target'], allocation[1]['target'])

    def test_legacy_explicit_question_normalizes_without_inventing_one(self):
        plan = example_plan()
        plan['story_question'] = plan.pop('main_question')
        before = copy.deepcopy(plan)
        normalized = normalize_scene_plan({'plan': plan})
        self.assertEqual(normalized['main_question'], before['story_question'])
        self.assertEqual(plan, before)
        del plan['story_question']
        self.assertNotIn('main_question', normalize_scene_plan(plan))

    def test_legacy_structure_replan_receives_guide_and_required_question_schema(self):
        project = self.project(4000)
        flow = StoryWorkflow(project, None)
        plan = example_plan()
        captured = []
        def replan(task, prompt):
            captured.append((task, prompt))
            return copy.deepcopy(plan)
        # Control the independent judgment to enter the real replan branch;
        # no literary conclusion is asserted by this transport test.
        with patch.object(flow, '_selection', return_value=({'id': 'selected'}, {'text': ''})), \
                patch.object(flow, '_call_validated_json', return_value={'claims': []}), \
                patch.object(flow, 'call_json', side_effect=replan), \
                patch.object(flow, '_preflight_plan', side_effect=lambda p, c, **kw: p), \
                patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False), \
                patch('short_fiction_agent.story_studio.workflow.whole_story.gate', side_effect=[['test-control'], []]), \
                patch('short_fiction_agent.story_studio.workflow.whole_story.issues', return_value=[]), \
                patch('short_fiction_agent.story_studio.workflow.whole_story.review_summary', return_value={}):
            flow._preflight_story_structure(plan, project.contract())
        self.assertEqual([task for task, _ in captured], ['whole_story_replan'])
        prompt = captured[0][1]
        self.assertIn(scene_planning.PLAN_INSTRUCTION, prompt)
        context = submission_guide.submission_guide_context(project.contract(), 'revision')
        self.assertIn(context['guide_hash'], prompt)
        self.assertTrue(all(ident in prompt for ident in context['semantic_requirement_ids']))

    def test_main_question_review_cannot_claim_pass_without_actual_first_and_last_scenes(self):
        plan = example_plan()
        response = review_control(plan)
        response['main_question_check']['opening_scene_id'] = 'invented'
        with self.assertRaisesRegex(StoryError, '期待核验'):
            scene_planning.validate_review(response, plan)
        response = review_control(plan)
        response['main_question_check']['status'] = 'needs_replan'
        with self.assertRaisesRegex(StoryError, '退回规划'):
            scene_planning.validate_review(response, plan)

    def test_chief_plan_repair_keeps_required_progression_schema_and_guide_binding(self):
        project = self.project(6000)
        flow = StoryWorkflow(project, None)
        plan = self.progression_plan()
        rejected = review_control(scene_planning.review_view(plan), project.contract())
        rejected['decision'] = 'revise'
        rejected['issues'] = [{'path': 'plan.scenes[1].choice', 'reason': '控制样例的因果缺环',
                               'missing_link': '须根据前一场结果选择'}]
        revised = copy.deepcopy(plan)
        revised['scenes'][1]['choice'] += '先问清替代方案，再作出选择。'
        revised.update(core_reading_experience='协议控制：双方当面回应并选择同行。',
                       premise_test='协议控制：昨天失约使本次主动留下成为实际选择。')
        for scene in revised['scenes']:
            scene['reading_role'] = 'support' if scene['scene_role'] == 'transition' else 'core_experience'
            if scene['scene_role'] == 'core':
                scene['dramatic_chain'] = {field: '协议控制：明确行动、回应及改变。' for field in
                    ('attempt', 'response', 'adjustment', 'change', 'next_effect', 'deletion_loss')}
        captured = []
        def call(task, prompt):
            if task == 'story_plan_repair':
                captured.append(prompt)
                return copy.deepcopy(revised)
            return copy.deepcopy(rejected if not captured else review_control(
                scene_planning.review_view(revised), project.contract()))
        with patch.object(flow, '_validate_compact_plan'), patch.object(flow, 'call_json', side_effect=call):
            flow._preflight_plan_chief(plan, project.contract())
        self.assertEqual(len(captured), 1)
        prompt = captured[0]
        self.assertIn(PROGRESSION_PLAN_INSTRUCTION, prompt)
        self.assertIn(scene_planning.PLAN_INSTRUCTION, prompt)
        context = submission_guide.submission_guide_context(project.contract(), 'revision')
        self.assertIn(context['guide_hash'], prompt)
        self.assertTrue(all(ident in prompt for ident in context['semantic_requirement_ids']))

    def test_legacy_structural_repair_receives_complete_schema_and_guide(self):
        project = self.project(4000)
        flow = StoryWorkflow(project, None)
        contract = project.contract()
        contract['review_mode'] = 'deep_review'
        plan = self.progression_plan()
        revised = copy.deepcopy(plan)
        revised['main_question'] = '他愿意当面改签，留下陪她走完这次约会吗？'
        revised['scenes'][1]['choice'] = '她把票递回去，问他愿不愿意当面改签'
        captured = []
        def call(task, prompt):
            captured.append((task, prompt))
            if task == 'story_plan_structural_repair':
                data = self.assert_repair_guide(prompt, contract)
                self.assertEqual(data['plan'], plan)
                return copy.deepcopy(revised)
            if task == 'blueprint_audit' and len(captured) == 1:
                # main_question is an actual plan field outside the field-patch
                # allowlist, so this enters the full structural repair branch.
                return {'decision': 'revise', 'issues': [{'path': 'plan.main_question',
                    'reason': '协议控制：具体期待需与当前选择对齐',
                    'missing_link': '根据实际递票与改签明确问题'}]}
            if task in {'blueprint_audit', 'plan_reconciliation_review'}:
                return {'decision': 'pass', 'issues': []}
            self.fail(task)
        with patch.object(flow, 'call_json', side_effect=call):
            result = flow._preflight_plan(plan, contract, _structure=False,
                                         revision_context={'source': 'protocol-control'})
        self.assertEqual(result, revised)
        self.assertEqual([task for task, _ in captured], ['blueprint_audit',
            'story_plan_structural_repair', 'blueprint_audit', 'plan_reconciliation_review'])
        self.assertEqual(read_json(project.root / 'plan' / 'revision-working.json')['review_status'], 'pending')

    def test_budget_replan_receives_complete_schema_and_bound_guide(self):
        project = self.project(4000)
        flow = StoryWorkflow(project, None)
        contract = project.contract()
        candidate, opening = {'id': 'selected'}, {'text': '她拿出两张票。'}
        plan = self.progression_plan()
        revised = copy.deepcopy(plan)
        revised['scenes'][1]['choice'] = '她把票递回去，问他愿不愿意当面改签'
        write_json(project.root / 'plan' / 'scenes.json', plan)
        with patch.object(flow, '_selection', return_value=(candidate, opening)):
            flow._save_budget_replan_request(plan, contract, plan['scenes'][1], '旧版实际部分稿。',
                                            StoryError('缺少人物实际接招'))
        request = read_json(project.root / 'structure_repair_request.json')
        tasks = []
        def call(task, prompt):
            tasks.append(task)
            if task == 'story_plan_repair':
                data = self.assert_repair_guide(prompt, contract)
                self.assertEqual(data['executable_schema'], workflow.CHIEF_PLAN_EXECUTABLE_SCHEMA)
                self.assertIn('response', data['executable_schema']['progression_beats_when_enabled'])
                self.assertIn('chapter_reading_points_when_selected_guide_applies', data['executable_schema'])
                self.assertEqual(data['source_plan'], plan)
                self.assertEqual(data['partial_diagnostic_source'], '旧版实际部分稿。')
                return json.dumps(revised, ensure_ascii=False)
            if task == 'chief_plan_review':
                return json.dumps(review_control(scene_planning.review_view(revised), contract), ensure_ascii=False)
            self.fail(task)
        with patch.object(flow, 'call_text', side_effect=call):
            result = flow._consume_budget_replan(request, plan, contract, candidate=candidate,
                                                 opening=opening, planning_hash='current-planning')
        self.assertEqual(result['scenes'], revised['scenes'])
        self.assertEqual(tasks, ['story_plan_repair', 'chief_plan_review'])
        self.assertEqual(read_json(project.root / 'structure_repair_request.json')['status'], 'resolved')

    def test_pending_budget_replan_cannot_reuse_obsolete_guide_criteria(self):
        project = self.project(4000)
        flow = StoryWorkflow(project, None)
        contract = project.contract()
        candidate, opening = {'id': 'selected'}, {'text': '她拿出两张票。'}
        plan = self.progression_plan()
        with patch.object(flow, '_selection', return_value=(candidate, opening)):
            flow._save_budget_replan_request(plan, contract, plan['scenes'][1], '已保存部分稿。',
                                            StoryError('缺少人物实际接招'))
        request = read_json(project.root / 'structure_repair_request.json')
        arguments = {'candidate': candidate, 'opening': opening, 'planning_hash': 'current-planning'}
        with patch.object(flow, 'call_text', side_effect=StoryError('result pending')):
            with self.assertRaisesRegex(StoryError, 'result pending'):
                flow._consume_budget_replan(request, plan, contract, **arguments)
        changed = copy.deepcopy(submission_guide._guides())
        changed['profiles']['fanqie_editor_dami']['version'] = 'future-guide'
        with patch.object(submission_guide, '_guides', return_value=changed), \
                patch.object(flow, 'call_text') as model:
            with self.assertRaisesRegex(StoryError, '审核标准绑定变化'):
                flow._consume_budget_replan(request, plan, contract, **arguments)
        model.assert_not_called()
        self.assertEqual(read_json(project.root / 'plan' / 'budget_replans' / 'state.json')['attempts'], 1)


if __name__ == '__main__':
    unittest.main()
