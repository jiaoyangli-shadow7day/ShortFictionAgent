"""Control-flow tests, not proof of a model's judgment of a real manuscript."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import expectation_ledger as ledger
from short_fiction_agent.story_studio.contracts import StoryError, digest


CONTRACT = {'channels': ['fanqie_short'], 'project_id': 'ledger-control'}


def interval(name, first, last, *, active=None, verdict='effective'):
    return {'interval_id': name, 'start_id': first, 'end_id': last,
        'known_at_start': '只沿用先前正文已建立的问题。',
        'active_question_ids': list(active or []),
        'local_evidence_ids': [first], 'reading_value': '本测试只检查源绑定和流程。',
        'counterevidence_ids': [], 'counterevidence': '无额外反证。',
        'verdict': verdict, 'reason': '测试指定判断，不代表真实文本语义。'}


def response(questions, events, intervals):
    return {'protocol': ledger.PROTOCOL, 'questions': questions, 'events': events,
        'intervals': intervals,
        'overall': {'verdict': 'effective', 'weakest_interval_id': intervals[0]['interval_id'],
            'reason': '只测试协议。', 'counterevidence': '无额外反证。',
            'limitations': '人工构造的控制流夹具不证明语义能力。'}}


def event(name, question, source, kind):
    return {'event_id': name, 'question_id': question, 'evidence_ids': [source],
        'type': kind, 'reader_now_knows': '测试用的读者知情状态。'}


class ExpectationLedgerTests(unittest.TestCase):
    def setUp(self):
        self.body = '最终我搬走了。\n还差一千元租金。\n她提议一起分担。\n我问她自己的选择。'
        self.questions = [{'question_id': 'q1', 'question': '如何付清租金？'}]
        self.events = [event('e0', 'q1', 'p0', 'opened'),
            event('e1', 'q1', 'p0', 'answer_preannounced')]
        self.raw = response(self.questions, self.events,
            [interval('i0', 'p0', 'p0', active=['q1']), interval('i1', 'p1', 'p3', active=['q1'])])

    def stage(self, body, *, verdict='effective', store=None):
        def call(task, prompt, allowed, *, ledger_plan):
            payload = json.loads(prompt.rsplit('\n', 1)[1])
            if store is not None:
                store.append({'task': task, 'payload': payload, 'plan': copy.deepcopy(ledger_plan)})
            first = not payload['carry']['questions']
            questions = self.questions if first else []
            events = [event('e0', 'q1', allowed[0], 'opened')] if first else []
            intervals = []
            if first:
                intervals.append(interval('i-' + allowed[0], allowed[0], allowed[0], active=['q1']))
                if len(allowed) > 1:
                    intervals.append(interval('i-' + allowed[1], allowed[1], allowed[-1],
                        active=['q1'], verdict=verdict))
            else:
                intervals.append(interval('i-' + allowed[0], allowed[0], allowed[-1], active=['q1']))
            raw = response(copy.deepcopy(questions), events, intervals)
            receipt = {'input_hash': digest({'task': task, 'prompt': prompt}),
                'response': raw, 'response_hash': digest(raw)}
            return ledger.validate_response(raw, body, ledger_plan=ledger_plan), receipt
        return call

    def test_preannounced_result_does_not_mechanically_close_method_question(self):
        self.assertEqual(ledger.validate_response(self.raw, self.body)['intervals'][1]['verdict'], 'effective')

    def test_state_event_cannot_be_swallowed_by_one_large_interval(self):
        self.raw['intervals'] = [interval('i0', 'p0', 'p3', active=['q1'])]
        with self.assertRaisesRegex(StoryError, 'event_state_boundary_swallowed'):
            ledger.validate_response(self.raw, self.body)

    def test_resolved_question_cannot_stay_active_without_reopening(self):
        self.raw['events'].append(event('e2', 'q1', 'p2', 'resolved'))
        self.raw['intervals'] = [interval('i0', 'p0', 'p0', active=['q1']),
            interval('i1', 'p1', 'p1', active=['q1']), interval('i2', 'p2', 'p2', active=['q1']),
            interval('i3', 'p3', 'p3', active=['q1'])]
        with self.assertRaisesRegex(StoryError, 'resolved_question_reference'):
            ledger.validate_response(self.raw, self.body)
        self.raw['events'].append(event('e3', 'q1', 'p3', 'reopened'))
        self.assertEqual(len(ledger.validate_response(self.raw, self.body)['events']), 4)

    def test_full_source_fits_one_turn_with_exact_source_and_no_old_judgments(self):
        saved = []
        record = ledger.run_ledger(self.body, CONTRACT, stage=self.stage(self.body, store=saved))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['payload']['paragraphs'], ledger.paragraphs(self.body))
        self.assertEqual(saved[0]['payload']['carry'], ledger.empty_carry())
        self.assertNotIn('source_cards', saved[0]['payload'])
        self.assertNotIn('claim_audit', saved[0]['payload'])
        self.assertTrue(record['current_ledger']['coverage_complete'])

    def test_real_carry_and_parent_receipt_drive_capacity_and_replay(self):
        body = '\n'.join(str(i) + '测试源文。' * 80 for i in range(12))
        base = len(ledger.next_plan('短。', CONTRACT)['prompt'])
        saved = []
        record = ledger.run_ledger(body, CONTRACT, stage=self.stage(body, store=saved),
            max_prompt_chars=base + 1800)
        self.assertGreater(len(saved), 1)
        self.assertEqual(record['current_ledger']['paragraph_ids'], [f'p{i}' for i in range(12)])
        self.assertTrue(all(len(item['plan']['prompt']) <= base + 1800 for item in saved))
        for index in range(1, len(saved)):
            self.assertTrue(saved[index]['payload']['carry']['questions'])
            self.assertTrue(saved[index]['payload']['source_history'])
            self.assertIsNotNone(saved[index]['payload']['parent_receipt_hash'])
            self.assertNotIn('verdict', json.dumps(saved[index]['payload']['carry'], ensure_ascii=False))
        def replay(task, prompt, allowed, *, ledger_plan):
            index = next(i for i, row in enumerate(saved) if row['plan']['plan_hash'] == ledger_plan['plan_hash'])
            receipt = copy.deepcopy(record['ledger_receipts'][index])
            for key in ('plan_hash', 'paragraph_ids', 'parent_receipt_hash'):
                receipt.pop(key)
            return ledger.validate_response(receipt['response'], body, ledger_plan=ledger_plan), receipt
        replayed = ledger.run_ledger(body, CONTRACT, stage=replay, max_prompt_chars=base + 1800)
        self.assertEqual(replayed, record)

    def test_forged_normalized_carry_is_rejected(self):
        actual = self.stage(self.body)
        def forged(*args, **kwargs):
            normalized, receipt = actual(*args, **kwargs)
            normalized['events'][0]['reader_now_knows'] = '并未由实际回执返回。'
            return normalized, receipt
        with self.assertRaisesRegex(StoryError, '实际回执不一致'):
            ledger.run_ledger(self.body, CONTRACT, stage=forged)

    def test_too_small_capacity_fails_without_truncation(self):
        with self.assertRaisesRegex(StoryError, '不能截断或跳过'):
            ledger.next_plan(self.body, CONTRACT, max_prompt_chars=2000)

    def test_unrelated_channel_adds_no_call(self):
        def forbidden(*args, **kwargs):
            self.fail('A channel without the guide must not dispatch a ledger turn')
        record = ledger.run_ledger(self.body, {}, stage=forbidden)
        self.assertIsNone(record['current_ledger'])

    def test_thin_and_uncertain_cannot_be_overwritten_by_ready(self):
        record = ledger.run_ledger(self.body, CONTRACT, stage=self.stage(self.body, verdict='thin'))
        final = ledger.final_gate({'recommendation': 'ready', 'issues': []}, self.body,
            record['current_ledger'], contract=CONTRACT)
        self.assertEqual(final['recommendation'], 'revise')
        finding = final['issues'][0]
        self.assertEqual(finding['origin'], 'narrative_audit')
        for row in finding['evidence']:
            self.assertEqual(self.body[slice(*row['span'])], row['text'])
        record['current_ledger']['intervals'][1]['verdict'] = 'uncertain'
        final = ledger.final_gate({'recommendation': 'ready', 'issues': []}, self.body,
            record['current_ledger'], contract=CONTRACT)
        self.assertEqual(final['recommendation'], 'uncertain')

    def test_later_evidence_cannot_close_an_earlier_thin_interval(self):
        record = ledger.run_ledger(self.body, CONTRACT, stage=self.stage(self.body, verdict='thin'))
        current = record['current_ledger']
        current['intervals'][1] = interval('i-p1', 'p1', 'p1', active=['q1'], verdict='thin')
        current['intervals'].append(interval('i-p2', 'p2', 'p3', active=['q1']))
        issue = ledger.findings(current, self.body)[0]
        base = {'recommendation': 'ready', 'issues': [], 'expectation_dispositions': [
            {'issue_id': issue['issue_id'], 'status': 'context_explained',
             'evidence_ids': ['p3'], 'reason': '用后段亮点解释前段。'}]}
        self.assertEqual(ledger.final_gate(base, self.body, current)['recommendation'], 'revise')
        base['expectation_dispositions'][0]['evidence_ids'] = ['p1']
        self.assertEqual(ledger.final_gate(base, self.body, current)['recommendation'], 'ready')
        base['expectation_dispositions'][0]['status'] = 'revision_resolved'
        self.assertEqual(ledger.final_gate(base, self.body, current)['recommendation'], 'revise')


if __name__ == '__main__':
    unittest.main()
