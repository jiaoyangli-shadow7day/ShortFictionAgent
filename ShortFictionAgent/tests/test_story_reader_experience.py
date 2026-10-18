"""Evidence/schema regressions; semantic attraction is checked by live calibration."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import reader_experience as reader
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import DIMENSIONS, StoryReviewer, current_review, review_decision, review_rubric_hash
from short_fiction_agent.story_studio.research_guidance import (
    model_research_context, prose_expression_library, research_context,
)
from short_fiction_agent.story_studio.storage import StoryProject, read_json

DATA = Path(__file__).resolve().parents[1] / 'short_fiction_agent/story_studio/data'
CALIBRATION = json.loads((DATA / 'prose_reader_calibration.json').read_text())


def reader_response(body, *, verdict='continue', kind='relationship', necessity='needed', strength='sufficient'):
    """A schema fixture, not a claim that the model accepts or rejects this prose."""
    result = []
    for window in reader.windows(body):
        paragraphs = window['paragraphs']
        first, last = paragraphs[0]['paragraph_index'], paragraphs[-1]['paragraph_index']
        result.append({'window_id': window['window_id'], 'verdict': verdict,
            'reason': '测试桩：对方回应眼前选择，必要信息可在原文定位', 'fix_intent': '保留实际回应',
            'question': {'status': 'active', 'text': '对方会怎样回应眼前选择',
                         'evidence_paragraph_indices': [first]},
            'change': {'kind': kind, 'before': '眼前选择尚待回应', 'action': '人物在场作出选择',
                       'response': '另一方实际接招', 'after': '明确了双方的当前理解',
                       'evidence_paragraph_indices': list(dict.fromkeys([first, last]))},
            'pull': {'strength': strength,
                     'reason_to_continue': '测试桩：当前回应给出了具体情感阅读收益',
                     'expected_next_action': '测试桩：期待对方怎样照顾这次回应',
                     'incentive': '测试桩：双方能否感到被理解',
                     'evidence_paragraph_indices': [first]},
            'most_skippable': {'paragraph_index': first, 'pressure': 'description',
                              'necessity': necessity, 'burden': 'tolerable',
                              'reason': '测试桩：这处细节容易被误删',
                              'loss_if_removed': '失去当前反应的来源',
                              'compression': {'replacement': paragraphs[0]['text'],
                                  'interest_effect': 'preserved',
                                  'reason': '传输控制：原样保留本段，无新增事实'}}})
    return {'windows': result}


def grounding_response(body, *, overrides=None):
    """Transport fixture only; source-support semantics require live audit."""
    overrides = overrides or {}
    rows = []
    for window in reader.windows(body):
        first = window['paragraphs'][0]['paragraph_index']
        checks = [{'field': field, 'status': 'supported',
                   'reason': '传输控制：字段与原文核验的测试占位，非真实语义结论',
                   'evidence_paragraph_indices': [first]}
                  for field in reader.GROUNDING_FIELDS]
        for check in checks:
            check.update(overrides.get((window['window_id'], check['field']), {}))
        rows.append({'window_id': window['window_id'], 'checks': checks})
    return {'windows': rows}


class ReaderExperienceTests(unittest.TestCase):
    def setUp(self):
        self.body = '她把杯子推过来。\n“你先喝。”\n我把围巾搭在她椅背，她笑了。'
        self.contract = {'review_mode': 'deep_review', 'format': 'short_story', 'ending_mode': 'closed',
                         'length': {'min': 1, 'target': 100, 'max': 20000}}

    def test_valid_schema_grounds_source_offsets_and_raw_sha(self):
        record = reader.validate(reader_response(self.body), self.body, self.contract)
        self.assertEqual(reader.gate(self.body, self.contract, record), [])
        self.assertEqual(record['source_sha256'], hashlib.sha256(self.body.encode()).hexdigest())
        for row in record['windows']:
            for field in ('question', 'change', 'pull'):
                for item in row[field]['evidence']:
                    self.assertEqual(self.body[slice(*item['span'])], item['text'])
            skip = row['most_skippable']
            self.assertEqual(skip['compression']['replacement'], skip['evidence']['text'])
        self.assertEqual(record['window_sources'], reader.windows(self.body))

    def test_pull_and_compression_require_each_explanation_and_valid_enums(self):
        for field in ('strength', 'reason_to_continue', 'expected_next_action', 'incentive',
                      'evidence_paragraph_indices'):
            with self.subTest(pull_field=field):
                response = reader_response(self.body)
                response['windows'][0]['pull'].pop(field)
                with self.assertRaises(StoryError):
                    reader.validate(response, self.body, self.contract)
        for field in ('replacement', 'interest_effect', 'reason'):
            with self.subTest(compression_field=field):
                response = reader_response(self.body)
                response['windows'][0]['most_skippable']['compression'].pop(field)
                with self.assertRaises(StoryError):
                    reader.validate(response, self.body, self.contract)
        for field, value in (('strength', 'excellent'), ('incentive', '  ')):
            response = reader_response(self.body)
            response['windows'][0]['pull'][field] = value
            with self.assertRaises(StoryError):
                reader.validate(response, self.body, self.contract)
        response = reader_response(self.body)
        response['windows'][0]['most_skippable']['compression']['interest_effect'] = 'shorter'
        with self.assertRaises(StoryError):
            reader.validate(response, self.body, self.contract)

    def test_weak_and_unclear_pull_veto_an_optimistic_verdict_with_located_issue(self):
        for strength in ('weak', 'unclear'):
            with self.subTest(strength=strength):
                response = reader_response(self.body, strength=strength)
                row = response['windows'][0]
                row['pull']['reason_to_continue'] = '只约定了后面的手续，尚未给出我在意的回应'
                row['pull']['incentive'] = '没有证据说明这项手续给双方带来什么当下感受'
                row['pull']['evidence_paragraph_indices'] = [1]
                row['most_skippable']['paragraph_index'] = 0
                record = reader.validate(response, self.body, self.contract)
                self.assertEqual(reader.gate(self.body, self.contract, record),
                                 [f'reader_experience:first_screen:pull:{strength}'])
                issue, = reader.issues(record)
                self.assertIn(f'追读动力{strength}', issue['reason'])
                self.assertIn(row['pull']['incentive'], issue['reason'])
                self.assertEqual(issue['quote'], '“你先喝。”')
                self.assertEqual(self.body[slice(*issue['span'])], issue['quote'])

    def test_generic_praise_without_reader_evidence_cannot_pass(self):
        with self.assertRaises(StoryError):
            reader.validate({'windows': [{'window_id': 'first_screen', 'verdict': 'continue',
                             'reason': '自然流畅，无冗余', 'fix_intent': '保留'}]}, self.body, self.contract)
        response = reader_response(self.body)
        response['windows'][0]['most_skippable'].pop('loss_if_removed')
        with self.assertRaises(StoryError):
            reader.validate(response, self.body, self.contract)

    def test_evidence_cannot_use_another_window_or_unlisted_paragraph(self):
        body = '\n'.join(f'段落{i}：她看着他端来的茶。' * 18 for i in range(20))
        response = reader_response(body)
        self.assertEqual(len(reader.windows(body)), 2)
        response['windows'][0]['question']['evidence_paragraph_indices'] = [reader.windows(body)[1]['paragraphs'][0]['paragraph_index']]
        with self.assertRaisesRegex(StoryError, '越窗'):
            reader.validate(response, body, self.contract)
        for indices in ([True], [0, 0], [], [0, 1, 2, 3], [999]):
            with self.subTest(pull_indices=indices):
                response = reader_response(body)
                response['windows'][0]['pull']['evidence_paragraph_indices'] = indices
                with self.assertRaisesRegex(StoryError, '越窗'):
                    reader.validate(response, body, self.contract)
        response = reader_response(body)
        response['windows'][0]['most_skippable']['paragraph_index'] = True
        with self.assertRaises(StoryError):
            reader.validate(response, body, self.contract)

    def test_missing_duplicate_window_and_conflicting_positive_verdict_fail(self):
        response = reader_response(self.body)
        response['windows'] *= 2
        with self.assertRaises(StoryError):
            reader.validate(response, self.body, self.contract)
        response = reader_response(self.body)
        response['windows'][0]['most_skippable']['burden'] = 'blocks_current_interest'
        with self.assertRaisesRegex(StoryError, '冲突'):
            reader.validate(response, self.body, self.contract)
        response['windows'][0]['verdict'] = 'revise'
        record = reader.validate(response, self.body, self.contract)
        self.assertEqual(reader.gate(self.body, self.contract, record), ['reader_experience:first_screen:revise'])

    def test_saved_evidence_and_changed_body_or_contract_cannot_be_reused(self):
        record = reader.validate(reader_response(self.body), self.body, self.contract)
        for body, contract in ((self.body + '后来。', self.contract),
                               (self.body, {**self.contract, 'tone': '新合同'})):
            with self.assertRaises(StoryError):
                reader.gate(body, contract, record)
        forged = copy.deepcopy(record)
        forged['windows'][0]['most_skippable']['evidence']['text'] = '他承认错误。'
        with self.assertRaisesRegex(StoryError, '位置'):
            reader.gate(self.body, self.contract, forged)
        forged = copy.deepcopy(record)
        forged['windows'][0]['pull']['evidence'][0]['span'] = [0, 1]
        with self.assertRaisesRegex(StoryError, '位置'):
            reader.gate(self.body, self.contract, forged)
        old = copy.deepcopy(record)
        old['protocol'] = 'reader-experience-2026-10-08-v1'
        with self.assertRaisesRegex(StoryError, '绑定'):
            reader.gate(self.body, self.contract, old)

    def test_quiet_affection_professional_details_and_description_have_no_mechanical_veto(self):
        # These checks establish that the code imposes no word/length/conflict
        # veto. Whether a model makes the right judgment is a separate live test.
        positives = [row for row in CALIBRATION['cases'] if row['expected_verdict'] == 'continue']
        self.assertGreaterEqual(len(positives), 4)
        for case in positives:
            response = reader_response(case['body'], kind='understanding', necessity='needed')
            record = reader.validate(response, case['body'], self.contract)
            self.assertEqual(reader.gate(case['body'], self.contract, record), [], case['id'])
        response = reader_response(self.body, necessity='compressible')
        self.assertEqual(reader.gate(self.body, self.contract,
            reader.validate(response, self.body, self.contract)), [])

    def test_settled_warmth_and_compression_do_not_force_a_new_conflict(self):
        response = reader_response(self.body, necessity='compressible')
        row = response['windows'][0]
        row['question'] = {'status': 'settled', 'text': '照顾已得到笑容回应，留下被珍惜的感受',
                           'evidence_paragraph_indices': [2]}
        row['pull']['expected_next_action'] = '已收束，没有新行动要求；当前照顾与笑容留有余味'
        row['pull']['incentive'] = '她的笑容确认已经收到这次照顾'
        for effect in ('improved', 'preserved', 'damaged'):
            with self.subTest(effect=effect):
                row['most_skippable']['compression']['interest_effect'] = effect
                record = reader.validate(response, self.body, self.contract)
                self.assertEqual(reader.gate(self.body, self.contract, record), [])

    def test_full_scores_cannot_override_independent_veto(self):
        for verdict, strength, blocker in (
                ('revise', 'sufficient', 'reader_experience:first_screen:revise'),
                ('continue', 'weak', 'reader_experience:first_screen:pull:weak'),
                ('continue', 'unclear', 'reader_experience:first_screen:pull:unclear')):
            with self.subTest(verdict=verdict, strength=strength):
                record = reader.validate(reader_response(self.body, verdict=verdict, strength=strength),
                                         self.body, self.contract)
                report = {'content_hash': digest(self.body), 'contract_hash': digest(self.contract),
                          'review_rubric_hash': review_rubric_hash(),
                          'review_coverage': {'complete': True, 'spans': [[0, len(self.body)]]},
                          'dimensions': {key: 4 for key in DIMENSIONS},
                          'dimension_evidence': {key: '自然流畅，完全无冗余' for key in DIMENSIONS},
                          'issues': [], 'reader_experience': record,
                          'reader_grounding': reader.validate_grounding(
                              grounding_response(self.body), self.body, self.contract, record)}
                with patch('short_fiction_agent.story_studio.quality.engagement_gate', return_value=[]), \
                     patch('short_fiction_agent.story_studio.quality.narrative_setup.gate', return_value=[]), \
                     patch('short_fiction_agent.story_studio.quality.whole_story.enabled', return_value=False):
                    decision = review_decision(self.body, self.contract, report)
                self.assertEqual(decision['decision'], 'revise_required')
                self.assertIn(blocker, decision['blockers'])

    def test_blind_prompt_excludes_author_defense_plan_scores_and_unread_ending(self):
        body = '未来摘要：作者声称完美。\n第一章\n' + '\n'.join('她递来茶。' * 60 for _ in range(30)) + '\n最终他中了一等奖，证明都合理。'
        contract = {**self.contract, 'title': '泄露标题', 'reader_promise': '最后获得大奖',
                    'author_input': {'premise': '未来他们结婚'}, 'future_plan': '精心策划'}
        prompt = reader.prompt(body, contract)
        for forbidden in ('未来摘要：作者声称完美', '最终他中了一等奖', '最后获得大奖', '未来他们结婚', '泄露标题'):
            self.assertNotIn(forbidden, prompt)
        self.assertIn('最想跳过', prompt)
        for required in ('事前想象', '手续完成', '当前损失', '不能把计划写成已经完成', '已收束',
                         'pull', 'compression', 'weak/unclear会独立阻止通过'):
            self.assertIn(required, prompt)
        shape = json.loads(prompt.split('\n', 1)[1])['schema_example']
        self.assertEqual(shape['verdict'], 'continue|revise|unclear')
        self.assertEqual(shape['pull']['strength'], 'sufficient|weak|unclear')

    def test_weak_manuscript_stops_before_full_review_and_keeps_incomplete_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'review_mode': 'deep_review', 'format': 'short_story', 'opening_mode': 'direct_scene',
                'length': {'min': 1, 'target': 1000, 'max': 2000},
                'author_input': {'premise': '双方照顾彼此', 'locked_facts': []}}))
            project.save_draft(self.body)
            _, saved_body = project.active_draft()
            calls = []
            def call_json(task, prompt):
                calls.append(task)
                if task == 'reader_basis_grounding':
                    return grounding_response(self.body)
                if task != 'reader_experience_review':
                    self.fail('弱稿不应继续昂贵全文审查')
                return reader_response(self.body, verdict='continue', strength='weak')
            reviewer = StoryReviewer(project, call_json)
            report = reviewer.review()
            self.assertEqual(calls, ['reader_experience_review', 'reader_basis_grounding'])
            self.assertEqual(report['decision'], 'revise_required')
            self.assertIn('reader_experience:first_screen:pull:weak', report['blockers'])
            self.assertIs(report['review_coverage']['complete'], False)
            self.assertEqual(report['dimensions'], {})
            self.assertEqual(report['review_stage'], 'reader_experience_preflight')
            self.assertEqual(read_json(project.root / 'reviews' / f"{report['review_id']}.json"), report)
            self.assertEqual(review_decision(saved_body, project.contract(), report)['decision'], 'revise_required')
            self.assertEqual(current_review(project, require_passed=False), report)
            with self.assertRaisesRegex(StoryError, '尚未通过'):
                current_review(project)
            from short_fiction_agent.story_studio.workflow import StoryWorkflow
            flow = StoryWorkflow(project, object())
            edit_calls = []
            def edit_response(task, prompt):
                edit_calls.append((task, prompt))
                return {'back_to_plan': False, 'reason': '按定位修订', 'edits': [{
                    'quote': '她把杯子推过来。', 'replacement': '她把我的杯子推过来，杯柄朝着我。',
                    'kind': 'rewrite', 'benefit': '让照顾具体', 'risk': '保留现有关系事实'}]}
            flow.call_json = edit_response
            edited = flow._edit(saved_body, issues=report['issues'])
            self.assertNotEqual(edited, saved_body)
            self.assertEqual([task for task, _ in edit_calls], ['story_edit'])
            self.assertIn('reader_experience', edit_calls[0][1])
            with self.assertRaises(StoryError):
                reader.gate(edited, project.contract(), report['reader_experience'])
            forged = copy.deepcopy(report)
            forged['review_coverage']['complete'] = True
            with self.assertRaisesRegex(StoryError, '全文通过'):
                review_decision(saved_body, project.contract(), forged)
            forged = copy.deepcopy(report)
            forged['issues'] = []
            with self.assertRaisesRegex(StoryError, '不一致'):
                review_decision(saved_body, project.contract(), forged)

    def test_frozen_case_sources_and_expected_labels_are_explicit(self):
        self.assertIn('待真实模型检验', CALIBRATION['scope'])
        self.assertIn('不是金标准', CALIBRATION['scope'])
        ids = {case['id'] for case in CALIBRATION['cases']}
        self.assertIn('college_resume_boundaries_list', ids)
        self.assertIn('roommate_technical_flirt', ids)
        for case in CALIBRATION['cases']:
            self.assertEqual(hashlib.sha256(case['body'].encode()).hexdigest(), case['source']['excerpt_sha256'])
            self.assertEqual(len(case['source']['sha256']), 64)

    def test_two_matching_expression_cards_reach_prose_prompt_with_sources_and_mechanism(self):
        contract = {'review_mode': 'deep_review', 'format': 'short_story', 'genre': {'primary': 'romance'},
                    'story_engine': 'relationship_choice', 'reader_promise': '成年室友安静甜宠',
                    'author_input': {'premise': '稳定恋爱中的双方主动偏爱'}}
        context = research_context(contract, task='write_story_scene')
        projected = model_research_context(context)['prose_expression_research']
        self.assertEqual(len(projected['evidence']), 2)
        for card in projected['evidence']:
            self.assertIn('source', card)
            self.assertEqual(len(card['source']['sha256']), 64)
            self.assertTrue(card['expression_mechanism'])
            self.assertTrue(card['counterexample_limit'])
            for evidence in card['evidence']:
                self.assertLessEqual(len(evidence['quote']), 48)
                self.assertEqual(len(evidence['quote']), evidence['span'][1] - evidence['span'][0])
        self.assertEqual(research_context({**contract, 'review_mode': 'deep_review', 'format': 'short_serial'},
            task='write_story_scene')['prose_expression_research']['evidence'], [])
        self.assertEqual(research_context(contract, task='story_candidates')['prose_expression_research']['evidence'], [])

    def test_expression_card_quotes_match_bound_source_snapshot_and_inspected_scope(self):
        repository = Path(__file__).resolve().parents[2]
        for card in prose_expression_library()['evidence']:
            source = repository / card['source']['path']
            raw = source.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), card['source']['sha256'])
            body = raw.decode()
            for item in card['evidence']:
                self.assertEqual(body[slice(*item['span'])], item['quote'])
                self.assertTrue(any(a <= item['span'][0] and item['span'][1] <= b
                    for a, b in card['source']['read_ranges']))


if __name__ == '__main__':
    unittest.main()
