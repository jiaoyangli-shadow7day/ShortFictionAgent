"""Protocol controls, not empirical claims that a model judges fiction correctly."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, narrative_audit as narrative
from short_fiction_agent.story_studio.chief_review import finish_review
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, review_decision
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from tests.narrative_fixtures import narrative_response


class NarrativeAuditTests(unittest.TestCase):
    def setUp(self):
        self.body = '我想留下，试着取出第二副碗筷。\n她说每周可以回来，却先问我愿不愿意。\n我答应后她收好碗筷，留下了自己的钥匙。'
        self.prompt = chief.prompt(self.body, {})
        self.raw = narrative_response(self.prompt)

    def checked(self, raw=None, *, body=None, allowed=None, chunk=False):
        return chief.validate_response(raw or self.raw, body or self.body, allowed_ids=allowed,
                                       require_facts=chunk)

    def gate(self, raw=None):
        result = self.checked(raw)
        return narrative.final_gate(result, self.body, [result])

    def test_positive_source_bound_coverage_and_quiet_interaction(self):
        original = copy.deepcopy(self.raw)
        final = self.gate()
        self.assertEqual(final['recommendation'], 'ready')
        self.assertEqual(final['narrative_gate']['status'], 'complete')
        self.assertEqual(self.raw, original)
        for row in final['narrative_audit']['audit']['core_scenes']:
            self.assertEqual(row['attempt_ids'], row['response_ids'])
            for evidence in row['evidence']:
                self.assertEqual(self.body[slice(*evidence['span'])], evidence['text'])

    def test_reading_and_synthesis_check_expansion_value_and_keep_counterexamples(self):
        reading = self.checked({**self.raw, 'facts': []}, chunk=True)
        synthesis, _ = chief.synthesis_prompt(self.body, {}, [reading])
        for prompt in (self.prompt, synthesis):
            with self.subTest(stage='reading' if prompt == self.prompt else 'synthesis'):
                for rule in ('四环齐全不等于值得如此详写', '先真正写出该处可替代的压缩版本',
                             '不得靠尾部一处温情为大段操作补证',
                             '必要职业交锋、技术判断和安静关系回应可写足',
                             '不按步骤、术语或对白数量机械否决',
                             '局部表达负担时可保留effective并列suggestion',
                             'observations.role仍只允许condition/process/outcome，接招过程写process'):
                    self.assertIn(rule, prompt)

    def test_local_expansion_suggestion_does_not_invent_a_structural_blocker(self):
        self.raw['issues'] = [{'severity': 'suggestion', 'evidence_ids': ['p1'],
            'reason': '局部操作可以压缩，保留双方实际选择与回应。',
            'fix_intent': '删去不改变理解的操作细节。', 'scope': 'local'}]
        final = self.gate()
        self.assertEqual(final['recommendation'], 'ready')
        self.assertTrue(final['narrative_gate']['coverage_complete'])
        self.assertEqual([issue['severity'] for issue in final['issues']], ['suggestion'])

    def test_reading_and_synthesis_trace_expectation_in_reading_order(self):
        """Prompt wiring control; it does not certify the model's literary judgment."""
        reading = self.checked({**self.raw, 'facts': []}, chunk=True)
        synthesis, _ = chief.synthesis_prompt(self.body, {}, [reading])
        for prompt in (self.prompt, synthesis):
            for rule in ('分清期待何时提出、何时已回答或提前告知',
                         '回答之后核对正文还留下哪些有据的未定方法、人物选择、回应或代价',
                         '期待提出处、可能提前回答处及其后原段',
                         '不能用换场、办手续、金额入账等已知终点的兑现流程替原期待续命'):
                self.assertIn(rule, prompt)

    def test_known_outcome_and_result_first_leads_remain_valid_when_questions_are_grounded(self):
        reading = self.checked({**self.raw, 'facts': []}, chunk=True)
        synthesis, _ = chief.synthesis_prompt(self.body, {}, [reading])
        for prompt in (self.prompt, synthesis):
            for rule in ('结果前置的导语、倒叙及已知结局可以有效',
                         '读者仍可期待如何做到、为何如此、付出什么代价或人物怎样选择',
                         '由原段实际建立并持续展开',
                         '不因先透露结果机械判thin，也不强制结局未知或新增灾难'):
                self.assertIn(rule, prompt)

    def test_narrative_response_ids_do_not_expand_fact_observation_roles(self):
        self.raw['observations'] = [{'subject': '双方回应', 'role': 'response',
            'statement': '她问我愿不愿意。', 'evidence_ids': ['p1']}]
        with self.assertRaises(StoryError):
            self.checked()
        self.raw['observations'][0]['role'] = 'process'
        checked = self.checked()
        self.assertEqual(checked['observations'][0]['role'], 'process')
        self.assertTrue(checked['narrative_audit']['audit']['core_scenes'][0]['response_ids'])

    def test_missing_schema_is_uncertain_and_never_an_empty_pass(self):
        raw = {'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []}
        final = self.gate(raw)
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertEqual(final['narrative_gate']['status'], 'incomplete')
        self.assertEqual(final['issues'], [])

    def test_phases_cannot_all_pick_the_pretty_opening(self):
        for row in self.raw['narrative_audit']['coverage']:
            row['evidence_ids'] = ['p0']
        final = self.gate()
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertTrue(any('ending.source_range' in key for key in final['narrative_gate']['missing']))

    def test_weakest_middle_must_reference_actual_middle(self):
        self.raw['narrative_audit']['weakest_middle']['evidence_ids'] = ['p0']
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_out_of_input_counterevidence_cannot_complete_a_review(self):
        self.raw['narrative_audit']['weakest_middle']['counterevidence_ids'] = ['p999']
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_ending_and_weakest_core_are_both_mandatory(self):
        self.raw['narrative_audit']['core_scenes'].pop()
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_enacted_core_requires_source_for_each_interaction_link(self):
        self.raw['narrative_audit']['core_scenes'][0]['response_ids'] = []
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_source_window_cannot_claim_unprovided_bridge(self):
        body = '开头选择。\n隐藏的过渡。\n结尾回应。'
        prompt = chief.prompt(body, {})
        raw = narrative_response(prompt)
        raw['narrative_audit']['core_scenes'][0].update(start_id='p0', end_id='p2')
        checked = self.checked(raw, body=body, allowed=['p0', 'p2'])
        self.assertIn('core_scenes[0].unprovided_window', checked['narrative_audit']['missing'])
        self.assertFalse(checked['narrative_audit']['findings'])

    def test_core_beats_cannot_borrow_a_different_scene(self):
        self.raw['narrative_audit']['core_scenes'][0]['turn_ids'] = ['p0']
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_partial_reading_has_local_phases_and_allows_effective_transition(self):
        body = '\n'.join(f'{i}：' + '相处改变下一次选择。' * 20 for i in range(9))
        # Local-phase behavior needs at least one complete paragraph to fit.
        capacity = len(chief._prompt(chief.paragraphs(body)[:1], {},
            chunk={'index': 9999, 'count': 9999})) + 100
        plan = chief.build_plan(body, {}, max_prompt_chars=capacity)
        self.assertGreater(len(plan['chunks']), 1)
        self.assertTrue(all(len(part['prompt']) <= capacity for part in plan['chunks']))
        chunk = plan['chunks'][-1]
        data = json.loads(chunk['prompt'].rsplit('\n', 1)[1])
        self.assertEqual(data['narrative_scope'], 'chunk')
        raw = narrative_response(chunk['prompt'])
        scene = raw['narrative_audit']['core_scenes'][0]
        scene.update(verdict='transition', attempt_ids=[], response_ids=[], turn_ids=[], payoff_ids=[])
        checked = self.checked(raw, body=body, allowed=chunk['paragraph_ids'], chunk=True)
        self.assertEqual(checked['narrative_audit']['scope'], 'chunk')
        self.assertTrue(checked['narrative_audit']['coverage_complete'])

    def test_whole_story_cannot_call_core_payoff_a_transition_to_skip_it(self):
        self.raw['narrative_audit']['core_scenes'][0].update(verdict='transition', attempt_ids=[],
            response_ids=[], turn_ids=[], payoff_ids=[])
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_repetition_must_compare_source_in_actual_order(self):
        self.raw['narrative_audit']['repetition'].update(earlier_ids=['p2'], later_ids=['p0'])
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_structural_thin_is_blocker_and_returns_to_existing_replan_interface(self):
        check = self.raw['narrative_audit']['repetition']
        check.update(verdict='thin', repair_scope='replan',
            reason='测试负控制：前后只是换人办同一种事，前次选择不改变后次方案。')
        self.raw['recommendation'] = 'revise'  # A grounded narrative finding can supply the blocker.
        final = self.gate()
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['narrative_gate']['status'], 'thin')
        issue = chief.legacy_issues(final, self.body)[0]
        self.assertEqual(issue['repair_scope'], 'replan')
        self.assertEqual(issue['origin'], 'narrative_audit')
        self.assertEqual(issue['evidence_ids'], ['p0', 'p2'])

    def test_missing_fix_scope_does_not_invent_a_structural_defect(self):
        self.raw['narrative_audit']['repetition'].update(verdict='thin', repair_scope='none')
        self.assertEqual(self.gate()['recommendation'], 'uncertain')
        self.assertEqual(self.gate()['issues'], [])

    def test_thin_and_missing_are_independent_and_cannot_claim_complete_coverage(self):
        self.raw['narrative_audit']['repetition'].update(verdict='thin', repair_scope='replan')
        self.raw['narrative_audit']['coverage'].pop()
        final = self.gate()
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['narrative_gate']['status'], 'thin')
        self.assertFalse(final['narrative_gate']['coverage_complete'])
        self.assertTrue(final['narrative_gate']['missing'])

    def test_single_paragraph_can_support_multiple_phases_and_quiet_beats(self):
        body = '我试着留下，她递来钥匙；我答应每周回来，她收好第二副碗筷。'
        checked = self.checked(narrative_response(chief.prompt(body, {})), body=body)
        final = narrative.final_gate(checked, body, [checked])
        self.assertEqual(final['recommendation'], 'ready')

    def test_completed_final_does_not_hide_missing_chunk_coverage(self):
        result = self.checked()
        old = self.checked({'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []})
        final = narrative.final_gate(result, self.body, [old])
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertTrue(final['narrative_gate']['missing'])

    def test_native_uncertain_is_not_upgraded_by_complete_fields(self):
        self.raw['recommendation'] = 'uncertain'
        self.assertEqual(self.gate()['recommendation'], 'uncertain')

    def test_provisional_chunk_uncertainty_can_be_rechecked_in_existing_full_final(self):
        prompt = chief.prompt(self.body, {}, narrative_scope='chunk')
        raw = narrative_response(prompt)
        raw['facts'] = []
        raw['narrative_audit']['weakest_middle'].update(verdict='uncertain')
        chunk = chief.validate_response(raw, self.body, require_facts=True)
        self.assertFalse(chunk['narrative_audit']['coverage_complete'])
        final = narrative.final_gate(self.checked(), self.body, [chunk])
        self.assertEqual(final['recommendation'], 'ready')
        self.assertTrue(final['narrative_gate']['provisional_uncertainties'])
        self.assertEqual(chunk['narrative_audit']['audit']['weakest_middle']['verdict'], 'uncertain')

    def test_final_cannot_recheck_a_provisional_weakness_without_its_source(self):
        chunk = self.checked()
        final = self.checked()
        final['narrative_audit']['provided_ids'] = ['p0']
        final['narrative_audit']['provided_ids_hash'] = digest(['p0'])
        gated = narrative.final_gate(final, self.body, [chunk])
        self.assertEqual(gated['recommendation'], 'uncertain')
        self.assertTrue(any('unprovided_narrative_anchors' in k for k in gated['narrative_gate']['missing']))

    def test_neutral_narrative_anchors_reach_synthesis_without_old_verdicts(self):
        body = '\n'.join(f'{i}：' + '她作出回应，我因此改变决定。' * 10 for i in range(30))
        # Keep this a multi-chunk anchor test as the shared policy prompt grows.
        budget = len(chief.prompt('短。', {})) + 2500
        plan = chief.build_plan(body, {}, max_prompt_chars=budget)
        self.assertGreater(len(plan['chunks']), 1)
        readings = []
        for chunk in plan['chunks']:
            raw = narrative_response(chunk['prompt'])
            raw['narrative_audit']['weakest_middle']['reason'] = '旧评判不得成为汇总事实'
            readings.append(chief.validate_response(raw, body, allowed_ids=chunk['paragraph_ids'], require_facts=True))
        prompt, allowed = chief.synthesis_prompt(body, {}, readings, max_prompt_chars=100000)
        data = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertNotIn('旧评判不得成为汇总事实', prompt)
        self.assertEqual(data['narrative_scope'], 'full')
        self.assertEqual(len(data['narrative_reading_anchors']), len(readings))
        for reading, ids in zip(readings, data['narrative_reading_anchors']):
            self.assertEqual(ids, narrative.source_anchors(reading['narrative_audit']))
            self.assertTrue(set(ids) <= set(allowed))


class NarrativeProductionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = StoryProject(Path(temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100}, 'author_input': {'premise': '双方主动回应并改变共同生活。'}}))
        self.project.save_draft('她主动递来钥匙。\n我答应每周回来。\n她收好第二副碗筷。')
        self.body = self.project.active_draft()[1]
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('short_fiction_agent.story_studio.quality.model_research_context',
              return_value={'common': ['有效安静互动应保留。']}).start()

    def test_missing_raw_is_saved_and_replayed_as_unfinished_without_resend(self):
        def call(task, prompt):
            self.calls.append((task, prompt))
            if task == 'chief_editor_first_read':
                return narrative_response(prompt)
            return {'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []}
        reviewer = StoryReviewer(self.project, call)
        first = reviewer.review()
        self.assertEqual(first['chief_editor']['response']['recommendation'], 'uncertain')
        self.assertFalse(first['review_coverage']['narrative_complete'])
        self.assertEqual(first['decision'], 'revise_required')
        self.assertEqual(len(self.calls), 2)
        self.assertNotIn('narrative_audit', first['chief_editor']['chunks'][0]['response'])
        reviewer.review()
        self.assertEqual(len(self.calls), 2)
        self.assertFalse((self.project.root / 'memory/commits').exists())

    def test_positive_uses_same_editor_first_then_formal_read_and_tampering_cannot_pass(self):
        def call(task, prompt):
            self.calls.append((task, prompt))
            return narrative_response(prompt)
        report = StoryReviewer(self.project, call).review()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual([task for task, _ in self.calls], ['chief_editor_first_read', 'chief_editor_review'])
        self.assertTrue(report['review_coverage']['narrative_complete'])
        forged = copy.deepcopy(report)
        forged['chief_editor']['response']['narrative_audit']['audit']['weakest_middle']['reason'] = '篡改判断'
        forged['chief_editor']['record_hash'] = digest({k:v for k,v in forged['chief_editor'].items() if k!='record_hash'})
        with self.assertRaises(StoryError):
            review_decision(self.body, self.project.contract(), forged)

    def test_new_protocol_invalidates_old_criteria_and_never_accepts_old_approval(self):
        current = chief.criteria_hash()
        with patch.object(narrative, 'PROTOCOL', 'old-narrative-protocol'):
            self.assertNotEqual(current, chief.criteria_hash())
        with patch.object(narrative, 'INSTRUCTION', narrative.INSTRUCTION + '新要求'):
            self.assertNotEqual(current, chief.criteria_hash())
        with patch.object(narrative, 'VALIDATION_VERSION', 'old-narrative-validation'):
            self.assertNotEqual(current, chief.criteria_hash())

    def test_defect_and_missing_coverage_remain_independent_in_persisted_report(self):
        def call(task, prompt):
            raw = narrative_response(prompt)
            if task == 'chief_editor_first_read':
                return raw
            raw['narrative_audit']['coverage'].pop()
            raw['narrative_audit']['repetition'].update(verdict='thin', repair_scope='replan')
            return raw
        report = StoryReviewer(self.project, call).review()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertFalse(report['review_coverage']['narrative_complete'])
        self.assertEqual(report['chief_editor']['response']['narrative_gate']['status'], 'thin')
        self.assertTrue(report['chief_editor']['response']['narrative_gate']['missing'])
        forged = copy.deepcopy(report)
        forged['review_coverage']['narrative_complete'] = True
        with self.assertRaisesRegex(StoryError, '叙事覆盖标记'):
            review_decision(self.body, self.project.contract(), forged)

    def test_full_reading_synthesis_uses_existing_turns_and_one_identity(self):
        body = '\n'.join(f'{i}：' + '她主动提出条件，我回应后改变相处方式。' * 8 for i in range(90))
        self.project.save_draft(body)
        def call(task, prompt):
            self.calls.append((task, prompt))
            return narrative_response(prompt)
        report = StoryReviewer(self.project, call, max_prompt_chars=16000).review()
        chunks = report['chief_editor']['chunks']
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len(self.calls), len(chunks)+1+len(report['chief_editor']['first_read_receipts']))
        self.assertEqual({json.loads(p.rsplit('\n',1)[1])['reviewer_id'] for _,p in self.calls},
                         {report['chief_editor']['reviewer_id']})
        self.assertEqual(report['chief_editor']['response']['narrative_gate']['status'], 'complete')
        self.assertTrue(all(len(p)<=16000 for _,p in self.calls))


if __name__ == '__main__':
    unittest.main()
