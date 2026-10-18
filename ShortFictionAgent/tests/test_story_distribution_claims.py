"""Publication integration controls; scripted verdicts do not test model accuracy."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.distribution import chief_preparation, validate_chief_preparation
from short_fiction_agent.story_studio.quality import StoryReviewer
from short_fiction_agent.story_studio.storage import StoryProject
from tests.narrative_fixtures import narrative_response


class PublicationClaimTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = StoryProject(Path(self.temp.name) / 'publication')
        self.project.create(StoryBrief.from_dict({'title': '旧标题', 'format': 'micro_story',
            'length': {'min': 1, 'target': 100}, 'channels': ['fanqie_short'],
            'author_input': {'premise': '人物依照赛况预测下注，须核对实际过程。'}}))
        self.body = '他预测青禾从起点到终点一直领跑。\n青禾起步领先，在弯道仍领先。\n青禾保持领先直到夺冠。'
        self.project.save_draft(self.body)
        self.body = self.project.active_draft()[1]
        StoryReviewer(self.project, lambda task, prompt: narrative_response(prompt)).review()
        self.inputs = {'channel': 'fanqie_short', 'titles': ['新的赛马标题'],
                       'synopsis': '人物核对赛况后下注。', 'category': ''}

    def client(self, status='verified', *, include_process=True, invalid=False):
        calls = []
        def respond(task, prompt):
            calls.append((task, prompt))
            self.assertLessEqual(len(prompt), 30000)
            data = json.loads(prompt.rsplit('\n', 1)[1])
            if task.endswith('_expectation_ledger'):
                return narrative_response(prompt)
            if task.endswith('_claim_reread'):
                checks = [{'claim_id': key, 'status': status, 'reason': '测试桩：对照原断言与实际赛况。',
                           'process_ids': ['p1'] if include_process else [], 'outcome_ids': ['p2']}
                          for key in data['claim_audit']['claim_ids']]
                return {'recommendation': 'ready', 'issues': [], 'claim_checks': [] if invalid else checks}
            if task.endswith('_synthesis'):
                return narrative_response(prompt)
            available = {row['id'] for row in data['paragraphs']}
            observations = [
                {'subject': '青禾赛况', 'role': 'process', 'statement': '青禾实际领先过程。', 'evidence_ids': ['p1']},
                {'subject': '青禾赛况', 'role': 'outcome', 'statement': '青禾夺冠。', 'evidence_ids': ['p2']}]
            claim = {'kind': 'prediction', 'comparison_scope': 'process_and_outcome',
                    'subject': '青禾赛况', 'statement': '全程领跑的预测须与过程对照。',
                    'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': [], 'outcome_ids': [],
                    'status': 'pending', 'reason': '测试桩：定向核对实际过程。', 'search_terms': ['青禾']}
            return narrative_response(prompt, {'recommendation': 'ready', 'issues': [],
                    'observations': [row for row in observations if set(row['evidence_ids']) <= available],
                    'claims': [claim] if 'p0' in available else [],
                    **({'facts': []} if 'reading_turn' in data else {})})
        return respond, calls

    def test_publication_ready_cannot_override_missing_process_and_pending(self):
        call, calls = self.client(include_process=False)
        result = chief_preparation(self.project, call, **self.inputs)
        self.assertEqual([task for task, _ in calls],
                         ['chief_editor_publication', 'chief_editor_publication_expectation_ledger',
                          'chief_editor_publication_claim_reread'])
        self.assertEqual(result['decision'], 'needs_revision')
        self.assertEqual(result['metadata_response']['recommendation'], 'uncertain')
        checked = result['metadata_reading']['claim_checks']['claim_checks'][0]
        self.assertEqual((checked['model_status'], checked['status']), ('verified', 'pending'))
        self.assertFalse(validate_chief_preparation(self.project, result, self.body))
        self.assertEqual(self.project.active_draft()[1], self.body)
        self.assertEqual(chief_preparation(self.project, lambda *_: self.fail('cached result resent'),
                                          **self.inputs), result)

    def test_publication_verified_context_can_finish_without_rejecting_every_claim(self):
        call, calls = self.client()
        result = chief_preparation(self.project, call, **self.inputs)
        self.assertEqual(result['decision'], 'editorial_ready')
        self.assertEqual(result['metadata_response']['claim_gate']['status'], 'verified')
        self.assertEqual(len(result['metadata_response']['claim_gate']['claim_checks']), 1)
        self.assertTrue(validate_chief_preparation(self.project, result, self.body))
        self.assertEqual(len(calls), 3)

    def test_publication_conflict_becomes_source_bound_blocker_despite_ready(self):
        call, _ = self.client(status='conflict')
        result = chief_preparation(self.project, call, **self.inputs)
        self.assertEqual(result['decision'], 'needs_revision')
        final = result['metadata_response']
        self.assertEqual(final['claim_gate']['model_recommendation'], 'ready')
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual(final['issues'][0]['evidence_ids'], ['p0', 'p1', 'p2'])
        for source in final['issues'][0]['evidence']:
            self.assertEqual(self.body[slice(*source['span'])], source['text'])
        self.assertFalse(validate_chief_preparation(self.project, result, self.body))

    def test_long_publication_finishes_reading_reread_and_synthesis_within_capacity(self):
        from short_fiction_agent.story_studio import chief_editor
        self.project.save_draft(self.body + '\n第二章 名单\n' + '\n'.join(
            f'第{i}场，她核实名单，将确认过的事项记在本子里。' * 8 for i in range(100)))
        self.body = self.project.active_draft()[1]
        def baseline(task, prompt):
            data = json.loads(prompt.rsplit('\n', 1)[1])
            return narrative_response(prompt)
        # Scoped guide, existing research cards and current source-bound
        # expectation judgments require room beyond the former small header.
        capacity = 30000
        StoryReviewer(self.project, baseline, max_prompt_chars=capacity).review()
        call, calls = self.client()
        def complete_event(task, prompt):
            response = call(task, prompt)
            if task.endswith('_claim_reread'):
                for check in response['claim_checks']:
                    check.update(event_start_id='p1', event_end_id='p2')
            return response
        result = chief_preparation(self.project, complete_event, **self.inputs)
        self.assertGreater(len(result['metadata_reading']['chunks']), 1)
        read_rows = [row for task, prompt in calls if task == 'chief_editor_publication'
                     for row in json.loads(prompt.rsplit('\n', 1)[1])['paragraphs']]
        self.assertEqual(read_rows, [{'id': row['id'], 'text': row['text']}
                                    for row in chief_editor.paragraphs(self.body)])
        self.assertEqual(sum(task.endswith('_claim_reread') for task, _ in calls), 1)
        self.assertEqual(sum(task.endswith('_synthesis') for task, _ in calls), 1)
        self.assertTrue(all(len(prompt) <= capacity for _, prompt in calls))
        self.assertEqual(result['decision'], 'editorial_ready')
        self.assertTrue(validate_chief_preparation(self.project, result, self.body))
        self.assertEqual(chief_preparation(self.project, lambda *_: self.fail('exact cache resent'),
                                          **self.inputs), result)

    def test_tampered_reread_or_normalized_checks_fail_even_with_new_outer_hash(self):
        call, _ = self.client()
        result = chief_preparation(self.project, call, **self.inputs)
        for change in ('missing_receipt', 'response_hash', 'normalized_check', 'claim_plan'):
            forged = copy.deepcopy(result)
            reading = forged['metadata_reading']
            if change == 'missing_receipt':
                reading['claim_reread'] = None
            elif change == 'response_hash':
                reading['claim_reread']['response']['claim_checks'][0]['reason'] = '篡改原回答'
            elif change == 'normalized_check':
                reading['claim_checks']['claim_checks'][0]['status'] = 'pending'
            else:
                reading['claim_plan_hash'] = 'different'
            forged['record_hash'] = digest({key: value for key, value in forged.items() if key != 'record_hash'})
            with self.subTest(change=change), self.assertRaises(StoryError):
                validate_chief_preparation(self.project, forged, self.body)

    def test_invalid_completed_reread_is_preserved_without_semantic_resend(self):
        call, calls = self.client(invalid=True)
        for _ in range(2):
            with self.assertRaisesRegex(StoryError, 'claim_checks'):
                chief_preparation(self.project, call, **self.inputs)
        self.assertEqual(len(calls), 3)
        cached = list((self.project.root / 'publication_preparation_resume').glob('*/*.json'))
        self.assertEqual(len(cached), 3)
        self.assertEqual(sum(task.endswith('_claim_reread') for task, _ in calls), 1)
        self.assertEqual(sum(task.endswith('_expectation_ledger') for task, _ in calls), 1)
        self.assertFalse(list((self.project.root / 'publication_preparation').glob('*.json')))
        self.assertEqual(self.project.active_draft()[1], self.body)

    def test_unknown_reread_resumes_only_after_transport_allows_a_new_attempt(self):
        call, calls = self.client()
        failed = []
        def unknown(task, prompt):
            if task.endswith('_claim_reread'):
                failed.append(task)
                raise RuntimeError('transport outcome unknown; authorization belongs to transport')
            return call(task, prompt)
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            chief_preparation(self.project, unknown, **self.inputs)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(failed), 1)
        result = chief_preparation(self.project, call, **self.inputs)
        self.assertEqual([task for task, _ in calls],
                         ['chief_editor_publication', 'chief_editor_publication_expectation_ledger',
                          'chief_editor_publication_claim_reread'])
        self.assertTrue(validate_chief_preparation(self.project, result, self.body))


if __name__ == '__main__':
    unittest.main()
