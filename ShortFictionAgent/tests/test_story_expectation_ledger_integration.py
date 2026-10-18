"""Independent source/receipt controls; scripted labels do not assess fiction."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import expectation_ledger as ledger
from short_fiction_agent.story_studio import chief_editor
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.distribution import chief_preparation, validate_chief_preparation
from short_fiction_agent.story_studio.quality import StoryReviewer, current_review
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from tests.narrative_fixtures import narrative_response


def contract():
    return StoryBrief.from_dict({
        'title': '修表铺', 'format': 'short_story', 'channels': ['fanqie_short'],
        'length': {'min': 1, 'target': 1000},
        'author_input': {'premise': '两人共同决定修表铺的去留。'},
    }).effective_contract()


def response_for_plan(plan, *, verdict='effective'):
    """A transport stub only: retain an opened question across real chunks."""
    ids = plan['paragraph_ids']
    first = ids[0] == 'p0'
    groups = [[ids[0]], ids[1:]] if first and len(ids) > 1 else [ids]
    intervals = []
    for group in groups:
        intervals.append({
            'interval_id': 'interval-' + group[0], 'start_id': group[0], 'end_id': group[-1],
            'known_at_start': '只以已经读到的原文为准。', 'active_question_ids': ['q1'],
            'local_evidence_ids': [group[0]], 'reading_value': '离线桩不评估阅读价值。',
            'counterevidence_ids': [], 'counterevidence': '语义应由独立实测判断。',
            'verdict': verdict, 'reason': '本样本只验证来源、连续覆盖和门禁传递。',
        })
    return {'protocol': ledger.PROTOCOL,
        'questions': [{'question_id': 'q1', 'question': '修表铺能否留下？'}] if first else [],
        'events': [{'event_id': 'event-0', 'question_id': 'q1', 'evidence_ids': ['p0'],
                    'type': 'opened', 'reader_now_knows': '读者已经看到去留问题。'}] if first else [],
        'intervals': intervals,
        'overall': {'verdict': verdict, 'weakest_interval_id': intervals[-1]['interval_id'],
            'reason': '这里只测试协议。', 'counterevidence': '不提供文学质量结论。',
            'limitations': '构造响应不是模型实际表现。'}}


def recording_stage(body, calls, *, verdict='effective'):
    def stage(task, prompt, allowed_ids, *, ledger_plan):
        calls.append((task, prompt, deepcopy(allowed_ids), deepcopy(ledger_plan)))
        raw = response_for_plan(ledger_plan, verdict=verdict)
        normalized = ledger.validate_response(raw, body, ledger_plan=ledger_plan)
        receipt = {'task': task, 'input_hash': digest(prompt),
                   'response': raw, 'response_hash': digest(raw)}
        return normalized, receipt
    return stage


class ExpectationLedgerSourceControls(unittest.TestCase):
    def setUp(self):
        self.body = ('先问修表铺能否留下。\n他向房东借来时间。\n'
                     '他交清欠租，房东续约。\n二人吃完晚饭。\n房东又通知拆迁。')
        self.contract = contract()

    def plan(self):
        return ledger.next_plan(self.body, self.contract)

    def test_real_plan_supplies_whole_source_actual_guide_and_neutral_carry(self):
        plan = self.plan()
        payload = json.loads(plan['prompt'].rsplit('\n', 1)[1])
        self.assertEqual(payload['paragraphs'], ledger.paragraphs(self.body))
        self.assertTrue(payload['submission_guide']['enabled'])
        self.assertIn('mainline_expectation', payload['submission_guide']['semantic_requirement_ids'])
        self.assertEqual(payload['carry'], ledger.empty_carry())
        self.assertEqual(payload['source_history'], [])
        self.assertIsNone(payload['parent_receipt_hash'])
        self.assertTrue(plan['is_final'])
        self.assertEqual(plan['prompt_hash'], digest(plan['prompt']))

    def test_event_cannot_hide_inside_giant_interval(self):
        plan = self.plan()
        raw = response_for_plan(plan)
        raw['events'].append({'event_id': 'event-2', 'question_id': 'q1',
            'evidence_ids': ['p2'], 'type': 'resolved', 'reader_now_knows': '去留已定。'})
        with self.assertRaisesRegex(StoryError, 'event_state_boundary_swallowed'):
            ledger.validate_response(raw, self.body, ledger_plan=plan)

    def test_future_local_anchor_and_unread_question_fail(self):
        plan = self.plan()
        for mutation in ('anchor', 'question'):
            raw = response_for_plan(plan)
            if mutation == 'anchor':
                raw['intervals'][0]['local_evidence_ids'] = ['p4']
            else:
                raw['questions'].append({'question_id': 'q2', 'question': '拆迁如何解决？'})
                raw['events'].append({'event_id': 'event-4', 'question_id': 'q2',
                    'evidence_ids': ['p4'], 'type': 'opened', 'reader_now_knows': '新的拆迁问题。'})
                raw['intervals'][0]['active_question_ids'] = ['q2']
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                ledger.validate_response(raw, self.body, ledger_plan=plan)

    def test_stale_criteria_and_modified_carry_are_rejected_even_with_fresh_plan_digest(self):
        for mutation in ('criteria', 'carry'):
            plan = self.plan()
            if mutation == 'criteria':
                plan['criteria_hash'] = 'previous-criteria'
            else:
                plan['carry']['interval_ids'].append('forged-history')
            plan['plan_hash'] = digest({key: value for key, value in plan.items()
                                        if key not in {'plan_hash', 'prompt'}})
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                ledger.validate_response(response_for_plan(plan), self.body, ledger_plan=plan)

    def test_resolved_question_can_be_reward_in_event_window_but_not_later(self):
        plan = self.plan()
        raw = response_for_plan(plan)
        raw['events'].append({'event_id': 'event-2', 'question_id': 'q1',
            'evidence_ids': ['p2'], 'type': 'resolved', 'reader_now_knows': '去留已经解决。'})
        template = raw['intervals'][0]
        raw['intervals'] = [{**deepcopy(template), 'interval_id': f'i{i}',
            'start_id': f'p{i}', 'end_id': f'p{i}', 'local_evidence_ids': [f'p{i}'],
            'active_question_ids': ['q1'] if i <= 2 else []} for i in range(5)]
        raw['overall']['weakest_interval_id'] = 'i3'
        self.assertEqual(ledger.validate_response(raw, self.body, ledger_plan=plan)
                         ['binding']['paragraph_count'], 5)
        raw['intervals'][3]['active_question_ids'] = ['q1']
        with self.assertRaisesRegex(StoryError, 'future_or_resolved_question_reference'):
            ledger.validate_response(raw, self.body, ledger_plan=plan)

    def test_preannounced_result_keeps_method_expectation_structurally_possible(self):
        plan = self.plan()
        raw = response_for_plan(plan)
        raw['events'].append({'event_id': 'event-2', 'question_id': 'q1',
            'evidence_ids': ['p2'], 'type': 'answer_preannounced',
            'reader_now_knows': '结果被预告，如何做到仍未交代。'})
        template = raw['intervals'][0]
        raw['intervals'] = [{**deepcopy(template), 'interval_id': f'i{i}',
            'start_id': f'p{i}', 'end_id': f'p{i}',
            'local_evidence_ids': [f'p{i}']} for i in range(5)]
        raw['overall']['weakest_interval_id'] = 'i3'
        self.assertEqual(ledger.validate_response(raw, self.body, ledger_plan=plan)
                         ['binding']['paragraph_count'], 5)


class ExpectationLedgerDriverControls(unittest.TestCase):
    def setUp(self):
        self.body = '\n'.join(f'第{i}段：' + '修表铺里这次需要共同面对的选择。' * 35
                              for i in range(12))
        self.contract = contract()

    def test_driver_splits_whole_paragraphs_carries_raw_history_and_keeps_identity(self):
        baseline = ledger.next_plan(self.body, self.contract)
        limit = len(baseline['prompt']) - len(self.body) + 1800
        calls = []
        result = ledger.run_ledger(self.body, self.contract, stage=recording_stage(self.body, calls),
                                   max_prompt_chars=limit, reviewer_id='one-chief')
        self.assertGreater(len(calls), 1)
        self.assertEqual(result['current_ledger']['paragraph_ids'],
                         [row['id'] for row in ledger.paragraphs(self.body)])
        self.assertTrue(result['current_ledger']['coverage_complete'])
        self.assertEqual(result['current_ledger']['reviewer_id'], 'one-chief')
        for index, (task, prompt, ids, plan) in enumerate(calls):
            with self.subTest(chunk=index):
                self.assertEqual(task, 'chief_editor_expectation_ledger')
                self.assertLessEqual(len(prompt), limit)
                payload = json.loads(prompt.rsplit('\n', 1)[1])
                self.assertEqual(payload['reviewer_id'], 'one-chief')
                self.assertEqual([row['id'] for row in payload['paragraphs']], ids)
                self.assertEqual(set(payload['carry']), {'questions', 'events', 'interval_ids'})
                if index:
                    previous = result['ledger_receipts'][index - 1]
                    original_receipt = {key: value for key, value in previous.items()
                        if key not in {'plan_hash', 'paragraph_ids', 'parent_receipt_hash'}}
                    self.assertEqual(plan['parent_receipt_hash'], digest(original_receipt))
                    self.assertEqual(payload['source_history'][0]['id'], 'p0')
                    self.assertEqual(payload['carry']['questions'][0]['question_id'], 'q1')
        self.assertEqual(result['ledger_plan_hash'], digest([row[3]['plan_hash'] for row in calls]))

    def test_normalized_result_cannot_replace_actual_raw_answer(self):
        calls = []
        base = recording_stage(self.body, calls)
        def tampered(*args, **kwargs):
            normalized, receipt = base(*args, **kwargs)
            normalized['intervals'][0]['reason'] = '伪造的已规范化结论'
            return normalized, receipt
        with self.assertRaisesRegex(StoryError, '规范化结果与实际回执不一致'):
            ledger.run_ledger(self.body, self.contract, stage=tampered)

    def test_missing_raw_receipt_or_changed_hash_cannot_complete_driver(self):
        for kind in ('missing', 'changed'):
            calls = []
            base = recording_stage(self.body, calls)
            def tampered(*args, **kwargs):
                normalized, receipt = base(*args, **kwargs)
                if kind == 'missing':
                    del receipt['response']
                else:
                    receipt['response']['overall']['reason'] = '未同步响应摘要的替换'
                return normalized, receipt
            with self.subTest(kind=kind), self.assertRaisesRegex(StoryError, '实际模型回执'):
                ledger.run_ledger(self.body, self.contract, stage=tampered)

    def test_capacity_never_truncates_single_oversize_paragraph(self):
        with self.assertRaisesRegex(StoryError, '单段正文'):
            ledger.next_plan('正文' * 10000, self.contract, max_prompt_chars=2000)

    def test_missing_ledger_is_not_ready_for_enabled_submission_path(self):
        with self.assertRaisesRegex(StoryError, '缺少期待账本'):
            ledger.final_gate({'issues': [], 'recommendation': 'ready'}, self.body, None,
                              contract=self.contract)

    def test_thin_interval_cannot_be_cleared_with_future_evidence_or_same_source_revision(self):
        calls = []
        result = ledger.run_ledger(self.body, self.contract,
                                   stage=recording_stage(self.body, calls, verdict='thin'))
        current = result['current_ledger']
        issues = ledger.findings(current, self.body)
        for status in ('context_explained', 'revision_resolved'):
            final = {'issues': [], 'recommendation': 'ready', 'expectation_dispositions': [
                {'issue_id': issue['issue_id'], 'status': status, 'reason': '测试撤销尝试。',
                 'evidence_ids': ['p11']} for issue in issues]}
            checked = ledger.final_gate(final, self.body, current, contract=self.contract)
            with self.subTest(status=status):
                self.assertEqual(checked['recommendation'], 'revise')
                self.assertIn(issues[0]['issue_id'], checked['expectation_gate']['retained_issue_ids'])
                expected = ('.outside_interval_or_missing_explanation' if status == 'context_explained'
                            else '.same_source_version')
                self.assertIn(issues[0]['issue_id'] + expected, checked['expectation_gate']['missing'])

    def test_local_context_can_withdraw_a_mistaken_thin_label_without_forcing_revision(self):
        calls = []
        current = ledger.run_ledger(self.body, self.contract,
            stage=recording_stage(self.body, calls, verdict='thin'))['current_ledger']
        issues = ledger.findings(current, self.body)
        final = {'issues': [], 'recommendation': 'ready', 'expectation_dispositions': [
            {'issue_id': issue['issue_id'], 'status': 'context_explained',
             'evidence_ids': issue['evidence_ids'],
             'reason': '这是离线反控：允许同一区间的实际选择反驳错误薄弱判断。'}
            for issue in issues]}
        checked = ledger.final_gate(final, self.body, current, contract=self.contract)
        self.assertEqual(checked['recommendation'], 'ready')
        self.assertEqual(checked['expectation_gate']['retained_issue_ids'], [])
        self.assertEqual(checked['expectation_gate']['missing'], [])

    def test_actual_paragraph_spans_survive_repeated_text_and_existing_repair_adapter(self):
        body = '相同的原文。\n\n相同的原文。\n接着作出选择。'
        calls = []
        current = ledger.run_ledger(body, self.contract,
            stage=recording_stage(body, calls, verdict='thin'))['current_ledger']
        issues = ledger.findings(current, body)
        repaired = chief_editor.legacy_issues({'issues': issues}, body)
        self.assertEqual(len(repaired), 2)
        self.assertNotEqual(repaired[0]['span'], repaired[1]['span'])
        for issue in repaired:
            start, end = issue['span']
            self.assertEqual(body[start:end], issue['quote'])


class ExpectationLedgerProductionReplayControls(unittest.TestCase):
    """Use production stage wiring and durable receipts, with invented judgments."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = StoryProject(Path(temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({
            'title': '共同留下修表铺', 'format': 'short_story', 'opening_mode': 'direct_scene',
            'channels': ['fanqie_short'], 'length': {'min': 1, 'target': 1000, 'max': 100000},
            'author_input': {'premise': '双方共同决定修表铺的去留。'}}))
        self.project.save_draft('\n'.join(f'第{i}段：' + '修表铺里这次需要共同面对的选择。' * 35
                                           for i in range(48)))
        self.body = self.project.active_draft()[1]
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('short_fiction_agent.story_studio.quality.model_research_context', return_value={}).start()

    def review(self, *, thin=False):
        def call(task, prompt):
            self.calls.append((task, prompt))
            raw = narrative_response(prompt)
            if thin and task.endswith('_expectation_ledger'):
                # One explicit negative stub exercises the production gate;
                # this is not a diagnosis of the constructed text.
                if raw['intervals'][0]['start_id'] == 'p0':
                    raw['intervals'][0].update(verdict='thin', reason='离线桩：验证负判断传到正式门禁。')
                    raw['overall'].update(verdict='thin',
                        weakest_interval_id=raw['intervals'][0]['interval_id'])
            return raw
        report = StoryReviewer(self.project, call, max_prompt_chars=30000).review()
        self.report_path = self.project.root / 'reviews' / f"{report['review_id']}.json"
        return report

    def replace(self, original, change):
        report = deepcopy(original)
        change(report['chief_editor'])
        chief = report['chief_editor']
        chief['record_hash'] = digest({key: value for key, value in chief.items() if key != 'record_hash'})
        write_json(self.report_path, report)

    def test_live_stage_chain_and_formal_generic_export_replay_all_actual_ledger_receipts(self):
        report = self.review()
        chief = report['chief_editor']
        self.assertGreater(len(chief['ledger_receipts']), 1)
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(chief['current_ledger']['paragraph_ids'],
                         [row['id'] for row in ledger.paragraphs(self.body)])
        self.assertEqual(chief['current_ledger']['reviewer_id'], chief['reviewer_id'])
        ledger_prompts = [prompt for task, prompt in self.calls if task.endswith('_expectation_ledger')]
        self.assertEqual(len(ledger_prompts), len(chief['ledger_receipts']))
        self.assertTrue(all(len(prompt) <= 30000 for prompt in ledger_prompts))
        self.assertEqual(current_review(self.project)['review_id'], report['review_id'])
        package = StoryExporter(self.project).export('generic_text')
        self.assertTrue((package / 'manifest.json').exists())

    def test_missing_ledger_and_forged_current_carry_cannot_export_with_recomputed_outer_hash(self):
        report = self.review()
        changes = {
            'missing': lambda chief: chief.pop('ledger_receipts'),
            'carry': lambda chief: chief['current_ledger']['questions'][0].update(question='改写的历史问题'),
            'coverage': lambda chief: chief['ledger_receipts'][0]['paragraph_ids'].pop(),
            'parent': lambda chief: chief['ledger_receipts'][1].update(parent_receipt_hash='forged-parent'),
        }
        for name, change in changes.items():
            with self.subTest(mutation=name):
                self.replace(report, change)
                with self.assertRaises(StoryError):
                    current_review(self.project)
                with self.assertRaises(StoryError):
                    StoryExporter(self.project).export('generic_text')
        self.assertFalse(list((self.project.root / 'exports').glob('*/manifest.json')))

    def test_changed_first_raw_history_cannot_reuse_later_turn_input(self):
        report = self.review()
        self.assertGreater(len(report['chief_editor']['ledger_receipts']), 1)
        def change(chief):
            first = chief['ledger_receipts'][0]
            first['response']['questions'][0]['question'] = '伪造的前序提问会改变后续真实输入。'
            first['response_hash'] = digest(first['response'])
        self.replace(report, change)
        with self.assertRaises(StoryError):
            current_review(self.project)
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export('generic_text')

    def test_future_interval_anchor_cannot_be_repaired_by_rehashing_actual_receipt(self):
        report = self.review()
        def change(chief):
            first = chief['ledger_receipts'][0]
            first['response']['intervals'][0]['local_evidence_ids'] = ['p47']
            first['response_hash'] = digest(first['response'])
        self.replace(report, change)
        with self.assertRaisesRegex(StoryError, 'local_evidence_ids'):
            current_review(self.project)
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export('generic_text')

    def test_changed_ledger_criteria_invalidates_formal_approval(self):
        self.review()
        before = chief_editor.criteria_hash()
        with patch.object(ledger, 'VALIDATION_VERSION', ledger.VALIDATION_VERSION + '-changed'):
            self.assertNotEqual(chief_editor.criteria_hash(), before)
            with self.assertRaises(StoryError):
                current_review(self.project)
            with self.assertRaises(StoryError):
                StoryExporter(self.project).export('generic_text')

    def test_actual_ledger_thin_blocks_passed_state_and_formal_export(self):
        report = self.review(thin=True)
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(self.project.active_draft()[0]['state'], 'revise_required')
        self.assertTrue(report['chief_editor']['response']['expectation_gate']['retained_issue_ids'])
        self.assertTrue(any(issue['origin'] == 'narrative_audit' for issue in report['issues']))
        self.assertTrue(any(task.endswith('_synthesis') for task, _ in self.calls))
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export('generic_text')

    def test_changed_metadata_requires_bound_ledger_and_refuses_missing_receipt_cache(self):
        self.review()
        inputs = {'channel': 'fanqie_short', 'titles': ['他选择与她留下修表铺'],
                  'synopsis': '两人共同承担选择。', 'category': ''}
        calls = []
        def call(task, prompt):
            calls.append((task, prompt))
            return narrative_response(prompt)
        preparation = chief_preparation(self.project, call, **inputs)
        self.assertTrue(validate_chief_preparation(self.project, preparation, self.body))
        reading = preparation['metadata_reading']
        self.assertGreater(len(reading['ledger_receipts']), 1)
        ledger_prompts = [prompt for task, prompt in calls if task.endswith('_expectation_ledger')]
        self.assertTrue(ledger_prompts)
        self.assertTrue(all(prompt.startswith(ledger.INSTRUCTION) for prompt in ledger_prompts))
        forged = deepcopy(preparation)
        forged['metadata_reading'].pop('ledger_receipts')
        forged['record_hash'] = digest({key: value for key, value in forged.items() if key != 'record_hash'})
        with self.assertRaises(StoryError):
            validate_chief_preparation(self.project, forged, self.body)
        path = self.project.root / 'publication_preparation' / f"{digest(forged['inputs'])}.json"
        write_json(path, forged)
        with self.assertRaises(StoryError):
            StoryExporter(self.project, call).export('fanqie_short', titles=inputs['titles'],
                synopsis=inputs['synopsis'], category='')
        self.assertFalse(list((self.project.root / 'exports').glob('*/manifest.json')))


if __name__ == '__main__':
    unittest.main()
