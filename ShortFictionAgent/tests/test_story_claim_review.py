"""Actual reviewer/cache/publication gates, using explicit transport controls."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, current_review, review_decision
from short_fiction_agent.story_studio.storage import StoryProject
from tests.narrative_fixtures import narrative_response


READY = {'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []}


class ClaimReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = StoryProject(Path(temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100},
            'author_input': {'premise': '我按订单交车，对方按实际收到的车辆结账。'}}))
        self.project.save_draft('我保证这一单交车不会晚于周一。\n周二上午我才交车。\n他确认收车，我却说自己按期交车。')
        _, self.body = self.project.active_draft()
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('short_fiction_agent.story_studio.quality.model_research_context',
              return_value={'common': {'rule': '只核对同一事件的原文证据。'}}).start()

    def initial(self):
        return {**copy.deepcopy(READY), 'claims': [{
            'kind': 'timeline', 'subject': '这一单交车',
            'statement': '这一单交车不会晚于周一。',
            'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': [], 'outcome_ids': [],
            'status': 'pending', 'reason': '须对照实际交车日期。'}]}

    def reader(self, status='pending', *, interrupted=False):
        first_unknown = interrupted
        ident = chief.validate_response(self.initial(), self.body)['claims'][0]['claim_id']

        def call(task, prompt):
            nonlocal first_unknown
            self.calls.append((task, prompt))
            if task == 'chief_editor_first_read':
                return narrative_response(prompt)
            if task == 'chief_editor_review':
                return narrative_response(prompt, self.initial())
            if task == 'chief_editor_claim_reread':
                if first_unknown:
                    first_unknown = False
                    raise RuntimeError('transport result unknown')
                return {'recommendation': 'ready', 'issues': [], 'claim_checks': [{
                    'claim_id': ident, 'status': status,
                    'process_ids': ['p1'] if status != 'pending' else [],
                    'outcome_ids': ['p2'] if status != 'pending' else [],
                    'reason': ('周一截止与周二才交车冲突，正文却将它当成按期交车。'
                               if status == 'conflict' else '当前回读不足以确认同一订单。')}]}
            raise AssertionError('Unexpected extra review: ' + task)
        return StoryReviewer(self.project, call)

    def test_missing_claim_arrays_cannot_silently_become_an_empty_audit(self):
        reviewer = StoryReviewer(self.project, lambda task, prompt: narrative_response(prompt)
            if task == 'chief_editor_first_read' else {'recommendation': 'ready', 'issues': []})
        with self.assertRaises(StoryError):
            reviewer.review()
        self.assertEqual(self.project.active_draft()[0]['state'], 'review_error')
        self.assertFalse((self.project.root / 'memory/commits').exists())
        self.assertTrue(list((self.project.root / 'review_resume').rglob('*.json')))

    def test_pending_survives_a_ready_flag_and_is_replayed_without_another_call(self):
        reviewer = self.reader()
        report = reviewer.review()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(report['chief_editor']['response']['recommendation'], 'uncertain')
        self.assertEqual(report['chief_editor']['response']['claim_gate']['model_recommendation'], 'ready')
        self.assertTrue(report['chief_editor']['response']['claim_gate']['pending_ids'])
        self.assertEqual([task for task, _ in self.calls], ['chief_editor_first_read', 'chief_editor_review', 'chief_editor_claim_reread'])
        self.assertEqual(current_review(self.project, require_passed=False)['review_id'], report['review_id'])
        with self.assertRaises(StoryError):
            current_review(self.project)
        reviewer.review()
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.project.active_draft()[1], self.body)

    def test_unknown_reread_preserves_initial_read_and_resumes_only_the_missing_stage(self):
        reviewer = self.reader(interrupted=True)
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            reviewer.review()
        self.assertEqual(self.project.active_draft()[1], self.body)
        report = reviewer.review()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual([task for task, _ in self.calls].count('chief_editor_review'), 1)
        self.assertEqual([task for task, _ in self.calls].count('chief_editor_claim_reread'), 2)

    def test_confirmed_conflict_enters_existing_concentrated_repair_with_actual_evidence(self):
        report = self.reader('conflict').review()
        self.assertEqual(report['decision'], 'revise_required')
        blockers = [item for item in report['issues'] if item['severity'] == 'blocker']
        self.assertTrue(blockers)
        ids = {ident for item in blockers for ident in item['evidence_ids']}
        self.assertTrue({'p0', 'p1', 'p2'} <= ids)
        for item in blockers:
            for evidence in item['evidence']:
                self.assertEqual(self.body[slice(*evidence['span'])], evidence['text'])
        self.assertFalse((self.project.root / 'memory/commits').exists())

    def test_changed_check_record_cannot_be_rebound_by_rehashing_the_outer_report(self):
        report = self.reader().review()
        forged = copy.deepcopy(report)
        forged['chief_editor']['claim_checks']['claim_checks'][0]['status'] = 'verified'
        forged['chief_editor']['record_hash'] = digest({key: value for key, value in
            forged['chief_editor'].items() if key != 'record_hash'})
        with self.assertRaises(StoryError):
            review_decision(self.body, self.project.contract(), forged)


if __name__ == '__main__':
    unittest.main()
