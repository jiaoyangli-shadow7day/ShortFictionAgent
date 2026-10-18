"""Executable repair contract and source binding; not a literary benchmark."""
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


class PlanRepairLLM(ScriptedLLM):
    max_prompt_tokens = 200000

    def __init__(self, invalid=None, reject_again=False):
        super().__init__('协议控制正文。')
        self.invalid, self.reject_again = invalid, reject_again
        self.repaired = False
        self.source_plan = self.assessment = self.repair_payload = self.repair_response = None

    def generate(self, prompt, task_type):
        if task_type == 'chief_plan_review':
            response = json.loads(super().generate(prompt, task_type))
            data = _prompt_object(prompt, ('contract', 'plan'))
            if not self.repaired or self.reject_again:
                response.update(decision='revise', issues=[{
                    'path': 'plan.scenes[0].purpose', 'reason': '协议负控制：连续两个任务可合并。',
                    'missing_link': '合并同目标准备，保留实际选择与回应。'}])
                allocation = response['reading_allocation_check']
                allocation['status'] = 'needs_replan'
                groups = allocation['expansion_choices']
                merged = copy.deepcopy(groups[0])
                merged.update(scene_ids=[s['scene_id'] for s in data['plan']['scenes'][:2]],
                    planned_budget=sum(s['budget'] for s in data['plan']['scenes'][:2]), action='merge')
                merged['compression_trial']['retained_beats'] += groups[1]['compression_trial']['retained_beats']
                allocation['expansion_choices'] = [merged] + groups[2:]
                response['chapter_checks'][0].update(status='needs_replan',
                    reason='协议负控制：本章与后章重复同一交锋功能，须合并后重新分章。')
                self.assessment = copy.deepcopy(response)
            return json.dumps(response, ensure_ascii=False)
        if task_type == 'story_plan_repair':
            self.tasks.append(task_type)
            data = _prompt_object(prompt, ('contract', 'plan', 'assessment', 'executable_schema'))
            # The independent review receives an executable projection; the
            # concentrated repair must still receive the full source plan.
            self.source_plan = copy.deepcopy(data['plan'])
            self.repair_payload = copy.deepcopy(data)
            self.repair_prompt = prompt
            revised = copy.deepcopy(data['plan'])
            first = revised['scenes'][0]
            old_ids = {s['scene_id'] for s in revised['scenes'][:2]}
            first.update(scene_id='joined', purpose='协议控制：合并同目标准备并接续选择。',
                budget=sum(s['budget'] for s in revised['scenes'][:2]))
            revised['scenes'] = [first] + revised['scenes'][2:]
            revised['chapters'] = [{'chapter_id': f'chapter-{index + 1}',
                'title': f'第{index + 1}章', 'scene_ids': [scene['scene_id']],
                'reading_points': [
                    {'kind': 'hook', 'scene_id': scene['scene_id'], 'beat_index': 0,
                     'note': '传输控制：绑定本章第一节拍的待答问题，不证明吸引力。'},
                    {'kind': 'emotion', 'scene_id': scene['scene_id'], 'beat_index': 0,
                     'note': '传输控制：绑定本章第一节拍的实际回应，不证明文学质量。'}]}
                for index, scene in enumerate(revised['scenes'])]
            for scene in revised['scenes']:
                scene.update(scene_role='core', reading_role='core_experience')
                scene['dependencies'] = ['joined' if key in old_ids else key
                    for key in scene['dependencies'] if key != scene['scene_id']]
                scene['dramatic_chain'] = {field: '协议控制：当前选择与回应。' for field in
                    ('attempt', 'response', 'adjustment', 'change', 'next_effect', 'deletion_loss')}
            for promise in revised['promises']:
                if promise['payoff_scene'] in old_ids:
                    promise['payoff_scene'] = 'joined'
            revised.update(core_reading_experience='协议控制：亲历取信的选择与回应。',
                premise_test='协议控制：不同交换条件改变选择。')
            if self.invalid == 'purpose':
                for scene in revised['scenes']:
                    scene.pop('purpose')
            elif self.invalid == 'ending':
                revised['ending'] = revised.pop('ending_resolution')
            elif self.invalid == 'promises':
                revised['promise_payoff_map'] = revised.pop('promises')
            elif self.invalid in ('core_reading_experience', 'premise_test'):
                revised.pop(self.invalid)
            elif self.invalid in ('empty_core_reading_experience', 'empty_premise_test'):
                revised[self.invalid.removeprefix('empty_')] = '  '
            elif self.invalid in ('scene_role', 'reading_role', 'dramatic_chain'):
                revised['scenes'][0].pop(self.invalid)
            elif self.invalid == 'incomplete_dramatic_chain':
                revised['scenes'][0]['dramatic_chain'].pop('response')
            elif self.invalid == 'missing_beat_response':
                revised['scenes'][0]['progression_beats'][0].pop('response')
            elif self.invalid == 'empty_beat_response':
                revised['scenes'][0]['progression_beats'][0]['response'] = ''
            elif self.invalid == 'missing_reading_points':
                revised['chapters'][0].pop('reading_points')
            self.repair_response = copy.deepcopy(revised)
            self.repaired = True
            return json.dumps(revised, ensure_ascii=False)
        return super().generate(prompt, task_type)


class ChiefPlanRepairSchemaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def setup_flow(self, name='story', *, channels=None, **options):
        project = StoryProject(Path(self.temp.name) / name)
        project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': channels or ['generic_text'],
            'length': {'min': 1, 'target': 6000, 'max': None},
            'author_input': {'premise': '林禾找回一封信'}}))
        llm = PlanRepairLLM(**options)
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        return project, llm, flow

    def test_actual_plan_repair_receives_schema_and_whole_assessment_then_merges_and_reaudits(self):
        project, llm, flow = self.setup_flow()
        plan = flow.plan()
        data = llm.repair_payload
        self.assertEqual(data['assessment'], llm.assessment)
        self.assertEqual(data['assessment']['chapter_checks'][0]['status'], 'needs_replan')
        self.assertIn('chapter_id', data['assessment']['chapter_checks'][0])
        allocation = data['assessment']['reading_allocation_check']
        self.assertEqual(allocation['expansion_choices'][0]['action'], 'merge')
        self.assertEqual(allocation, llm.assessment['reading_allocation_check'])
        self.assertEqual(set(data['executable_schema']['required_scene_fields']), set(workflow.COMPACT_SCENE_FIELDS))
        self.assertIn('ending_resolution', data['executable_schema']['required_plan_fields'])
        self.assertIn('promises', data['executable_schema']['required_plan_fields'])
        self.assertEqual(data['source_schema']['plan_fields'], sorted(llm.source_plan))
        self.assertEqual(data['source_schema']['scene_fields']['s0'], sorted(llm.source_plan['scenes'][0]))
        self.assertIn('ending不能替代ending_resolution', llm.repair_prompt)
        self.assertIn('promise_payoff_map不能替代promises', llm.repair_prompt)
        self.assertEqual(len(plan['scenes']), 3)
        self.assertEqual(plan['scenes'][0]['scene_id'], 'joined')
        self.assertEqual(plan['promises'][0]['payoff_scene'], 'joined')
        self.assertEqual(llm.tasks.count('story_plan_repair'), 1)
        self.assertEqual(llm.tasks.count('chief_plan_review'), 2)
        audit = read_json(project.root / 'plan/chief-preflight.json')
        self.assertEqual(audit['assessment']['decision'], 'pass')
        record = read_json(next((project.root / 'plan/chief_preflight').glob('repair-*.json')))
        self.assertEqual(record['binding']['protocol'], workflow.PLAN_REPAIR_PROTOCOL)
        self.assertEqual(record['binding']['assessment_hash'], digest(llm.assessment))
        self.assertEqual(record['binding']['schema_hash'], digest(workflow.CHIEF_PLAN_EXECUTABLE_SCHEMA))

    def test_completed_missing_fields_are_retained_and_not_filled_or_retried_on_resume(self):
        for invalid, message in (('purpose', '字段缺失'), ('ending', '结局回报'), ('promises', 'promises数组')):
            with self.subTest(invalid=invalid):
                project, llm, flow = self.setup_flow(invalid, invalid=invalid)
                with self.assertRaisesRegex(StoryError, message):
                    flow.plan()
                record_path = next((project.root / 'plan/chief_preflight').glob('repair-*.json'))
                record = read_json(record_path)
                self.assertEqual(record['response'], llm.repair_response)
                self.assertEqual(record['response_hash'], digest(llm.repair_response))
                calls = len(llm.tasks)
                with self.assertRaisesRegex(StoryError, message):
                    StoryWorkflow(project, llm, max_calls=100)._preflight_plan_chief(
                        llm.source_plan, project.contract())
                self.assertEqual(len(llm.tasks), calls)
                self.assertEqual(llm.tasks.count('story_plan_repair'), 1)
                self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
                self.assertNotIn('json_format_repair', llm.tasks)
                self.assertEqual(read_json(record_path), record)

    def test_second_adverse_preflight_cannot_add_another_repair(self):
        project, llm, flow = self.setup_flow(reject_again=True)
        with self.assertRaisesRegex(StoryError, '仍有核心问题'):
            flow.plan()
        self.assertEqual(llm.tasks.count('story_plan_repair'), 1)
        self.assertEqual(llm.tasks.count('chief_plan_review'), 2)
        self.assertEqual(read_json(project.root / 'plan/chief-preflight.json')['assessment']['decision'], 'revise')

    def test_repair_missing_new_outline_values_is_retained_without_inheritance_or_extra_calls(self):
        for invalid in ('missing_beat_response', 'empty_beat_response', 'missing_reading_points'):
            with self.subTest(invalid=invalid):
                project, llm, flow = self.setup_flow(invalid, channels=['fanqie_short'], invalid=invalid)
                message = 'reading_points' if invalid == 'missing_reading_points' else r'progression_beats\[0\]\.response'
                with self.assertRaisesRegex(StoryError, message):
                    flow.plan()
                path = next((project.root / 'plan/chief_preflight').glob('repair-*.json'))
                record = read_json(path)
                self.assertEqual(record['response'], llm.repair_response)
                calls = len(llm.tasks)
                with self.assertRaisesRegex(StoryError, message):
                    StoryWorkflow(project, llm, max_calls=100)._preflight_plan_chief(
                        llm.source_plan, project.contract())
                self.assertEqual(len(llm.tasks), calls)
                self.assertEqual(llm.tasks.count('story_plan_repair'), 1)
                self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
                self.assertNotIn('json_format_repair', llm.tasks)
                self.assertEqual(read_json(path), record)

    def test_new_schema_fields_cannot_use_legacy_fallback_or_get_another_model_attempt(self):
        cases = ('core_reading_experience', 'premise_test', 'empty_core_reading_experience',
            'empty_premise_test', 'scene_role', 'reading_role', 'dramatic_chain',
            'incomplete_dramatic_chain')
        for invalid in cases:
            with self.subTest(invalid=invalid):
                project, llm, flow = self.setup_flow(invalid, invalid=invalid)
                with self.assertRaisesRegex(StoryError, invalid.removeprefix('empty_').removeprefix('incomplete_')):
                    flow.plan()
                path = next((project.root / 'plan/chief_preflight').glob('repair-*.json'))
                record = read_json(path)
                self.assertEqual(record['response'], llm.repair_response)
                calls = len(llm.tasks)
                with self.assertRaises(StoryError):
                    StoryWorkflow(project, llm, max_calls=100)._preflight_plan_chief(
                        llm.source_plan, project.contract())
                self.assertEqual(len(llm.tasks), calls)
                self.assertEqual(llm.tasks.count('story_plan_repair'), 1)
                self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
                self.assertNotIn('json_format_repair', llm.tasks)
                self.assertEqual(read_json(path), record)

    def test_direct_legacy_plan_preflight_remains_compatible(self):
        project, _, flow = self.setup_flow()
        llm = ScriptedLLM('未调用的正文。')
        flow.llm = llm
        plan = flow.plan()
        for field in ('core_reading_experience', 'premise_test'):
            plan.pop(field, None)
        for scene in plan['scenes']:
            for field in ('scene_role', 'reading_role', 'dramatic_chain'):
                scene.pop(field, None)
        before = copy.deepcopy(plan)
        self.assertEqual(flow._preflight_plan_chief(plan, project.contract(), allow_repair=False), before)
        self.assertEqual(plan, before)
        self.assertNotIn('story_plan_repair', llm.tasks)

    def test_strict_repair_protocol_changes_criteria_from_v21(self):
        current = workflow.chief_plan_criteria_hash()
        with patch.object(workflow, 'PLAN_REPAIR_PROTOCOL', 'full-plan-repair-v4-executable-schema-and-assessment'):
            self.assertNotEqual(workflow.chief_plan_criteria_hash(), current)

    def test_repair_binding_tampering_fails_before_any_new_model_call(self):
        project, llm, flow = self.setup_flow(invalid='purpose')
        with self.assertRaises(StoryError):
            flow.plan()
        path = next((project.root / 'plan/chief_preflight').glob('repair-*.json'))
        record = read_json(path)
        record['binding']['assessment_hash'] = 'another-assessment'
        write_json(path, record)
        calls = len(llm.tasks)
        with self.assertRaisesRegex(StoryError, '缓存与原始回答不一致'):
            StoryWorkflow(project, llm, max_calls=100)._preflight_plan_chief(llm.source_plan, project.contract())
        self.assertEqual(len(llm.tasks), calls)

    def test_executable_schema_changes_invalidate_editorial_criteria(self):
        old = workflow.chief_plan_criteria_hash()
        schema = copy.deepcopy(workflow.CHIEF_PLAN_EXECUTABLE_SCHEMA)
        schema['required_scene_fields']['purpose'] = 'changed executable contract'
        with patch.object(workflow, 'CHIEF_PLAN_EXECUTABLE_SCHEMA', schema):
            self.assertNotEqual(workflow.chief_plan_criteria_hash(), old)


if __name__ == '__main__':
    unittest.main()
