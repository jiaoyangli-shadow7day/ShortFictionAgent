"""Original promise binding and adverse routing, not literary calibration."""
import copy
import unittest

from short_fiction_agent.story_studio import scene_planning
from short_fiction_agent.story_studio.contracts import StoryError
from test_story_chapter_plan_review import chapter_plan
from test_story_scene_purpose_budget import review_control


class OriginalPlanEntryPayoffTests(unittest.TestCase):
    def setUp(self):
        self.source = chapter_plan()
        self.view = scene_planning.review_view(self.source)
        self.contract = {
            'channels': ['fanqie_short'], 'title': '这次我能走到她身边吗',
            'reader_promise': '预测天气为旅行争取新的选择，也亲历她的主动偏爱。',
            'audience': {'expectations': ['预测优势带来可见收益', '她主动选择与我同行']},
            'author_input': {'premise': '有限预报能看见历史天气，不能改变已经发生的雨。',
                             'entry_facts': ['只有一次提前查询的机会。'],
                             'locked_facts': ['这项私人限制不能冒充读者入口。']},
            'constraints': {'avoid': ['不要增加反派。']}}

    def valid_response(self):
        result = review_control(self.view, self.contract)
        # The new protocol does not require the old aggregate essay to be
        # repeated after every independent original promise has been checked.
        result['premise_test'].pop('reason')
        result['premise_test'].pop('without_setting_changes')
        result['premise_test'].pop('evidence')
        return result

    def validate(self, response, contract=None):
        return scene_planning.validate_review(response, self.source, evidence_plan=self.view,
                                              contract=self.contract if contract is None else contract)

    def test_fanqie_production_cannot_use_only_aggregate_setting_rationale(self):
        with self.assertRaisesRegex(StoryError, '独立卖点'):
            self.validate(review_control(self.view))
        response = self.valid_response()
        self.assertEqual(self.validate(response), response)

    def test_weaker_facts_cannot_replace_any_explicit_original_expectation(self):
        for index in range(len(self.contract['audience']['expectations'])):
            with self.subTest(missing_expectation=index):
                response = self.valid_response()
                path = f'contract.audience.expectations[{index}]'
                response['premise_test']['checks'] = [
                    row for row in response['premise_test']['checks'] if row['entry_path'] != path]
                before = copy.deepcopy(response)
                with self.assertRaisesRegex(StoryError, r'未覆盖每项非空原始读者期待') as error:
                    self.validate(response)
                self.assertIn(path, str(error.exception))
                self.assertEqual(response, before)
        response = self.valid_response()
        response['premise_test']['checks'] = response['premise_test']['checks'][:1]
        self.assertTrue(response['premise_test']['checks'][0]['entry_path'].startswith(
            'contract.author_input.'))
        with self.assertRaisesRegex(StoryError, '未覆盖每项非空原始读者期待'):
            self.validate(response)

    def test_duplicate_expectation_check_does_not_cover_a_different_expectation(self):
        response = self.valid_response()
        first = next(row for row in response['premise_test']['checks']
                     if row['entry_path'] == 'contract.audience.expectations[0]')
        response['premise_test']['checks'] = [first, copy.deepcopy(first)]
        with self.assertRaisesRegex(StoryError, '未覆盖每项非空原始读者期待'):
            self.validate(response)

    def test_original_indices_and_single_character_expectation_are_preserved(self):
        contract = copy.deepcopy(self.contract)
        contract['audience']['expectations'] = ['', '甜', '  ', None,
                                               '她主动选择与我同行']
        response = review_control(self.view, contract)
        paths = {row['entry_path'] for row in response['premise_test']['checks']}
        self.assertIn('contract.audience.expectations[1]', paths)
        self.assertIn('contract.audience.expectations[4]', paths)
        self.assertEqual(self.validate(response, contract), response)
        # An abbreviated or noncanonical path may resolve to a genuine source,
        # but it cannot silently satisfy an exact explicit-expectation check.
        row = next(row for row in response['premise_test']['checks']
                   if row['entry_path'] == 'contract.audience.expectations[4]')
        row['entry_path'] = 'audience.expectations[4]'
        with self.assertRaisesRegex(StoryError, '未覆盖每项非空原始读者期待'):
            self.validate(response, contract)

    def test_multiple_independent_fragments_in_one_expectation_are_allowed(self):
        contract = copy.deepcopy(self.contract)
        contract['audience']['expectations'][0] = '预测优势带来可见收益，重生记忆带来从容接招'
        response = review_control(self.view, contract)
        first = next(row for row in response['premise_test']['checks']
                     if row['entry_path'] == 'contract.audience.expectations[0]')
        second = copy.deepcopy(first)
        first['entry_quote'] = '预测优势带来可见收益'
        second['entry_quote'] = '重生记忆带来从容接招'
        response['premise_test']['checks'].append(second)
        # Coverage and genuine fragment binding are program checks. This does
        # not claim either scripted payoff is an adequate literary judgment.
        self.assertEqual(self.validate(response, contract), response)

    def test_entry_reference_is_original_positive_contract_never_plan_or_private_constraints(self):
        for path, quote in (
            ('plan.main_question', self.source['main_question']),
            ('contract.constraints.avoid[0]', '不要增加反派'),
            ('contract.author_input.locked_facts[0]', '私人限制'),
            ('contract.audience.expectations[99]', '预测优势'),
            ('contract.title', '原入口根本没有的宣传语')):
            with self.subTest(path=path):
                response = self.valid_response()
                response['premise_test']['checks'][0].update(entry_path=path, entry_quote=quote)
                with self.assertRaisesRegex(StoryError, '原始合同入口'):
                    self.validate(response)

    def test_positive_entry_references_accept_independent_fragments_from_real_fields(self):
        for path, quote in (
            ('contract.title', '走到她身边'),
            ('contract.reader_promise', '亲历她的主动偏爱'),
            ('contract.audience.expectations[0]', '预测优势'),
            ('contract.author_input.premise', '有限预报'),
            ('contract.author_input.entry_facts[0]', '只有一次')):
            with self.subTest(path=path):
                response = self.valid_response()
                response['premise_test']['checks'][0].update(entry_path=path, entry_quote=quote)
                self.assertEqual(self.validate(response), response)

    def test_one_unfulfilled_sellpoint_cannot_borrow_another_gain_or_overall_pass(self):
        response = self.valid_response()
        failed = copy.deepcopy(response['premise_test']['checks'][0])
        failed.update(entry_path='contract.audience.expectations[0]', entry_quote='预测优势',
                      actual_payoff='仅说明不能预测，没有提供本项承诺的优势收益。',
                      remaining_effect='boundary_only', status='needs_replan')
        response['premise_test']['checks'].append(failed)
        with self.assertRaisesRegex(StoryError, '独立卖点发现缺口'):
            self.validate(response)
        response['premise_test']['status'] = 'needs_replan'
        with self.assertRaisesRegex(StoryError, '退回规划'):
            self.validate(response)
        response.update(decision='revise', issues=[{'path': 'plan.scenes',
            'reason': '本项承诺的优势被另一主题替代。', 'missing_link': '重排该项具体收益和后果。'}])
        self.assertEqual(self.validate(response), response)

    def test_qualitative_remaining_effect_is_not_a_use_count_or_automatic_rejection(self):
        for effect in ('material', 'boundary_only', 'no_later_effect', 'not_applicable'):
            with self.subTest(effect=effect):
                response = self.valid_response()
                row = response['premise_test']['checks'][0]
                row.update(entry_path='contract.author_input.entry_facts[0]', entry_quote='只有一次',
                    expected_payoff='一次性机会的实际后果；不承诺往后继续查询。',
                    actual_payoff='传输控制：引用双方已发生的行动，不证明真实文学收益。',
                    remaining_effect=effect)
                self.assertEqual(self.validate(response), response)

    def test_new_checks_cannot_cite_hidden_planner_analysis_or_another_scene(self):
        self.source['scenes'][0]['purpose'] = '未传入主审的自评：查询作用十分充分。'
        response = self.valid_response()
        for scene_id, quote in (('arrival', self.source['scenes'][0]['purpose']),
                                 ('imagined', self.source['scenes'][0]['choice']),
                                 ('arrival', self.source['scenes'][2]['consequence'])):
            with self.subTest(scene_id=scene_id, quote=quote):
                response['premise_test']['checks'][0]['evidence'] = [{'scene_id': scene_id, 'quote': quote}]
                with self.assertRaisesRegex(StoryError, '引用不属于'):
                    self.validate(response)

    def test_missing_comparison_and_unsupported_status_cannot_be_silently_filled(self):
        for field, replacement in (('expected_payoff', ''), ('actual_payoff', ''),
                                   ('without_remaining', ''), ('remaining_effect', 'used_all_ten'),
                                   ('status', 'active')):
            with self.subTest(field=field):
                response = self.valid_response()
                response['premise_test']['checks'][0][field] = replacement
                before = copy.deepcopy(response)
                with self.assertRaisesRegex(StoryError, '独立卖点缺少'):
                    self.validate(response)
                self.assertEqual(response, before)

    def test_non_fanqie_or_old_public_api_keeps_source_only_premise_schema(self):
        legacy = review_control(self.view)
        self.assertEqual(scene_planning.validate_review(legacy, self.source, evidence_plan=self.view), legacy)
        generic = {**self.contract, 'channels': ['generic_text']}
        self.assertEqual(self.validate(legacy, generic), legacy)


if __name__ == '__main__':
    unittest.main()
