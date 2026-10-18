"""Priority/receipt regressions; scripted verdicts do not prove reader appeal.

The same finance/copyright words appear on both sides of these controls. The
test supplies the editor's semantic priority explicitly; no keyword detector
or invented retention measurement is being tested.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief
from short_fiction_agent.story_studio import claim_audit, editorial_closure, submission_guide
from short_fiction_agent.story_studio import editorial_priority as priority
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.distribution import chief_preparation, validate_chief_preparation
from short_fiction_agent.story_studio.quality import (
    DIMENSIONS, StoryReviewer, current_review, review_decision, review_rubric_hash,
)
from short_fiction_agent.story_studio.storage import StoryProject
from tests.narrative_fixtures import narrative_response


READY = {'recommendation': 'ready', 'issues': [], 'claims': [], 'observations': []}
BODY = ('我用三万元买下电影的海外录像带权。\n'
        '她与黄老板一同签字，银行确认两百万已进入制作账户。\n'
        '我再次向她报完余额，她问我还要算账多久。\n'
        '她握住我的手，在记者面前承认了我们的关系。')
# Real rev6 objection, reduced to its relevant reason rather than an invented
# short "missing copyright procedure" straw man. It claimed core causality even
# though the requested remedy was more legal/project authorization exposition.
HONGKONG_RIGHTS_REASON = (
    '融资主链仍缺少海外录像带权处分权限的来源。共同签字没有说明沈知棠或黄老板当时分别代表'
    '哪个权利主体、谁已经持有海外权、谁有权在项目尚未违约时把该权利附条件授予陆昭。'
    '后续银行以这份文件为五区权利同步生效的前提，因此这不是无关手续留白，而是三万元锁权及'
    '两百万进入制作账户能否成立的关键因果缺口。')


def issue(*, detail=True, reason=None, evidence_ids=None):
    return {'severity': 'blocker', 'scope': 'local',
        'evidence_ids': evidence_ids or ['p0', 'p1'],
        'reason': reason or HONGKONG_RIGHTS_REASON,
        'fix_intent': ('初始校对时可用一句话补充权利主体；不要扩写合同手续。' if detail else
                       '删去重复金额核对，尽快让女主作出公开回应。'),
        'editorial_priority': 'copyedit_detail' if detail else 'reading_core',
        'reading_effect': ('交易与发财结果已经清楚，法律主体留白不影响读者理解或期待。' if detail else
                           '已完成的发财结果被反复结算，拖延读者等待的关系回应。')}


def reading(raw, body=BODY):
    prompt = chief.prompt(body, {})
    return chief.validate_response(narrative_response(prompt, raw), body)


class EditorialPriorityHelperTests(unittest.TestCase):
    def test_typed_detail_is_retained_as_a_source_bound_initial_reminder(self):
        normalized = reading({**copy.deepcopy(READY), 'recommendation': 'revise',
                              'issues': [issue()]})
        before = copy.deepcopy(normalized)
        final = priority.final_gate(normalized, BODY)
        self.assertEqual(final['recommendation'], 'ready')
        self.assertFalse(any(item['severity'] == 'blocker' for item in final['issues']))
        self.assertTrue(final['initial_copyedit_reminders'])
        reminder = final['initial_copyedit_reminders'][0]
        self.assertTrue(priority.is_detail(reminder))
        self.assertEqual(reminder['reason'], HONGKONG_RIGHTS_REASON)
        for evidence in reminder['evidence']:
            self.assertEqual(BODY[slice(*evidence['span'])], evidence['text'])
        self.assertEqual(normalized, before)

    def test_same_money_and_copyright_words_do_not_waive_real_pacing_problem(self):
        core = issue(detail=False, reason=(
            '版权已经生效、两百万已经到账，但随后三个场景只重复金额与签字结果，'
            '没有新的交锋或关系变化，女主公开回应一直被推迟。'))
        normalized = reading({**copy.deepcopy(READY), 'recommendation': 'revise',
                              'issues': [issue(), core]})
        final = priority.final_gate(normalized, BODY)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertEqual([item['reason'] for item in final['issues']
                          if item['severity'] == 'blocker'], [core['reason']])
        self.assertTrue(final['initial_copyedit_reminders'])

    def test_missing_priority_defaults_to_core_without_guessing_from_words(self):
        legacy = issue()
        legacy.pop('editorial_priority')
        legacy.pop('reading_effect')
        self.assertFalse(priority.is_detail(priority.normalize(legacy)))
        final = priority.final_gate(reading({**copy.deepcopy(READY),
            'recommendation': 'revise', 'issues': [legacy]}), BODY)
        self.assertEqual(final['recommendation'], 'revise')

    def test_explicit_detail_requires_a_valid_priority_and_reading_reason(self):
        for changes in ({'editorial_priority': 'minor_if_contains_money'},
                        {'reading_effect': ''}, {'reading_effect': None}):
            with self.subTest(changes=changes), self.assertRaises(StoryError):
                priority.normalize({**issue(), **changes})

    def test_projection_is_idempotent_and_does_not_duplicate_reminders(self):
        first = priority.final_gate(reading({**copy.deepcopy(READY),
            'recommendation': 'revise', 'issues': [issue()]}), BODY)
        self.assertEqual(priority.final_gate(first, BODY), first)


class EditorialPriorityClaimAndClosureTests(unittest.TestCase):
    def claim_reading(self, status, *, detail=True):
        claim = {'kind': 'constraint', 'subject': '海外录像带签字授权',
            'statement': '签署电影海外录像带权的各方拥有完整授权。',
            'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': ['p1'],
            'outcome_ids': ['p1'] if status == 'conflict' else [],
            'status': status, 'reason': HONGKONG_RIGHTS_REASON,
            'editorial_priority': 'copyedit_detail' if detail else 'reading_core',
            'reading_effect': ('未展示授权身份不影响已经清楚发生的交易和发财回报。' if detail else
                               '正文先明确交易失败，又以同一交易已成功完成作为高潮唯一依据。')}
        raw = {**copy.deepcopy(READY), 'claims': [claim]}
        return reading(raw), raw

    def plan(self, normalized):
        return claim_audit.build_audit_plan(BODY, [normalized], max_prompt_chars=30000,
                                           render_prompt=lambda data: json.dumps(data, ensure_ascii=False))

    def test_secondary_pending_and_conflict_remain_recorded_without_blocking(self):
        for status in ('pending', 'conflict'):
            with self.subTest(status=status):
                normalized, raw = self.claim_reading(status)
                before = copy.deepcopy(raw)
                self.assertTrue(priority.is_detail(normalized['claims'][0]))
                checked = claim_audit.final_gate(normalized, self.plan(normalized))
                final = priority.final_gate(checked, BODY)
                self.assertEqual(final['recommendation'], 'ready')
                self.assertFalse(any(item['severity'] == 'blocker' for item in final['issues']))
                self.assertEqual(final['claims'][0]['status'], status)
                self.assertEqual(final['claim_gate']['claim_checks'][0]['status'], status)
                self.assertTrue(final['initial_copyedit_reminders'])
                self.assertEqual(raw, before)

    def test_core_pending_and_conflict_cannot_use_the_secondary_exception(self):
        for status, recommendation in (('pending', 'uncertain'), ('conflict', 'revise')):
            with self.subTest(status=status):
                normalized, _ = self.claim_reading(status, detail=False)
                checked = claim_audit.final_gate(normalized, self.plan(normalized))
                final = priority.final_gate(checked, BODY)
                self.assertEqual(final['recommendation'], recommendation)
                self.assertEqual(final['claims'][0]['status'], status)

    def test_retained_old_secondary_objection_does_not_become_a_new_blocker(self):
        initial = reading({**copy.deepcopy(READY), 'recommendation': 'revise',
                           'issues': [issue()]})
        readings = [initial, reading(copy.deepcopy(READY))]
        registry = editorial_closure.reading_registry(readings)
        self.assertEqual(len(registry), 1)
        self.assertTrue(priority.is_detail(registry[0]))
        raw = {**copy.deepcopy(READY), 'issue_dispositions': [{
            'issue_id': registry[0]['issue_id'], 'status': 'retained',
            'evidence_ids': ['p0', 'p1'], 'reason': HONGKONG_RIGHTS_REASON}]}
        normalized = reading(raw)
        closed = editorial_closure.final_gate(normalized, BODY, readings)
        final = priority.final_gate(closed, BODY)
        self.assertTrue(final['editorial_closure']['complete'])
        self.assertEqual(final['recommendation'], 'ready')
        self.assertFalse(any(item['severity'] == 'blocker' for item in final['issues']))
        self.assertTrue(final['initial_copyedit_reminders'])

    def test_retained_pacing_objection_stays_blocking_even_when_it_mentions_accounts(self):
        core = issue(detail=False, reason='读者已经知道结算成功，但结尾连续报账拖迟女主公开关系。')
        initial = reading({**copy.deepcopy(READY), 'recommendation': 'revise', 'issues': [core]})
        readings = [initial, reading(copy.deepcopy(READY))]
        registry = editorial_closure.reading_registry(readings)
        raw = {**copy.deepcopy(READY), 'issue_dispositions': [{
            'issue_id': registry[0]['issue_id'], 'status': 'retained',
            'evidence_ids': ['p1', 'p2'], 'reason': core['reason']}]}
        final = priority.final_gate(editorial_closure.final_gate(reading(raw), BODY, readings), BODY)
        self.assertEqual(final['recommendation'], 'revise')
        self.assertTrue(any(item['severity'] == 'blocker' for item in final['issues']))

    def test_unanswered_secondary_objection_does_not_require_another_final_pass(self):
        initial = reading({**copy.deepcopy(READY), 'recommendation': 'revise',
                           'issues': [issue()]})
        readings = [initial, reading(copy.deepcopy(READY))]
        raw = {**copy.deepcopy(READY), 'issue_dispositions': []}
        final = priority.final_gate(editorial_closure.final_gate(reading(raw), BODY, readings), BODY)
        self.assertTrue(final['editorial_closure']['complete'])
        self.assertEqual(final['recommendation'], 'ready')


class EditorialPriorityReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = StoryProject(Path(temporary.name) / 'story')
        self.project.create(StoryBrief.from_dict({'title': '海外录像带与她', 'format': 'micro_story',
            'channels': ['fanqie_short'], 'length': {'min': 1, 'target': 100},
            'author_input': {'premise': '他实际赚到钱，她在记者面前主动承认关系。'}}))
        self.project.save_draft(BODY)
        _, self.body = self.project.active_draft()
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('short_fiction_agent.story_studio.quality.model_research_context',
              return_value={'common': {'rule': '测试来源：不扩写与阅读无关的手续。'}}).start()

    def client(self, *, core=False):
        def respond(task, prompt):
            self.calls.append(task)
            if task in {'chief_editor_review', 'chief_editor_publication'}:
                items = [issue()]
                if core:
                    items.append(issue(detail=False, reason=(
                        '版权和金额已说清，却连续报账，主角一直没向女主提出关系选择。')))
                return narrative_response(prompt, {**copy.deepcopy(READY),
                    'recommendation': 'revise', 'issues': items})
            return narrative_response(prompt)
        return respond

    def test_full_review_and_current_review_replay_accept_only_secondary_details(self):
        report = StoryReviewer(self.project, self.client()).review()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(report['chief_editor']['response']['recommendation'], 'ready')
        self.assertTrue(report['chief_editor']['response']['initial_copyedit_reminders'])
        self.assertEqual(current_review(self.project)['review_id'], report['review_id'])
        self.assertEqual(review_decision(self.body, self.project.contract(), report)['decision'], 'passed')
        called = len(self.calls)
        StoryReviewer(self.project, lambda *_: self.fail('Exact completed source resent')).review()
        self.assertEqual(len(self.calls), called)

    def test_core_reading_issue_survives_alongside_secondary_reminders(self):
        report = StoryReviewer(self.project, self.client(core=True)).review()
        self.assertEqual(report['decision'], 'revise_required')
        blockers = [item for item in report['issues'] if item['severity'] == 'blocker']
        self.assertTrue(blockers)
        self.assertTrue(all(not priority.is_detail(item) for item in blockers))
        with self.assertRaises(StoryError):
            current_review(self.project)

    def test_normalized_priority_cannot_be_forged_by_rehashing_outer_review(self):
        report = StoryReviewer(self.project, self.client()).review()
        forged = copy.deepcopy(report)
        final = forged['chief_editor']['response']
        final['initial_copyedit_reminders'][0]['editorial_priority'] = 'reading_core'
        forged['chief_editor']['record_hash'] = digest({key: value for key, value in
            forged['chief_editor'].items() if key != 'record_hash'})
        with self.assertRaises(StoryError):
            review_decision(self.body, self.project.contract(), forged)

    def test_publication_preparation_cannot_restore_copyright_detail_as_blocker(self):
        StoryReviewer(self.project, self.client()).review()
        result = chief_preparation(self.project, self.client(), channel='fanqie_short',
            titles=['我买下电影版权，她买走我的心'], synopsis='他靠预知赚到钱，她公开承认关系。')
        self.assertEqual(result['decision'], 'editorial_ready')
        self.assertTrue(validate_chief_preparation(self.project, result, self.body))
        self.assertEqual(result['metadata_response']['recommendation'], 'ready')

    def test_publication_still_rejects_core_reading_problem_with_the_same_vocabulary(self):
        StoryReviewer(self.project, self.client()).review()
        result = chief_preparation(self.project, self.client(core=True), channel='fanqie_short',
            titles=['我买下电影版权，她买走我的心'], synopsis='他靠预知赚到钱，她公开承认关系。')
        self.assertEqual(result['decision'], 'needs_revision')
        self.assertFalse(validate_chief_preparation(self.project, result, self.body))
        self.assertTrue(any(item['severity'] == 'blocker'
                            for item in result['metadata_response']['issues']))

    def test_publication_priority_projection_cannot_be_rebound_with_a_new_outer_hash(self):
        StoryReviewer(self.project, self.client()).review()
        result = chief_preparation(self.project, self.client(), channel='fanqie_short',
            titles=['我买下电影版权，她买走我的心'], synopsis='他靠预知赚到钱，她公开承认关系。')
        forged = copy.deepcopy(result)
        forged['metadata_response']['initial_copyedit_reminders'][0][
            'editorial_priority'] = 'reading_core'
        forged['record_hash'] = digest({key: value for key, value in forged.items() if key != 'record_hash'})
        with self.assertRaises(StoryError):
            validate_chief_preparation(self.project, forged, self.body)

    def legacy_report(self, *, detail=True, severity='warning'):
        contract = self.project.contract()
        rows = chief.paragraphs(self.body)
        issues = []
        for row in rows[:3]:
            item = issue(detail=detail, evidence_ids=[row['id']])
            item.update(severity=severity, category='coherence',
                        quote=row['text'], span=row['span'])
            item['id'] = digest(item)[:32]
            issues.append(item)
        return {'content_hash': digest(self.body), 'contract_hash': digest(contract),
            'review_rubric_hash': review_rubric_hash(),
            'review_coverage': {'complete': True, 'spans': [[0, len(self.body)]]},
            'submission_guide': {**submission_guide.submission_guide_context(contract, 'review'),
                                 'content_hash': digest(self.body)},
            'dimensions': {key: 3 for key in DIMENSIONS},
            'dimension_evidence': {key: '测试桩：协议控制。' for key in DIMENSIONS},
            'issues': issues}

    def test_many_secondary_warnings_do_not_form_a_blocking_cluster(self):
        for severity in ('warning', 'blocker'):
            with self.subTest(severity=severity):
                report = self.legacy_report(severity=severity)
                before = copy.deepcopy(report)
                decision = review_decision(self.body, self.project.contract(), report)
                self.assertEqual(decision['decision'], 'passed')
                self.assertNotIn('coherence_warning_cluster', decision['blockers'])
                self.assertEqual(report, before)

    def test_primary_consistency_warnings_still_use_existing_cluster_gate(self):
        report = self.legacy_report(detail=False)
        decision = review_decision(self.body, self.project.contract(), report)
        self.assertEqual(decision['decision'], 'revise_required')
        self.assertIn('coherence_warning_cluster', decision['blockers'])

    def test_secondary_payoff_warnings_cannot_block_through_closed_ending_cluster(self):
        contract = self.project.contract()
        contract['ending_mode'] = 'closed'
        report = self.legacy_report()
        report['contract_hash'] = digest(contract)
        for item in report['issues']:
            item['category'] = 'payoff'
        decision = review_decision(self.body, contract, report)
        self.assertEqual(decision['decision'], 'passed')
        self.assertNotIn('payoff_warning_cluster', decision['blockers'])


if __name__ == '__main__':
    unittest.main()
