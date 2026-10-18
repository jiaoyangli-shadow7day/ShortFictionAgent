"""Source-bound priority controls, not empirical judgments of reader appeal."""
import copy
import unittest

from short_fiction_agent.story_studio import chief_editor as chief
from short_fiction_agent.story_studio import editorial_closure as closure
from short_fiction_agent.story_studio import editorial_priority as priority
from short_fiction_agent.story_studio import expectation_ledger as ledger
from short_fiction_agent.story_studio import narrative_audit as narrative
from short_fiction_agent.story_studio.contracts import StoryError, digest
from tests.narrative_fixtures import narrative_response


BODY = '我买下电影的海外录像带权。\n她与老板签字，银行确认资金到账。\n她问我还要重复算账多久。\n她握住我的手，向记者公开我们的关系。'
READY = {'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []}
DETAIL = {'editorial_priority': 'copyedit_detail',
          'reading_effect': '交易已成立且回报已兑现，授权身份留白不影响读者理解。'}
CORE = {'editorial_priority': 'reading_core',
        'reading_effect': '重复结算拖延了读者等待的关系回应。'}


def reading(raw=None):
    return chief.validate_response(narrative_response(chief.prompt(BODY, {}), raw or READY), BODY)


class NarrativePriorityEdgeTests(unittest.TestCase):
    def changed(self, verdict, *, detail=True):
        raw = narrative_response(chief.prompt(BODY, {}))
        raw['narrative_audit']['weakest_middle'].update(
            verdict=verdict, repair_scope='none' if detail else 'local',
            **(DETAIL if detail else CORE))
        return chief.validate_response(raw, BODY)

    def test_detail_thin_and_uncertain_keep_raw_judgment_without_final_veto(self):
        for verdict in ('thin', 'uncertain'):
            with self.subTest(verdict=verdict):
                checked = self.changed(verdict)
                final = priority.final_gate(narrative.final_gate(checked, BODY, [checked]), BODY)
                self.assertEqual(final['recommendation'], 'ready')
                self.assertEqual(final['narrative_gate']['status'], 'complete')
                self.assertEqual(checked['narrative_audit']['audit']['weakest_middle']['verdict'], verdict)
                self.assertTrue(final['initial_copyedit_reminders'])
                self.assertEqual(final['initial_copyedit_reminders'][0]['verdict'], verdict)

    def test_same_accounting_words_do_not_waive_real_pacing_thin_or_uncertainty(self):
        for verdict, expected in (('thin', 'revise'), ('uncertain', 'uncertain')):
            with self.subTest(verdict=verdict):
                checked = self.changed(verdict, detail=False)
                final = priority.final_gate(narrative.final_gate(checked, BODY, [checked]), BODY)
                self.assertEqual(final['recommendation'], expected)

    def test_detail_replan_is_retained_as_model_scope_without_forcing_replan(self):
        raw = narrative_response(chief.prompt(BODY, {}))
        raw['narrative_audit']['repetition'].update(verdict='thin', repair_scope='replan', **DETAIL)
        normalized = chief.validate_response(raw, BODY)
        row = normalized['narrative_audit']['audit']['repetition']
        self.assertEqual(row['model_repair_scope'], 'replan')
        self.assertEqual(row['repair_scope'], 'none')
        self.assertFalse(normalized['narrative_audit']['findings'])

    def test_native_revise_based_only_on_optional_narrative_detail_is_projected(self):
        raw = narrative_response(chief.prompt(BODY, {}))
        raw.update(recommendation='revise', issues=[])
        raw['narrative_audit']['weakest_middle'].update(verdict='thin', repair_scope='none', **DETAIL)
        checked = chief.validate_response(raw, BODY)
        final = priority.final_gate(narrative.final_gate(checked, BODY, [checked]), BODY)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertEqual(final['editorial_priority_gate']['model_recommendation'], 'revise')

    def test_detail_cannot_waive_missing_source_or_coverage(self):
        raw = narrative_response(chief.prompt(BODY, {}))
        raw['narrative_audit']['weakest_middle'].update(verdict='uncertain', **DETAIL)
        raw['narrative_audit']['weakest_middle']['counterevidence_ids'] = ['p999']
        raw['narrative_audit']['coverage'].pop()
        checked = chief.validate_response(raw, BODY)
        final = priority.final_gate(narrative.final_gate(checked, BODY, [checked]), BODY)
        self.assertEqual(final['recommendation'], 'uncertain')
        self.assertTrue(final['narrative_gate']['missing'])


class LedgerPriorityEdgeTests(unittest.TestCase):
    def record(self, *, verdict='thin', typed=DETAIL, overall_verdict='effective'):
        def stage(task, prompt, allowed, *, ledger_plan):
            raw = narrative_response(prompt)
            raw['intervals'][-1].update(verdict=verdict, **typed)
            if overall_verdict != 'effective':
                raw['overall'].update(verdict=overall_verdict, **typed)
            receipt = {'input_hash': digest({'task': task, 'prompt': prompt}),
                       'response': raw, 'response_hash': digest(raw)}
            return ledger.validate_response(raw, BODY, ledger_plan=ledger_plan), receipt
        return ledger.run_ledger(BODY, {'channels': ['fanqie_short']}, stage=stage)['current_ledger']

    def test_optional_thin_and_uncertain_do_not_require_ledger_closure(self):
        for verdict in ('thin', 'uncertain'):
            with self.subTest(verdict=verdict):
                current = self.record(verdict=verdict, overall_verdict=verdict)
                before = copy.deepcopy(current)
                final = priority.final_gate(ledger.final_gate(READY, BODY, current), BODY)
                self.assertEqual(final['recommendation'], 'ready')
                self.assertFalse(final['expectation_gate']['missing'])
                self.assertFalse(final['expectation_gate']['uncertain_intervals'])
                self.assertFalse(final['expectation_gate']['retained_issue_ids'])
                self.assertTrue(final['initial_copyedit_reminders'])
                self.assertEqual(current, before)

    def test_core_thin_and_uncertain_still_block(self):
        for verdict, expected in (('thin', 'revise'), ('uncertain', 'uncertain')):
            with self.subTest(verdict=verdict):
                current = self.record(verdict=verdict, typed=CORE)
                final = priority.final_gate(ledger.final_gate(READY, BODY, current), BODY)
                self.assertEqual(final['recommendation'], expected)

    def test_same_chief_can_reclassify_technical_objection_without_claiming_fixed(self):
        current = self.record(typed=CORE)
        objection = ledger.findings(current, BODY)[0]
        raw = {**copy.deepcopy(READY), 'expectation_dispositions': [{
            'issue_id': objection['issue_id'], 'status': 'retained', 'evidence_ids': ['p1'],
            'reason': '仅未写法律身份，原段已经签字到账，阅读回报成立。', **DETAIL}]}
        final = priority.final_gate(ledger.final_gate(raw, BODY, current), BODY)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertEqual(current['intervals'][-1]['verdict'], 'thin')
        self.assertEqual(final['initial_copyedit_reminders'][0]['status'], 'retained')
        raw['expectation_dispositions'][0]['evidence_ids'] = ['p0']
        final = priority.final_gate(ledger.final_gate(raw, BODY, current), BODY)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertTrue(final['expectation_gate']['missing'])

    def test_priority_requires_reading_reason_and_cannot_waive_coverage(self):
        with self.assertRaises(StoryError):
            self.record(typed={'editorial_priority': 'copyedit_detail'})
        current = self.record()
        current['coverage_complete'] = False
        with self.assertRaisesRegex(StoryError, '完整绑定'):
            ledger.final_gate(READY, BODY, current)


class ClosurePriorityEdgeTests(unittest.TestCase):
    def readings(self, *, detail=True):
        item = {'severity': 'blocker', 'scope': 'local', 'evidence_ids': ['p0', 'p1'],
                'reason': '测试的授权留白或报账拖延判断。', 'fix_intent': '只改读者确实受影响的部分。',
                **(DETAIL if detail else CORE)}
        return [reading({**copy.deepcopy(READY), 'recommendation': 'revise', 'issues': [item]}), reading()]

    def test_omitted_optional_disposition_keeps_reminder_and_does_not_freeze_uncertain(self):
        readings = self.readings()
        final = reading({**copy.deepcopy(READY), 'recommendation': 'uncertain'})
        final.pop('issue_dispositions', None)
        result = priority.final_gate(closure.final_gate(final, BODY, readings), BODY)
        self.assertEqual(result['recommendation'], 'ready')
        self.assertFalse(result['editorial_closure']['missing'])
        self.assertEqual(result['initial_copyedit_reminders'][0]['status'], 'unanswered')

    def test_unconfirmed_optional_revision_is_retained_without_fake_resolution(self):
        readings = self.readings()
        key = closure.reading_registry(readings)[0]['issue_id']
        final = reading({**copy.deepcopy(READY), 'issue_dispositions': [{
            'issue_id': key, 'status': 'revision_resolved', 'evidence_ids': ['p1'],
            'reason': '模型声称改过，但该稿没有改变。'}]})
        result = priority.final_gate(closure.final_gate(final, BODY, readings), BODY)
        self.assertEqual(result['recommendation'], 'ready')
        disposition = result['editorial_closure']['dispositions'][0]
        self.assertEqual(disposition['status'], 'retained')
        self.assertEqual(disposition['model_status'], 'revision_resolved')
        self.assertEqual(disposition['unconfirmed_resolution'], 'unchanged_source')

    def test_core_unanswered_and_bad_optional_source_remain_incomplete(self):
        core = closure.final_gate(reading(), BODY, self.readings(detail=False))
        self.assertEqual(core['recommendation'], 'uncertain')
        self.assertTrue(any(path.endswith('.unanswered') for path in core['editorial_closure']['missing']))
        readings = self.readings()
        key = closure.reading_registry(readings)[0]['issue_id']
        final = reading({**copy.deepcopy(READY), 'issue_dispositions': [{
            'issue_id': key, 'status': 'retained', 'evidence_ids': ['p999'], 'reason': '无效来源。'}]})
        result = priority.final_gate(closure.final_gate(final, BODY, readings), BODY)
        self.assertEqual(result['recommendation'], 'uncertain')
        self.assertTrue(result['editorial_closure']['missing'])


if __name__ == '__main__':
    unittest.main()
