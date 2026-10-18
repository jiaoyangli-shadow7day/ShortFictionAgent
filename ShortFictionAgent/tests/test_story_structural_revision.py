"""Actual workflow/receipt controls; scripted prose is not a quality benchmark."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import lead, run_control, structural_revision
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.distribution import valid_generation_lineage
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from short_fiction_agent.story_studio.workflow import CHIEF_PLAN_EXECUTABLE_SCHEMA
from test_story_studio import ScriptedLLM, _prompt_object
from tests.narrative_fixtures import narrative_response, generated_scene_plan_control


OLD = '旧稿：林禾听店主说信已经取回，却没有亲自作出交换。'
NEW = '新稿：林禾核对收据，决定交换自己的记忆；店主收下约定，把信交到她手里。'


class RevisionLLM(ScriptedLLM):
    def __init__(self):
        super().__init__(OLD)
        self.replanned = False
        self.fail_once = None
        self.invalid = None
        self.reject_again = False
        self.replan_inputs = []

    def generate(self, prompt, task_type):
        if self.fail_once == task_type:
            self.tasks.append(task_type)
            self.fail_once = None
            raise StoryError('transport outcome pending')
        if task_type == 'whole_story_replan':
            self.tasks.append(task_type)
            data = _prompt_object(prompt, ('source_plan', 'unresolved_core_issues'))
            self.replan_inputs.append(data)
            self.replanned = True
            if self.invalid == 'plan':
                return 'completed invalid plan'
            revised = copy.deepcopy(data['source_plan'])
            if self.invalid != 'unchanged':
                revised['scenes'][0]['choice'] = '林禾核对收据后当场选择交换自己的记忆'
                revised['scenes'][0]['consequence'] = '店主收下约定并将信交到林禾手里'
            if self.invalid != 'unchanged':
                revised = generated_scene_plan_control(revised)
            if self.invalid in ('core_reading_experience', 'premise_test', 'promises'):
                revised.pop(self.invalid)
            elif self.invalid in ('scene_role', 'reading_role', 'dramatic_chain'):
                revised['scenes'][0].pop(self.invalid)
            elif self.invalid == 'root_deletion_loss':
                first = revised['scenes'][0]
                first['deletion_loss'] = first['dramatic_chain'].pop('deletion_loss')
            return json.dumps(revised, ensure_ascii=False)
        if task_type == 'chief_plan_review' and self.replanned and self.invalid == 'preflight':
            self.tasks.append(task_type)
            return 'completed invalid editor response'
        if task_type == 'write_story_scene':
            # Short stories now use chapter/scene generation by default. Keep
            # both the rejection marker and repaired source on the actual path.
            self.tasks.append(task_type)
            return NEW if self.replanned else OLD
        if task_type in {'write_micro_story', 'write_short_story'} and self.replanned:
            self.tasks.append(task_type)
            return NEW
        if task_type == 'chief_editor_review':
            self.tasks.append(task_type)
            data = _prompt_object(prompt, ('paragraphs',))
            rows = data['paragraphs']
            if any('旧稿' in row['text'] for row in rows) or self.reject_again:
                row = next((r for r in rows if '旧稿' in r['text']), rows[-1])
                return json.dumps(narrative_response(prompt, {'recommendation': 'revise',
                    'claims': [], 'observations': [], 'issues': [{'severity': 'blocker',
                        'scope': 'global', 'evidence_ids': [row['id']],
                        'reason': '协议负控制：核心交换只被总结，必须重新规划实际接招。',
                        'fix_intent': '重排取信交锋，让林禾自己选择且店主实际回应。'}]}), ensure_ascii=False)
            return json.dumps(narrative_response(prompt), ensure_ascii=False)
        return super().generate(prompt, task_type)


class StructuralRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = StoryProject(Path(self.temp.name) / 'story')
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'channels': ['generic_text'], 'length': {'min': 1, 'target': 300, 'max': None},
            'author_input': {'premise': '林禾找回自己的信，交换只能使用亲历记忆。'}}))
        self.llm = RevisionLLM()
        self.flow = StoryWorkflow(self.project, self.llm, max_calls=100)
        self.flow.develop()

    def state(self):
        return read_json(self.project.root / 'structural_revisions' / 'state.json')

    def test_actual_default_pipeline_replans_writes_and_formally_reviews_once(self):
        contract = self.project.contract()
        result = self.flow.generate()
        self.assertEqual(result['decision'], 'passed')
        self.assertEqual(self.project.active_draft()[1].strip(), NEW)
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertEqual(self.llm.replan_inputs[0]['executable_schema'], CHIEF_PLAN_EXECUTABLE_SCHEMA)
        self.assertIn('deletion_loss', self.llm.replan_inputs[0]['executable_schema']
                      ['core_scene_nested_shape']['dramatic_chain'])
        self.assertEqual(self.llm.tasks.count('chief_plan_review'), 1)
        self.assertEqual(self.llm.tasks.count('chief_editor_review'), 2)
        self.assertNotIn('story_edit', self.llm.tasks)
        self.assertEqual(run_control.load(self.project)['automatic_repairs'], 1)
        self.assertEqual(self.state()['status'], 'passed')
        self.assertEqual(self.project.contract(), contract)
        self.assertIsNone(contract['length']['max'])
        self.assertTrue(valid_generation_lineage(self.project,
            read_json(self.project.root / 'generation_receipt.json')))
        self.assertEqual((Path(self.state()['directory']) / 'source-body.md').read_text().strip(), OLD)
        self.assertEqual(read_json(self.project.root / 'structure_repair_request.json')['status'], 'resolved')

    def test_no_auto_revise_keeps_original_and_no_replanning_call(self):
        result = self.flow.generate(auto_revise=False)
        self.assertEqual(result['decision'], 'revise_required')
        self.assertEqual(self.project.active_draft()[0]['state'], 'needs_replan')
        self.assertEqual(self.project.active_draft()[1].strip(), OLD)
        self.assertNotIn('whole_story_replan', self.llm.tasks)
        self.assertEqual(run_control.load(self.project)['automatic_repairs'], 0)

    def test_second_structural_rejection_is_terminal_across_processes(self):
        self.llm.reject_again = True
        result = self.flow.generate()
        self.assertEqual(result['decision'], 'revise_required')
        self.assertEqual(self.state()['status'], 'exhausted')
        self.assertEqual(self.project.active_draft()[0]['state'], 'needs_replan')
        calls = len(self.llm.tasks)
        restarted = StoryWorkflow(self.project, self.llm, max_calls=100)
        self.assertEqual(restarted.generate(), result)
        self.assertEqual(len(self.llm.tasks), calls)
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertFalse((self.project.root / 'generation_receipt.json').exists())

    def test_cold_cumulative_veto_alone_replans_and_resumes_pending_new_first_read(self):
        original = self.llm.generate
        pending = True
        def cold_only(prompt, task_type):
            nonlocal pending
            if task_type == 'chief_editor_review':
                self.llm.tasks.append(task_type)
                return json.dumps(narrative_response(prompt), ensure_ascii=False)
            if task_type == 'chief_editor_first_read':
                self.llm.tasks.append(task_type)
                if self.llm.replanned and pending:
                    pending = False
                    raise StoryError('new first reading pending')
                response = narrative_response(prompt)
                if not self.llm.replanned:
                    response['cumulative'].update(status='thin',
                        reason='协议负控制：连读只有交代交换结果，核心体验未展开。',
                        fix_intent='改写实际交换中的选择与接招。')
                return json.dumps(response, ensure_ascii=False)
            return original(prompt, task_type)
        self.llm.generate = cold_only
        with self.assertRaisesRegex(StoryError, 'new first reading pending'):
            self.flow.generate()
        self.assertEqual(self.state()['status'], 'review')
        self.assertEqual({issue['origin'] for issue in self.state()['issues']}, {'chief_first_read'})
        self.assertIn(NEW, self.project.active_draft()[1])
        self.assertFalse((self.project.root / 'generation_receipt.json').exists())
        report = StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertEqual(self.llm.tasks.count('chief_plan_review'), 1)

    def test_cold_cumulative_veto_can_reopen_selection_after_structural_exhaustion(self):
        original = self.llm.generate
        def cold_only(prompt, task_type):
            if task_type in {'chief_editor_review', 'chief_editor_first_read'}:
                self.llm.tasks.append(task_type)
                response = narrative_response(prompt)
                if task_type == 'chief_editor_first_read':
                    response['cumulative'].update(status='thin',
                        reason='协议负控制：全稿累计仍未实际展开核心交换。',
                        fix_intent='重新选择并规划交换的冲突机制。')
                return json.dumps(response, ensure_ascii=False)
            return original(prompt, task_type)
        self.llm.generate = cold_only
        report = self.flow.generate()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(self.state()['status'], 'exhausted')
        self.assertEqual({issue['origin'] for issue in report['issues']}, {'chief_first_read'})
        with patch.object(self.flow, 'develop', return_value={'reselected': True}) as develop:
            self.assertEqual(self.flow.reselect_after_rejected_draft(), {'reselected': True})
            develop.assert_called_once_with(refresh=True)
        self.assertEqual(self.state()['status'], 'superseded')
        self.assertEqual(self.state()['superseded_by']['action'], 'explicit_candidate_reselection')
        self.assertEqual(read_json(self.project.root / 'structure_repair_request.json')['status'], 'superseded')
        self.assertEqual(run_control.load(self.project)['automatic_repairs'], 1)
        self.assertIsNone(structural_revision.resume(self.flow, auto_revise=True,
            entry_point='StoryWorkflow.generate'))

    def test_completed_invalid_new_first_read_does_not_resend_or_fabricate_current_review(self):
        original = self.llm.generate
        def invalid_new_review(prompt, task_type):
            if task_type == 'chief_editor_first_read' and self.llm.replanned:
                self.llm.tasks.append(task_type)
                return json.dumps({'completed': 'invalid first reading'})
            return original(prompt, task_type)
        self.llm.generate = invalid_new_review
        with self.assertRaisesRegex(StoryError, '初读缺少'):
            self.flow.generate()
        body = self.project.active_draft()[1]
        calls = len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError, '初读缺少'):
            StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(len(self.llm.tasks), calls)
        result = StoryWorkflow(self.project, self.llm, max_calls=100).generate(auto_revise=False)
        self.assertEqual(result['content_hash'], digest(body))
        self.assertIsNone(result['review_id'])
        self.assertFalse((self.project.root / 'generation_receipt.json').exists())

    def test_completed_invalid_plan_or_preflight_does_not_get_another_attempt(self):
        for invalid in ('plan', 'preflight', 'unchanged'):
            with self.subTest(invalid=invalid):
                if invalid != 'plan':
                    self.setUp()
                self.llm.invalid = invalid
                result = self.flow.generate()
                self.assertEqual(result['decision'], 'revise_required')
                self.assertEqual(self.state()['status'], 'exhausted')
                self.assertEqual(self.project.active_draft()[1].strip(), OLD)
                calls = len(self.llm.tasks)
                StoryWorkflow(self.project, self.llm, max_calls=100).generate()
                self.assertEqual(len(self.llm.tasks), calls)
                self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
                self.assertNotIn('json_format_repair', self.llm.tasks)

    def test_unknown_preflight_resumes_completed_plan_without_regenerating_it(self):
        self.llm.fail_once = 'chief_plan_review'
        with self.assertRaisesRegex(StoryError, 'pending'):
            self.flow.generate()
        self.assertEqual(self.state()['status'], 'preflight')
        self.assertEqual(self.project.active_draft()[1].strip(), OLD)
        result = StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(result['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertEqual(run_control.load(self.project)['automatic_repairs'], 1)

    def test_generated_replan_missing_schema_is_terminal_before_preflight(self):
        for invalid in ('core_reading_experience', 'premise_test', 'promises',
                        'scene_role', 'reading_role', 'dramatic_chain'):
            with self.subTest(invalid=invalid):
                self.setUp()
                self.llm.invalid = invalid
                result = self.flow.generate()
                self.assertEqual(result['decision'], 'revise_required')
                self.assertEqual(self.state()['status'], 'exhausted')
                self.assertEqual(self.project.active_draft()[1].strip(), OLD)
                self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
                self.assertNotIn('chief_plan_review', self.llm.tasks)
                path = Path(self.state()['directory']) / 'plan-response.json'
                raw_bytes = path.read_bytes()
                record = read_json(path)
                self.assertEqual(record['raw_hash'], digest(record['raw_text']))
                calls = len(self.llm.tasks)
                StoryWorkflow(self.project, self.llm, max_calls=100).generate()
                self.assertEqual(len(self.llm.tasks), calls)
                self.assertEqual(path.read_bytes(), raw_bytes)

    def test_root_deletion_loss_in_structural_response_does_not_move_or_retry(self):
        self.llm.invalid = 'root_deletion_loss'
        result = self.flow.generate()
        self.assertEqual(result['decision'], 'revise_required')
        self.assertEqual(self.state()['status'], 'exhausted')
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertNotIn('chief_plan_review', self.llm.tasks)
        path = Path(self.state()['directory']) / 'plan-response.json'
        raw_bytes = path.read_bytes()
        response = json.loads(read_json(path)['raw_text'])
        self.assertIn('deletion_loss', response['scenes'][0])
        self.assertNotIn('deletion_loss', response['scenes'][0]['dramatic_chain'])
        calls = len(self.llm.tasks)
        StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(len(self.llm.tasks), calls)
        self.assertEqual(path.read_bytes(), raw_bytes)

    def test_unknown_rewrite_resumes_preflight_and_does_not_restore_source_plan(self):
        self.llm.fail_once = 'write_micro_story'
        # First writing belongs to initial generation; defer the failure until
        # the unique replan has actually completed.
        self.llm.fail_once = None
        original = self.llm.generate
        def fail_rewrite(prompt, task_type):
            if task_type == 'write_micro_story' and self.llm.replanned:
                self.llm.generate = original
                self.llm.tasks.append(task_type)
                raise StoryError('rewrite transport pending')
            return original(prompt, task_type)
        self.llm.generate = fail_rewrite
        with self.assertRaisesRegex(StoryError, 'rewrite transport pending'):
            self.flow.generate()
        state = self.state()
        self.assertEqual(state['status'], 'writing')
        revised_hash = digest(read_json(self.project.root / 'plan' / 'scenes.json'))
        result = StoryWorkflow(self.project, self.llm, max_calls=100).resume_generation()
        self.assertEqual(result['decision'], 'passed')
        self.assertEqual(digest(read_json(self.project.root / 'plan' / 'scenes.json')), revised_hash)
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)
        self.assertEqual(self.llm.tasks.count('chief_plan_review'), 1)

    def test_completed_prose_before_state_update_is_reused_after_process_crash(self):
        save = structural_revision._save
        def crash_after_completed_prose(flow, state):
            if state['status'] == 'lead':
                raise SystemExit('process stopped after completed prose')
            return save(flow, state)
        with patch.object(structural_revision, '_save', side_effect=crash_after_completed_prose):
            with self.assertRaises(SystemExit):
                self.flow.generate()
        state = self.state()
        self.assertEqual(state['status'], 'writing')
        self.assertTrue((Path(state['directory']) / 'rewritten-story.json').exists())
        writes = self.llm.tasks.count('write_micro_story')
        report = StoryWorkflow(self.project, self.llm, max_calls=100).resume_generation()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('write_micro_story'), writes)
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)

    def test_activated_body_before_generation_checkpoint_recovers_without_rewriting(self):
        checkpoint = self.flow._save_generation_checkpoint
        def crash_after_body_activation(record, source_receipt_hash):
            if self.llm.replanned:
                raise SystemExit('process stopped before generation checkpoint')
            return checkpoint(record, source_receipt_hash)
        with patch.object(self.flow, '_save_generation_checkpoint', side_effect=crash_after_body_activation):
            with self.assertRaises(SystemExit):
                self.flow.generate()
        self.assertEqual(self.state()['status'], 'review')
        self.assertIn(NEW, self.project.active_draft()[1])
        writes = self.llm.tasks.count('write_micro_story')
        report = StoryWorkflow(self.project, self.llm, max_calls=100).resume_generation()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('write_micro_story'), writes)
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)

    def test_pending_new_prose_never_returns_original_report_as_current(self):
        original = self.llm.generate
        def pending_review(prompt, task_type):
            if task_type == 'chief_editor_review' and self.llm.replanned:
                self.llm.generate = original
                self.llm.tasks.append(task_type)
                raise StoryError('formal review pending')
            return original(prompt, task_type)
        self.llm.generate = pending_review
        with self.assertRaisesRegex(StoryError, 'formal review pending'):
            self.flow.generate()
        self.assertEqual(self.state()['status'], 'review')
        body = self.project.active_draft()[1]
        self.assertIn(NEW, body)
        calls = len(self.llm.tasks)
        pending = StoryWorkflow(self.project, self.llm, max_calls=100).generate(auto_revise=False)
        self.assertEqual(pending['content_hash'], digest(body))
        self.assertIsNone(pending['review_id'])
        self.assertEqual(pending['review_status'], 'pending')
        self.assertNotEqual(pending['content_hash'], pending['structural_revision']['previous_content_hash'])
        self.assertEqual(len(self.llm.tasks), calls)
        request = read_json(self.project.root / 'structure_repair_request.json')
        self.assertIsNone(request['review_id'])
        self.assertEqual(request['content_hash'], digest(body))
        result = StoryWorkflow(self.project, self.llm, max_calls=100).resume_generation()
        self.assertEqual(result['decision'], 'passed')
        self.assertEqual(self.llm.tasks.count('whole_story_replan'), 1)

    def test_replaced_plan_cannot_resume_old_source_or_candidate(self):
        self.llm.fail_once = 'chief_plan_review'
        with self.assertRaises(StoryError):
            self.flow.generate()
        original = read_json(self.project.root / 'plan' / 'scenes.json')
        replaced = copy.deepcopy(original)
        replaced['scenes'][0]['choice'] = '无关候选中的另一种解法'
        write_json(self.project.root / 'plan' / 'scenes.json', replaced)
        calls = len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError, '其他规划替换'):
            StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(len(self.llm.tasks), calls)
        self.assertEqual(read_json(self.project.root / 'plan' / 'scenes.json'), replaced)

    def test_explicit_source_bound_author_replan_supersedes_without_resetting_allowance(self):
        project, flow, llm = self.lead_project(approved=False)
        original_generate = llm.generate
        def pending_preflight(prompt, task_type):
            if task_type == 'chief_plan_review' and llm.replanned:
                llm.generate = original_generate
                llm.tasks.append(task_type)
                raise StoryError('structural preflight pending')
            return original_generate(prompt, task_type)
        llm.generate = pending_preflight
        with self.assertRaisesRegex(StoryError, 'structural preflight pending'):
            flow.generate()
        state = read_json(project.root / 'structural_revisions/state.json')
        root = project.root / 'plan'
        source = state['source_plan']
        request = {'source': 'author', 'source_plan_hash': digest(source),
            'original_hash': state['binding']['source_content_hash'],
            'issues': ['作者明确要求在交换前先让林禾与店主直接交锋。']}
        request_id = digest(request)
        revised = copy.deepcopy(source)
        for key in ('candidate_hash', 'opening_hash', 'contract_hash', 'planning_hash'):
            revised.pop(key, None)
        revised['scenes'][0]['choice'] = '林禾依作者要求先向店主出示收据，再选择交换'
        flow._preflight_plan_chief(revised, project.contract(), allow_repair=False)
        audit = read_json(root / 'chief-preflight.json')
        revised.update({key: source[key] for key in (
            'candidate_hash', 'opening_hash', 'contract_hash', 'planning_hash') if key in source})
        revised['author_replan_request_id'] = request_id
        binding = {'protocol': 'source-bound-author-replan-v1',
            'contract_hash': state['binding']['contract_hash'],
            'candidate_hash': state['binding']['candidate_hash'],
            'opening_hash': state['binding']['opening_hash'],
            'selection_hash': state['binding']['selection_hash'],
            'original_hash': request['original_hash'], 'source_plan_hash': digest(source),
            'author_request_hash': request_id,
            'research_hash': state['binding']['research_hash'],
            'chief_plan_criteria_hash': state['binding']['criteria_hash']}
        directory = root / 'author_replans' / request_id
        write_json(directory / 'author-request.json', request)
        write_json(directory / 'source-plan.json', source)
        write_json(directory / 'raw-response.json', {'binding': binding,
            'response': revised, 'response_hash': digest(revised)})
        receipt = {'request_id': request_id, 'binding': binding,
            'plan_hash': digest(revised), 'actual_chief_preflight_hash': digest(audit),
            'model_response_hash': digest(revised)}
        write_json(root / 'author-replan-receipt.json', receipt)
        write_json(root / 'preflight_receipt.json', {'plan_hash': digest(revised),
            'planning_hash': revised['planning_hash'], 'decision': 'pass',
            'actual_chief_preflight_hash': digest(audit)})
        write_json(root / 'scenes.json', revised)
        calls = len(llm.tasks)
        # A loose receipt cannot retire the interrupted automatic rewrite.
        altered = {**receipt, 'actual_chief_preflight_hash': 'unrelated-audit'}
        write_json(root / 'author-replan-receipt.json', altered)
        with self.assertRaisesRegex(StoryError, '其他规划替换'):
            StoryWorkflow(project, llm, max_calls=100).generate()
        self.assertEqual(len(llm.tasks), calls)
        write_json(root / 'author-replan-receipt.json', receipt)
        result = StoryWorkflow(project, llm, max_calls=100).generate()
        self.assertEqual(result['decision'], 'passed')
        self.assertEqual(read_json(project.root / 'structural_revisions/state.json')['status'], 'superseded')
        self.assertEqual(read_json(project.root / 'structure_repair_request.json')['status'], 'superseded')
        self.assertEqual(read_json(root / 'scenes.json'), revised)
        self.assertEqual(llm.tasks.count('whole_story_replan'), 1)
        self.assertEqual(run_control.load(project)['automatic_repairs'], 1)

    def test_locked_fact_deletion_keeps_original_and_exhausts_rewrite(self):
        # Recreate with a literal fact that the old prose contains. As with the
        # existing local editor, deleting it cannot silently activate a draft.
        project = StoryProject(Path(self.temp.name) / 'locked')
        fact = '交换只能使用亲历记忆'
        project.create(StoryBrief.from_dict({'format': 'micro_story', 'channels': ['generic_text'],
            'length': {'min': 1, 'target': 300, 'max': None},
            'author_input': {'premise': '林禾找回信', 'locked_facts': [fact]}}))
        llm = RevisionLLM()
        llm.body = OLD + '\n' + fact
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        result = flow.generate()
        self.assertEqual(result['decision'], 'revise_required')
        self.assertIn(fact, project.active_draft()[1])
        self.assertEqual(project.active_draft()[0]['state'], 'needs_replan')
        self.assertEqual(read_json(project.root / 'structural_revisions/state.json')['status'], 'exhausted')
        self.assertFalse((project.root / 'generation_receipt.json').exists())

    def test_old_budget_request_is_archived_before_structure_request_replaces_it(self):
        prior = {'protocol': 'scene-budget-replan-v1', 'status': 'resolved',
                 'request_hash': 'closed-budget-history'}
        write_json(self.project.root / 'structure_repair_request.json', prior)
        self.flow.generate()
        saved = [read_json(path) for path in (self.project.root / 'structure_repair_history').glob('*.json')]
        self.assertIn(prior, saved)

    def test_state_or_source_tampering_fails_closed_before_calls(self):
        self.llm.fail_once = 'chief_plan_review'
        with self.assertRaises(StoryError):
            self.flow.generate()
        state = self.state()
        (Path(state['directory']) / 'source-body.md').write_text('替换原稿', encoding='utf-8')
        calls = len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError, '绑定损坏'):
            StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(len(self.llm.tasks), calls)

    def test_rewrite_budget_failure_does_not_launch_nested_budget_replan(self):
        original = self.flow._draft_text
        def budget_failure(plan):
            if self.llm.replanned:
                self.flow._save_budget_replan_request(plan, self.project.contract(), plan['scenes'][0],
                    '新稿部分正文', StoryError('结构需退回规划层：预算缺必要互动'))
                raise StoryError('结构需退回规划层：预算缺必要互动')
            return original(plan)
        with patch.object(self.flow, '_draft_text', side_effect=budget_failure):
            report = self.flow.generate()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(self.state()['status'], 'exhausted')
        self.assertTrue((Path(self.state()['directory']) / 'rewrite-budget-failure.json').exists())
        calls = len(self.llm.tasks)
        StoryWorkflow(self.project, self.llm, max_calls=100).generate()
        self.assertEqual(len(self.llm.tasks), calls)
        self.assertNotIn('story_plan_repair', self.llm.tasks)

    def lead_project(self, *, approved):
        project = StoryProject(Path(self.temp.name) / ('approved' if approved else 'lead'))
        project.create(StoryBrief.from_dict({'format': 'short_story',
            'opening_mode': 'lead_then_chapter', 'channels': ['generic_text'],
            'length': {'min': 1, 'target': 300, 'max': None},
            'author_input': {'premise': '林禾拿回自己的信'}}))
        if approved:
            project.save_draft(lead.assemble('我拿着旧收据，问店主愿不愿履行约定。', OLD))
            write_json(project.root / 'approved_lead.json',
                lead.approve_lead(project.active_draft()[1], project.contract()))
        llm = RevisionLLM()
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        return project, flow, llm

    def test_new_body_gets_new_independent_lead_but_confirmed_lead_is_preserved(self):
        for approved in (False, True):
            with self.subTest(approved=approved):
                project, flow, llm = self.lead_project(approved=approved)
                receipt = read_json(project.root / 'approved_lead.json')
                report = flow.generate()
                self.assertEqual(report['decision'], 'passed')
                self.assertEqual(llm.tasks.count('lead_draft'), 0 if approved else 2)
                self.assertIn(NEW, project.active_draft()[1])
                if approved:
                    self.assertEqual(read_json(project.root / 'approved_lead.json'), receipt)
                    self.assertEqual(lead.split(project.active_draft()[1])[0], receipt['lead_text'])


if __name__ == '__main__':
    unittest.main()
