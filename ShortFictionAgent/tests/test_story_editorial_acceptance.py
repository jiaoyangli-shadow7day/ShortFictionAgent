"""Adversarial protocol controls; scripted judgments are not literary validation."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, narrative_audit as narrative
from short_fiction_agent.story_studio import editorial_closure as closure, submission_guide
from short_fiction_agent.story_studio.chief_review import finish_review
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, current_review
from short_fiction_agent.story_studio.storage import StoryProject, write_json
from tests.narrative_fixtures import narrative_response


class EditorialAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.body = '第一章\n我问她要不要留下。\n她让我先听完条件。\n第二章\n我听完后答应每周回来。\n她收好第二副碗筷，递来钥匙。'

    def checked(self, raw=None):
        return chief.validate_response(raw or narrative_response(chief.prompt(self.body, {})), self.body)

    def test_actual_chapter_tails_and_closed_ending_are_required(self):
        prompt = chief.prompt(self.body, {})
        data = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertEqual(data['coverage_plan']['chapter_endings'], [
            {'boundary_id': 'p2', 'tail_ids': ['p1', 'p2'], 'is_final': False},
            {'boundary_id': 'p5', 'tail_ids': ['p4', 'p5'], 'is_final': True}])
        raw = narrative_response(prompt)
        self.assertEqual(raw['narrative_audit']['chapter_handoffs'][-1]['next_question'], '')
        self.assertTrue(self.checked(raw)['narrative_audit']['coverage_complete'])
        raw['narrative_audit']['chapter_handoffs'][0]['evidence_ids'] = ['p1']
        # Any actual final-tail paragraph can support the judgment; an opening
        # from another chapter cannot be substituted for the true ending.
        self.assertTrue(self.checked(raw)['narrative_audit']['coverage_complete'])
        raw['narrative_audit']['chapter_handoffs'][0]['evidence_ids'] = ['p4']
        self.assertIn('chapter_handoffs[0].actual_chapter_tail',
                      self.checked(raw)['narrative_audit']['missing'])

    def test_reading_cuts_are_never_invented_as_chapter_ends(self):
        rows = chief.paragraphs(self.body)
        data = json.loads(chief._prompt(rows[:2], {}, chunk={'index': 1, 'count': 2},
            full_rows=rows).rsplit('\n', 1)[1])
        self.assertEqual(data['coverage_plan']['chapter_endings'], [])

    def test_missing_main_question_or_real_compression_is_incomplete(self):
        raw = narrative_response(chief.prompt(self.body, {}))
        del raw['narrative_audit']['mainline']
        self.assertIn('mainline', self.checked(raw)['narrative_audit']['missing'])
        raw = narrative_response(chief.prompt(self.body, {}))
        del raw['narrative_audit']['weakest_middle']['compression']
        self.assertIn('weakest_middle.compression', self.checked(raw)['narrative_audit']['missing'])
        raw = narrative_response(chief.prompt(self.body, {}))
        raw['narrative_audit']['weakest_middle']['compression']['preserved_fact_ids'] = ['p5']
        self.assertIn('weakest_middle.compression.preserved_fact_ids.outside_weak_passage',
                      self.checked(raw)['narrative_audit']['missing'])

    def test_source_bound_weak_expectation_blocks_without_fact_conflict(self):
        raw = narrative_response(chief.prompt(self.body, {}))
        raw['narrative_audit']['mainline'].update(verdict='thin', repair_scope='replan',
            reason='测试负控制：原段持续换目标，当前问题与结局没有回应关系。')
        checked = self.checked(raw)
        final = narrative.final_gate(checked, self.body, [checked])
        self.assertEqual(final['recommendation'], 'revise')
        self.assertTrue(final['narrative_gate']['coverage_complete'])
        self.assertEqual(final['issues'][0]['origin'], 'narrative_audit')

    def readings_with_objection(self):
        rows = chief.paragraphs(self.body)
        results = []
        for selected in (rows[:3], rows[3:]):
            prompt = chief._prompt(selected, {}, chunk={'index': 1, 'count': 2}, full_rows=rows)
            raw = narrative_response(prompt)
            results.append(chief.validate_response(raw, self.body,
                allowed_ids=[row['id'] for row in selected], require_facts=True))
        first = results[0]
        first['narrative_audit']['findings'] = [{'severity': 'blocker', 'origin': 'narrative_audit',
            'scope': 'global', 'repair_scope': 'replan', 'evidence_ids': ['p1', 'p2'],
            'reason': '测试负控制：回应被概述替代，后续应核对是否补足。',
            'fix_intent': '保留实际选择和回应。'}]
        return results

    def test_synthesis_cannot_silently_drop_source_bound_reading_objection(self):
        readings = self.readings_with_objection()
        prompt, allowed = chief.synthesis_prompt(self.body, {}, readings)
        data = json.loads(prompt.rsplit('\n', 1)[1])
        self.assertEqual(len(data['editorial_issue_registry']), 1)
        raw = narrative_response(prompt)
        del raw['issue_dispositions']
        final = closure.final_gate(chief.validate_response(raw, self.body, allowed_ids=allowed), self.body, readings)
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertFalse(final['editorial_closure']['complete'])

    def test_mistaken_old_objection_can_be_withdrawn_with_current_evidence(self):
        readings = self.readings_with_objection()
        raw = narrative_response(chief.synthesis_prompt(self.body, {}, readings)[0])
        original = copy.deepcopy(readings)
        final = closure.final_gate(self.checked(raw), self.body, readings)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertTrue(final['editorial_closure']['complete'])
        self.assertEqual(readings, original)
        raw['issue_dispositions'][0].update(status='retained', reason='当前原段仍未解决该回应缺口。')
        final = closure.final_gate(self.checked(raw), self.body, readings)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['issues'][-1]['origin'], 'editorial_closure')
        raw['issue_dispositions'][0]['status'] = 'revision_resolved'
        final = closure.final_gate(self.checked(raw), self.body, readings)
        self.assertTrue(any(item.endswith('.unchanged_source') for item in final['editorial_closure']['missing']))

    def test_each_actual_external_rejection_requires_closure_after_body_change(self):
        feedback = {'decision': 'reject', 'source': 'editor', 'content_hash': digest('旧稿'),
                    'reason': '主问题游移，章末期待不足。'}
        raw = narrative_response(chief.prompt(self.body, {}, editorial_feedback=feedback))
        self.assertEqual(len(raw['issue_dispositions']), 2)
        raw['issue_dispositions'].pop()
        final = closure.final_gate(self.checked(raw), self.body, [self.checked(raw)], feedback)
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertTrue(any(item.endswith('.unanswered') for item in final['editorial_closure']['missing']))

    def test_unchanged_rejected_body_cannot_claim_revision_and_machine_advice_is_not_authority(self):
        feedback = {'decision': 'reject', 'source': 'author', 'content_hash': digest(self.body),
                    'reason': '需要具体关系回应'}
        raw = narrative_response(chief.prompt(self.body, {}, editorial_feedback=feedback))
        raw['issue_dispositions'][0]['status'] = 'revision_resolved'
        final = closure.final_gate(self.checked(raw), self.body, [self.checked(raw)], feedback)
        self.assertTrue(any(item.endswith('.unchanged_source') for item in final['editorial_closure']['missing']))
        self.assertEqual(closure.feedback_registry({**feedback, 'source': 'model'}, self.body), [])
        feedback.pop('content_hash')
        raw = narrative_response(chief.prompt(self.body, {}, editorial_feedback=feedback))
        raw['issue_dispositions'][0]['status'] = 'revision_resolved'
        final = closure.final_gate(self.checked(raw), self.body, [self.checked(raw)], feedback)
        self.assertTrue(any(item.endswith('.unknown_source_version') for item in final['editorial_closure']['missing']))

    def test_guide_semantics_and_plan_promise_reach_production_receipts_and_stale_approval_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = StoryProject(Path(tmp) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['fanqie_short'],
                'length': {'min': 1, 'target': 1000}, 'author_input': {'premise': '双方共同决定。'}}))
            project.save_draft('她让我先听完条件。\n第一章\n我答应每周回来。\n她递来钥匙。')
            plan = {'main_question': '双方能否共同决定以后的相处方式？', 'ending_resolution': '她主动递来钥匙。'}
            write_json(project.root / 'plan' / 'scenes.json', plan)
            calls = []
            def call(task, prompt):
                calls.append(prompt)
                return narrative_response(prompt)
            with patch('short_fiction_agent.story_studio.quality.model_research_context', return_value={}):
                report = StoryReviewer(project, call).review()
                cold = json.loads(calls[0].rsplit('\n', 1)[1])
                self.assertNotIn('planned_reader_promise', cold)
                self.assertNotIn('submission_guide', cold)
                data = json.loads(calls[1].rsplit('\n', 1)[1])
                self.assertEqual(data['planned_reader_promise']['main_question'], plan['main_question'])
                self.assertEqual(data['planned_reader_promise']['source_hash'], digest(plan))
                self.assertTrue(data['submission_guide']['enabled'])
                self.assertIn('mainline_expectation', data['submission_guide']['semantic_requirement_ids'])
                self.assertEqual(report['submission_guide']['content_hash'], digest(project.active_draft()[1]))
                current_review(project)
                plan['main_question'] = '另一项待兑现的具体问题？'
                write_json(project.root / 'plan' / 'scenes.json', plan)
                with self.assertRaisesRegex(StoryError, '当前方案'):
                    current_review(project)


if __name__ == '__main__':
    unittest.main()
