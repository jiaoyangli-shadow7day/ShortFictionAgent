"""Strict generated-plan entrance controls; no literary quality claims."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import workflow
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, _prompt_object


class GeneratedPlanLLM(ScriptedLLM):
    max_prompt_tokens = 200000

    def __init__(self, invalid=None, invalid_once=False):
        super().__init__('协议控制正文。')
        self.invalid, self.invalid_once = invalid, invalid_once
        self.plan_responses = []
        self.chief_inputs = []
        self.planner_inputs = []
        self.planner_prompts = []

    def generate(self, prompt, task_type):
        if task_type == 'story_plan':
            if 'executable_schema' in prompt:
                self.planner_inputs.append(_prompt_object(prompt, ('contract', 'candidate', 'executable_schema')))
                self.planner_prompts.append(prompt)
            plan = json.loads(super().generate(prompt, task_type))
            if self.invalid and (not self.invalid_once or not self.plan_responses):
                if self.invalid in ('core_reading_experience', 'premise_test', 'promises'):
                    plan.pop(self.invalid)
                elif self.invalid in ('empty_core_reading_experience', 'empty_premise_test'):
                    plan[self.invalid.removeprefix('empty_')] = '  '
                elif self.invalid == 'incomplete_dramatic_chain':
                    plan['scenes'][0]['dramatic_chain'].pop('response')
                elif self.invalid == 'root_deletion_loss':
                    first = plan['scenes'][0]
                    first['deletion_loss'] = first['dramatic_chain'].pop('deletion_loss')
                elif self.invalid == 'missing_beat_response':
                    plan['scenes'][0]['progression_beats'][0].pop('response')
                elif self.invalid == 'empty_beat_response':
                    plan['scenes'][0]['progression_beats'][0]['response'] = '  '
                elif self.invalid == 'missing_reading_points':
                    plan['chapters'][0].pop('reading_points')
                elif self.invalid == 'empty_reading_points':
                    plan['chapters'][0]['reading_points'] = []
                else:
                    plan['scenes'][0].pop(self.invalid)
            self.plan_responses.append(copy.deepcopy(plan))
            return json.dumps(plan, ensure_ascii=False)
        if task_type == 'chief_plan_review':
            self.chief_inputs.append(_prompt_object(prompt, ('contract', 'plan')))
        return super().generate(prompt, task_type)


class GeneratedPlanSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def setup_flow(self, name='story', *, channels=None, guide_id=None, **options):
        project = StoryProject(Path(self.temp.name) / name)
        project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': channels or ['generic_text'],
            'length': {'min': 1, 'target': 6000, 'max': None},
            'publication_contract': {'submission_guide_id': guide_id} if guide_id else {},
            'author_input': {'premise': '林禾找回一封信'}}))
        llm = GeneratedPlanLLM(**options)
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        return project, llm, flow

    def test_fresh_missing_declared_fields_exhaust_only_existing_two_structure_responses(self):
        cases = ('core_reading_experience', 'premise_test', 'promises',
            'empty_core_reading_experience', 'empty_premise_test',
            'scene_role', 'reading_role', 'dramatic_chain', 'incomplete_dramatic_chain',
            'missing_beat_response', 'empty_beat_response')
        for invalid in cases:
            with self.subTest(invalid=invalid):
                project, llm, flow = self.setup_flow(invalid, invalid=invalid)
                with self.assertRaisesRegex(StoryError, '场景方案结构仍无效'):
                    flow.plan()
                self.assertEqual(llm.tasks.count('story_plan'), 2)
                self.assertNotIn('chief_plan_review', llm.tasks)
                self.assertNotIn('story_plan_repair', llm.tasks)
                self.assertNotIn('json_format_repair', llm.tasks)
                self.assertEqual(read_json(project.root / 'plan/raw-1.json'), llm.plan_responses[0])
                self.assertEqual(read_json(project.root / 'plan/raw-2.json'), llm.plan_responses[1])
                self.assertFalse((project.root / 'plan/preflight_receipt.json').exists())

    def test_valid_fresh_response_reaches_real_chief_without_old_value_fill(self):
        project, llm, flow = self.setup_flow(invalid='core_reading_experience', invalid_once=True)
        plan = flow.plan()
        self.assertEqual(llm.tasks.count('story_plan'), 2)
        self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
        self.assertNotIn('core_reading_experience', llm.plan_responses[0])
        self.assertEqual(llm.chief_inputs[0]['plan'], workflow.scene_planning.review_view(llm.plan_responses[1]))
        self.assertEqual(llm.chief_inputs[0]['contract'], project.contract())
        self.assertEqual(plan['core_reading_experience'], llm.plan_responses[1]['core_reading_experience'])
        self.assertEqual(read_json(project.root / 'plan/raw-1.json'), llm.plan_responses[0])

    def test_fresh_planner_receives_canonical_nested_schema_and_real_contract(self):
        project, llm, flow = self.setup_flow()
        plan = flow.plan()
        self.assertEqual(len(llm.planner_inputs), 1)
        data = llm.planner_inputs[0]
        self.assertEqual(data['contract'], project.contract())
        self.assertEqual(data['executable_schema'], workflow.CHIEF_PLAN_EXECUTABLE_SCHEMA)
        shape = data['executable_schema']['core_scene_nested_shape']
        self.assertNotIn('deletion_loss', shape)
        self.assertEqual(set(shape['dramatic_chain']), {
            'attempt', 'response', 'adjustment', 'change', 'next_effect', 'deletion_loss'})
        self.assertIn('response', data['executable_schema']['progression_beats_when_enabled'])
        self.assertIn('chapter_reading_points_when_selected_guide_applies', data['executable_schema'])
        self.assertIn(workflow.scene_planning.PLAN_INSTRUCTION, llm.planner_prompts[0])
        self.assertEqual(llm.chief_inputs[0]['plan']['scenes'], workflow.scene_planning.review_view(plan)['scenes'])
        self.assertEqual(llm.tasks.count('story_plan'), 1)
        self.assertEqual(llm.tasks.count('chief_plan_review'), 1)

    def test_root_deletion_loss_is_rejected_unchanged_and_retry_gets_exact_path(self):
        project, llm, flow = self.setup_flow(invalid='root_deletion_loss')
        with self.assertRaisesRegex(StoryError, r'scenes\[0\]\.dramatic_chain\.deletion_loss'):
            flow.plan()
        self.assertEqual(llm.tasks.count('story_plan'), 2)
        self.assertNotIn('chief_plan_review', llm.tasks)
        self.assertNotIn('json_format_repair', llm.tasks)
        self.assertIn('scenes[0].dramatic_chain.deletion_loss', llm.planner_prompts[1])
        for index, response in enumerate(llm.plan_responses, start=1):
            self.assertIn('deletion_loss', response['scenes'][0])
            self.assertNotIn('deletion_loss', response['scenes'][0]['dramatic_chain'])
            self.assertEqual(read_json(project.root / f'plan/raw-{index}.json'), response)

    def test_pending_missing_fields_cannot_enter_chief_legacy_fallback(self):
        for invalid in ('core_reading_experience', 'promises', 'dramatic_chain', 'missing_beat_response'):
            with self.subTest(invalid=invalid):
                project, llm, flow = self.setup_flow(invalid, invalid=invalid)
                contract = project.contract()
                candidate, opening = flow._selection()
                pending_plan = json.loads(llm.generate('"format": "short_story"', 'story_plan'))
                llm.tasks.clear()
                llm.plan_responses.clear()
                write_json(project.root / 'plan/pending-plan.json', {
                    'planning_hash': flow._short_planning_hash(contract, candidate, opening),
                    'plan': pending_plan, 'plan_hash': digest(pending_plan), 'status': 'pending_review'})
                with self.assertRaisesRegex(StoryError, '场景方案结构仍无效'):
                    StoryWorkflow(project, llm, max_calls=100).plan()
                self.assertEqual(llm.tasks.count('story_plan'), 1)
                self.assertNotIn('chief_plan_review', llm.tasks)
                self.assertEqual(read_json(project.root / 'plan/raw-1.json'), pending_plan)
                self.assertFalse((project.root / 'plan/preflight_receipt.json').exists())

    def test_valid_pending_enters_chief_without_regeneration_and_old_evidence_is_retained(self):
        project, llm, flow = self.setup_flow()
        contract = project.contract()
        candidate, opening = flow._selection()
        pending_plan = json.loads(llm.generate('"format": "short_story"', 'story_plan'))
        llm.tasks.clear()
        llm.plan_responses.clear()
        old_evidence = {'completed_failed_response': '保留原始失败证据。'}
        write_json(project.root / 'plan/raw-1.json', old_evidence)
        old_bytes = (project.root / 'plan/raw-1.json').read_bytes()
        write_json(project.root / 'plan/pending-plan.json', {
            'planning_hash': flow._short_planning_hash(contract, candidate, opening),
            'plan': pending_plan, 'plan_hash': digest(pending_plan), 'status': 'pending_review'})
        StoryWorkflow(project, llm, max_calls=100).plan()
        self.assertNotIn('story_plan', llm.tasks)
        self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
        self.assertEqual(llm.chief_inputs[0]['plan'], workflow.scene_planning.review_view(pending_plan))
        self.assertEqual((project.root / 'plan/raw-1.json').read_bytes(), old_bytes)
        new_records = list((project.root / 'plan').glob('raw-1-*.json'))
        self.assertEqual(len(new_records), 1)
        self.assertEqual(read_json(new_records[0]), pending_plan)

    def test_current_valid_legacy_cache_remains_compatible_and_new_planning_hash_changes(self):
        project, llm, flow = self.setup_flow()
        plan = flow.plan()
        for field in ('core_reading_experience', 'premise_test'):
            plan.pop(field)
        for scene in plan['scenes']:
            for field in ('scene_role', 'reading_role', 'dramatic_chain'):
                scene.pop(field)
            for beat in scene['progression_beats']:
                beat.pop('response')
        for chapter in plan['chapters']:
            chapter.pop('reading_points')
        write_json(project.root / 'plan/scenes.json', plan)
        write_json(project.root / 'plan/preflight_receipt.json', {
            'planning_hash': plan['planning_hash'], 'plan_hash': digest(plan), 'decision': 'pass'})
        calls = len(llm.tasks)
        self.assertEqual(StoryWorkflow(project, llm, max_calls=100).plan(), plan)
        self.assertEqual(len(llm.tasks), calls)
        candidate, opening = flow._selection()
        current = flow._short_planning_hash(project.contract(), candidate, opening)
        with patch.object(workflow, 'SHORT_PLAN_PROTOCOL', 'short-plan-preflight-v10-full-repair-schemas-guide'):
            self.assertNotEqual(flow._short_planning_hash(project.contract(), candidate, opening), current)
        with patch.object(workflow, 'SHORT_PLAN_PROTOCOL', 'short-plan-preflight-v11-strict-generated-schema'):
            self.assertNotEqual(flow._short_planning_hash(project.contract(), candidate, opening), current)

    def test_guide_outline_points_missing_or_empty_cannot_reach_chief_and_raw_is_unchanged(self):
        for guide_name, options in (
                ('default', {'channels': ['fanqie_short']}),
                ('explicit', {'channels': ['fanqie_short'], 'guide_id': 'fanqie_editor_dami'})):
            for invalid in ('missing_reading_points', 'empty_reading_points'):
                with self.subTest(guide=guide_name, invalid=invalid):
                    project, llm, flow = self.setup_flow(guide_name + invalid, invalid=invalid, **options)
                    with self.assertRaisesRegex(StoryError, r'chapters\[0\]\.reading_points'):
                        flow.plan()
                    self.assertEqual(llm.tasks.count('story_plan'), 2)
                    self.assertNotIn('chief_plan_review', llm.tasks)
                    self.assertNotIn('story_plan_repair', llm.tasks)
                    for index, response in enumerate(llm.plan_responses, start=1):
                        self.assertEqual(read_json(project.root / f'plan/raw-{index}.json'), response)
                    self.assertFalse((project.root / 'plan/preflight_receipt.json').exists())

    def test_unselected_generic_guide_does_not_impose_reading_points(self):
        project, llm, flow = self.setup_flow(invalid='missing_reading_points')
        plan = flow.plan()
        self.assertNotIn('reading_points', plan['chapters'][0])
        self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
        self.assertEqual(llm.tasks.count('story_plan'), 1)

    def test_generated_response_is_not_filled_from_existing_scene_chain_but_legacy_inspection_is_allowed(self):
        project, llm, flow = self.setup_flow()
        plan = json.loads(llm.generate('"format": "short_story"', 'story_plan'))
        plan['scenes'][0]['progression_beats'][0].pop('response')
        before = copy.deepcopy(plan)
        flow._validate_compact_plan(plan, project.contract())
        self.assertTrue(plan['scenes'][0]['dramatic_chain']['response'])
        with self.assertRaisesRegex(StoryError, r'progression_beats\[0\]\.response'):
            flow._validate_generated_chief_plan(plan, project.contract())
        self.assertEqual(plan, before)


if __name__ == '__main__':
    unittest.main()
