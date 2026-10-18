"""Separate reading targets from the full evidence budget, without model calls."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, claim_audit
from short_fiction_agent.story_studio.chief_review import finish_review
from short_fiction_agent.story_studio.contracts import StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject
from tests.narrative_fixtures import narrative_response


class ChiefCapacityTests(unittest.TestCase):
    def readings(self, body, plan):
        results = []
        for chunk in plan['chunks']:
            ids = chunk['paragraph_ids']
            raw = {'recommendation': 'ready', 'issues': [], 'facts': [], 'observations': [], 'claims': [{
                'kind': 'prediction', 'comparison_scope': 'process_and_outcome',
                'subject': '测试事件' + chunk['id'], 'statement': '测试桩：本次事件的完整经过已核验。',
                'assertion_ids': [ids[0]], 'condition_ids': [], 'process_ids': [ids[1]],
                'outcome_ids': [ids[-1]], 'event_start_id': ids[1], 'event_end_id': ids[-1],
                'status': 'verified', 'reason': '测试桩：事件完整原段已提供。'}]}
            results.append(chief.validate_response(raw, body, allowed_ids=ids, require_facts=True))
        return results

    def test_default_total_budget_keeps_original_thirty_thousand_reading_prompts(self):
        body = '\n'.join(f'{i}：' + '完整叙述应保留每一个原段。' * 40 for i in range(130))
        plan = chief.build_plan(body, {})
        old = chief.build_plan(body, {}, max_prompt_chars=30000)
        self.assertEqual(plan['max_prompt_chars'], 100000)
        self.assertEqual(plan['reading_prompt_chars'], 30000)
        self.assertEqual(plan['reading_limit_chars'], 30000)
        self.assertGreater(len(plan['chunks']), 1)
        self.assertEqual([c['prompt'] for c in plan['chunks']], [c['prompt'] for c in old['chunks']])
        self.assertEqual([c['span'] for c in plan['chunks']], [c['span'] for c in old['chunks']])
        self.assertTrue(all(len(c['prompt']) <= 30000 for c in plan['chunks']))
        self.assertEqual([key for c in plan['chunks'] for key in c['paragraph_ids']],
                         [row['id'] for row in chief.paragraphs(body)])

    def test_large_mandatory_evidence_fits_default_and_small_total_rejects_without_truncating(self):
        body = '\n'.join(f'{i}：' + '完整事件原文不得被截掉或改写。' * 40 for i in range(130))
        reading_plan = chief.build_plan(body, {})
        readings = self.readings(body, reading_plan)
        original = copy.deepcopy(readings)
        calls = []

        def stage(task, prompt, allowed, **kwargs):
            calls.append((task, prompt, allowed))
            return chief.validate_response({'recommendation': 'ready', 'issues': [],
                'claims': [], 'observations': []}, body, allowed_ids=allowed), {'test': 'local-stage'}

        final, continuation = finish_review(body, {}, readings, stage=stage)
        self.assertEqual([call[0] for call in calls], ['chief_editor_synthesis'])
        self.assertGreater(len(calls[0][1]), 30000)
        self.assertLessEqual(len(calls[0][1]), chief.DEFAULT_MAX_PROMPT_CHARS)
        self.assertEqual(calls[0][2], [row['id'] for row in chief.paragraphs(body)])
        supplied = json.loads(calls[0][1].rsplit('\n', 1)[1])
        self.assertEqual(supplied['paragraphs'], [[row['id'], row['text']] for row in chief.paragraphs(body)])
        self.assertEqual(len(final['claim_gate']['claim_checks']), len(readings))
        self.assertEqual(len(supplied['claim_audit']['registry']), len(readings))
        self.assertIsNone(continuation['claim_reread'])
        self.assertEqual(readings, original)
        calls.clear()
        with self.assertRaisesRegex(StoryError, '超过汇总容量'):
            finish_review(body, {}, readings, stage=stage, max_prompt_chars=30000)
        self.assertEqual(calls, [])
        self.assertEqual(readings, original)

    def test_one_whole_paragraph_can_exceed_reading_target_but_not_total_budget(self):
        body = '同一完整段落不能被截成两份。' * 3000
        plan = chief.build_plan(body, {})
        self.assertEqual(len(plan['chunks']), 1)
        chunk = plan['chunks'][0]
        self.assertGreater(len(chunk['prompt']), plan['reading_prompt_chars'])
        self.assertLessEqual(len(chunk['prompt']), plan['max_prompt_chars'])
        self.assertEqual(chunk['span'], [0, len(body)])
        self.assertEqual(json.loads(chunk['prompt'].rsplit('\n', 1)[1])['paragraphs'],
                         [{'id': 'p0', 'text': body}])
        with self.assertRaisesRegex(StoryError, '超过主审容量'):
            chief.build_plan(body, {}, max_prompt_chars=30000)
        multiple = chief.build_plan('起点。\n' + body + '\n终点。', {})
        self.assertEqual([row for ch in multiple['chunks'] for row in
            json.loads(ch['prompt'].rsplit('\n', 1)[1])['paragraphs']],
            [{'id': p['id'], 'text': p['text']} for p in chief.paragraphs('起点。\n' + body + '\n终点。')])
        self.assertTrue(all(len(ch['prompt']) <= multiple['max_prompt_chars'] for ch in multiple['chunks']))



if __name__ == '__main__':
    unittest.main()
