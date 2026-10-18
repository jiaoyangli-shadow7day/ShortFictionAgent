"""Reader priority in actual planning/editing control flow.

These explicit scripted verdicts test routing, receipts and cache invalidation.
They do not establish that a model can correctly classify prose or attract readers.
"""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief
from short_fiction_agent.story_studio import editorial_priority as priority, workflow
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from tests.narrative_fixtures import narrative_response
from tests.test_story_editorial_priority import BODY, READY, issue
from tests.test_story_scene_purpose_budget import example_plan, review_control


def plan_control():
    plan = example_plan()
    plan['chapters'] = [{'chapter_id': f'chapter-{index + 1}', 'title': f'第{index + 1}章',
                         'scene_ids': [scene['scene_id']]} for index, scene in enumerate(plan['scenes'])]
    plan.update(core_reading_experience='双方实际作出同行的选择。',
                premise_test='取消约会后的主动回应改变双方安排。')
    for scene in plan['scenes']:
        scene['reading_role'] = 'support' if scene['scene_role'] == 'transition' else 'core_experience'
        scene['progression_beats'] = [{
            'reader_question': plan['main_question'], 'action': scene['choice'],
            'response': scene['consequence'],
            'change': scene['new_information'], 'reward': scene['consequence'],
            'next_expectation': plan['ending_resolution']}]
        if scene['scene_role'] == 'core':
            scene['dramatic_chain'] = {
                'attempt': scene['choice'], 'response': scene['consequence'],
                'adjustment': '双方按已经发生的回应改变下一步安排。',
                'change': scene['new_information'], 'next_effect': plan['ending_resolution'],
                'deletion_loss': '失去双方实际选择同行的回应。'}
    return plan


def plan_issue(*, detail=True):
    return {'path': 'plan.scenes[1].purpose',
        'reason': ('签字授权主体和两百万到账尾数未交代完整。' if detail else
                   '版权、两百万到账已经交代清楚，连续报账仍拖延双方关系回应。'),
        'missing_link': ('可在初始校对时用一句话提醒，不扩写手续。' if detail else
                         '合并重复报账，让当前选择得到人物回应。'),
        'editorial_priority': 'copyedit_detail' if detail else 'reading_core',
        'reading_effect': ('不影响读者理解交易结果或核心期待。' if detail else
                           '读者等待的关系回应被已知信息反复拖延。')}


class EditorialPriorityWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = StoryProject(Path(temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({'format': 'short_story',
            'channels': ['generic_text'], 'length': {'min': 1, 'target': 4000, 'max': None},
            'author_input': {'premise': '取消约会后，两人重新选择同行。'}}))
        # No remote provider is available to these controls. Every attempted
        # generation must cross the explicitly patched call_json below.
        self.flow = StoryWorkflow(self.project, object(), max_calls=20)
        self.plan = plan_control()
        self.contract = self.project.contract()

    def preflight_response(self, *, detail=True, legacy=False):
        response = review_control(self.plan)
        finding = plan_issue(detail=detail)
        if legacy:
            finding.pop('editorial_priority')
            finding.pop('reading_effect')
        response.update(decision='revise', issues=[finding])
        return response

    def test_detail_only_plan_does_not_replan_and_raw_cache_stays_adverse(self):
        raw = self.preflight_response()
        before = copy.deepcopy(raw)
        with patch.object(self.flow, 'call_json', return_value=raw) as call:
            accepted = self.flow._preflight_plan_chief(self.plan, self.contract)
            self.assertEqual(accepted, self.plan)
            self.assertEqual([row.args[0] for row in call.call_args_list], ['chief_plan_review'])
            # Replaying the identical completed input must not call any model.
            self.assertEqual(self.flow._preflight_plan_chief(self.plan, self.contract), self.plan)
            self.assertEqual(call.call_count, 1)
        assessment = read_json(self.project.root / 'plan/chief-preflight.json')['assessment']
        self.assertEqual(assessment['decision'], 'pass')
        self.assertEqual(assessment['model_decision'], 'revise')
        self.assertEqual(assessment['issues'], [])
        self.assertEqual(assessment['initial_copyedit_reminders'], raw['issues'])
        records = list((self.project.root / 'plan/chief_preflight').glob('*.json'))
        self.assertEqual(len(records), 1)
        cached = read_json(records[0])
        self.assertEqual(cached['response'], before)
        self.assertEqual(cached['response_hash'], digest(before))
        self.assertEqual(raw, before)

    def test_same_vocabulary_core_plan_issue_still_blocks_without_repair_permission(self):
        raw = self.preflight_response(detail=False)
        with patch.object(self.flow, 'call_json', return_value=raw) as call:
            with self.assertRaisesRegex(StoryError, '仍有核心问题'):
                self.flow._preflight_plan_chief(self.plan, self.contract, allow_repair=False)
        self.assertEqual(call.call_count, 1)
        assessment = read_json(self.project.root / 'plan/chief-preflight.json')['assessment']
        self.assertEqual(assessment['decision'], 'revise')
        self.assertEqual(assessment['issues'], raw['issues'])

    def test_core_plan_issue_receives_one_real_repair_and_reaudit(self):
        first = self.preflight_response(detail=False)
        repaired = copy.deepcopy(self.plan)
        repaired['scenes'][1]['purpose'] = '删去重复核对，直接让邀请得到回应。'
        captured = []

        def respond(task, prompt):
            captured.append(task)
            if task == 'chief_plan_review':
                return copy.deepcopy(first if captured.count(task) == 1 else review_control(repaired))
            self.assertEqual(task, 'story_plan_repair')
            self.assertIn(first['issues'][0]['reason'], prompt)
            self.assertIn('reading_core', prompt)
            return copy.deepcopy(repaired)

        with patch.object(self.flow, 'call_json', side_effect=respond):
            accepted = self.flow._preflight_plan_chief(self.plan, self.contract)
        self.assertEqual(accepted, repaired)
        self.assertEqual(captured, ['chief_plan_review', 'story_plan_repair', 'chief_plan_review'])
        self.assertEqual(read_json(self.project.root / 'plan/chief-preflight.json')
                         ['assessment']['decision'], 'pass')

    def test_old_policy_rejection_cache_cannot_be_reused_under_new_policy(self):
        old_raw = self.preflight_response(legacy=True)
        # Historical receipt control: the old policy classified every finding
        # as mandatory. It has no semantic priority fields to reinterpret.
        with patch.object(workflow, 'CHIEF_PLAN_PROTOCOL', 'chief-plan-review-v4-strict-generated-plan'):
            with patch.object(self.flow, 'call_json', return_value=old_raw) as old_call:
                with self.assertRaisesRegex(StoryError, '仍有核心问题'):
                    self.flow._preflight_plan_chief(self.plan, self.contract, allow_repair=False)
            self.assertEqual(old_call.call_count, 1)
        old_path = next((self.project.root / 'plan/chief_preflight').glob('*.json'))
        old_record = read_json(old_path)
        with patch.object(self.flow, 'call_json', return_value=self.preflight_response()) as new_call:
            self.assertEqual(self.flow._preflight_plan_chief(self.plan, self.contract,
                             allow_repair=False), self.plan)
        self.assertEqual(new_call.call_count, 1)
        self.assertEqual(read_json(old_path), old_record)
        records = [read_json(path) for path in
                   (self.project.root / 'plan/chief_preflight').glob('*.json')]
        self.assertEqual(len(records), 2)
        self.assertEqual({record['binding']['protocol'] for record in records},
                         {old_record['binding']['protocol'], workflow.CHIEF_PLAN_PROTOCOL})
        self.assertNotEqual(records[0]['input_hash'], records[1]['input_hash'])

    def test_detail_only_replan_request_is_reminded_once_without_edit_or_resolution_loop(self):
        details = [{**issue(), 'origin': origin, 'repair_scope': 'replan'}
                   for origin in ('chief_editor', 'independent_full_read')]
        with patch.object(self.flow, 'call_json', side_effect=AssertionError(
                'Optional detail cannot trigger a repair or resolution model')) as call:
            self.assertEqual(self.flow._edit(BODY, issues=details), BODY)
            self.assertEqual(self.flow._edit(BODY, issues=details), BODY)
        call.assert_not_called()
        self.assertFalse((self.project.root / 'structure_repair_request.json').exists())
        self.assertFalse((self.project.root / 'independent_revision_checks').exists())
        paths = list((self.project.root / 'initial_copyedit_reminders').glob('*.json'))
        self.assertEqual(len(paths), 1)
        record = read_json(paths[0])
        self.assertEqual(record['findings'], details)
        self.assertEqual(record['source_hash'], digest(BODY))

    def test_secondary_reminder_cannot_mask_mixed_core_replan_request(self):
        detail = {**issue(), 'origin': 'chief_editor', 'repair_scope': 'replan'}
        core = {**issue(detail=False), 'origin': 'chief_editor', 'repair_scope': 'replan'}
        with patch.object(self.flow, 'call_json') as call:
            with self.assertRaisesRegex(StoryError, '结构需退回规划层'):
                self.flow._edit(BODY, issues=[detail, core])
        call.assert_not_called()
        request = read_json(self.project.root / 'structure_repair_request.json')
        self.assertEqual(request['issues'], [core])


class EditorialPriorityChiefSchemaTests(unittest.TestCase):
    def test_raw_ready_blocker_and_revise_suggestion_detail_are_valid_then_project_ready(self):
        for recommendation, severity in (('ready', 'blocker'), ('revise', 'suggestion')):
            with self.subTest(recommendation=recommendation, severity=severity):
                raw = narrative_response(chief.prompt(BODY, {}), {
                    **copy.deepcopy(READY), 'recommendation': recommendation,
                    'issues': [{**issue(), 'severity': severity}]})
                before = copy.deepcopy(raw)
                normalized = chief.validate_response(raw, BODY)
                self.assertEqual(normalized['recommendation'], recommendation)
                self.assertEqual(normalized['issues'][0]['severity'], severity)
                final = priority.final_gate(normalized, BODY)
                self.assertEqual(final['recommendation'], 'ready')
                self.assertEqual(final['issues'], [])
                self.assertEqual(final['initial_copyedit_reminders'][0]['severity'], severity)
                self.assertEqual(raw, before)

    def test_raw_ready_with_core_blocker_still_is_a_schema_contradiction(self):
        raw = narrative_response(chief.prompt(BODY, {}), {
            **copy.deepcopy(READY), 'issues': [issue(detail=False)]})
        with self.assertRaisesRegex(StoryError, '有阻断问题却建议ready'):
            chief.validate_response(raw, BODY)


if __name__ == '__main__':
    unittest.main()
