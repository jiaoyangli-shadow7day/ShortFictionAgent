"""Source and routing controls for whole-story expansion review.

These tests do not score fiction or establish that a model's judgment is right.
"""
import copy
import unittest

from short_fiction_agent.story_studio import scene_planning
from short_fiction_agent.story_studio.contracts import StoryError, digest
from tests.test_story_scene_purpose_budget import example_plan, review_control


class WholeStoryReadingAllocationTests(unittest.TestCase):
    def setUp(self):
        self.plan = example_plan()
        self.response = review_control(self.plan)

    def check(self):
        return self.response['reading_allocation_check']

    def needs_replan(self):
        self.check()['status'] = 'needs_replan'
        self.response.update(decision='revise', issues=[{
            'path': 'plan.scenes[1].budget', 'reason': '协议控制：需比较具体展开取舍。',
            'missing_link': '改变篇幅分配而不是补一段解释。'}])

    def test_old_scene_and_causality_pass_without_whole_story_review_is_rejected(self):
        self.response.pop('reading_allocation_check')
        with self.assertRaisesRegex(StoryError, '全篇展开取舍'):
            scene_planning.validate_review(self.response, self.plan)

    def test_new_review_reads_legacy_plan_without_inventing_or_mutating_fields(self):
        before = copy.deepcopy(self.plan)
        scene_planning.validate_review(self.response, self.plan)
        self.assertEqual(self.plan, before)
        self.assertNotIn('core_reading_experience', self.plan)
        self.assertNotIn('reading_role', self.plan['scenes'][1])

    def test_core_dramatic_scene_can_be_support_and_quiet_response_can_be_core_experience(self):
        self.plan['core_reading_experience'] = '对方用实际行动选择同行，不靠口头保证。'
        for scene, role in zip(self.plan['scenes'], ('support', 'core_experience', 'core_experience')):
            scene['reading_role'] = role
        # A dramatic role and a reading role describe different things.
        self.plan['scenes'][0]['scene_role'] = 'core'
        response = review_control(self.plan)
        self.assertEqual(scene_planning.validate_review(response, self.plan), response)
        self.assertEqual(response['scene_checks'][0]['kind'], 'core')
        self.assertEqual(response['reading_allocation_check']['expansion_choices'][0]['reading_role'], 'support')

    def test_budget_and_first_reward_position_are_bound_to_actual_plan(self):
        edits = [
            ('total_budget', 3999, '实际预算'),
            ('total_budget', True, '实际预算'),
        ]
        for key, value, message in edits:
            with self.subTest(value=value):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check'][key] = value
                with self.assertRaisesRegex(StoryError, message):
                    scene_planning.validate_review(response, self.plan)
        self.check()['first_reward']['budget_before'] = 0
        with self.assertRaisesRegex(StoryError, '实际场景累计预算'):
            scene_planning.validate_review(self.response, self.plan)

    def test_expansion_budget_cannot_omit_support_cost(self):
        self.check()['expansion_choices'][0]['planned_budget'] = 1
        with self.assertRaisesRegex(StoryError, '对应实际场景预算'):
            scene_planning.validate_review(self.response, self.plan)

    def test_ordered_partition_rejects_unknown_duplicate_missing_and_distant_scene_groups(self):
        for keys in (['invented'], ['arrival', 'arrival'], ['arrival', 'payoff'], ['response', 'arrival']):
            with self.subTest(keys=keys):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['expansion_choices'][0]['scene_ids'] = keys
                with self.assertRaisesRegex(StoryError, '实际顺序连续覆盖'):
                    scene_planning.validate_review(response, self.plan)
        self.check()['expansion_choices'].pop()
        with self.assertRaisesRegex(StoryError, '覆盖全部实际场景'):
            scene_planning.validate_review(self.response, self.plan)

    def test_first_reward_quote_cannot_come_from_different_scene_or_future_invention(self):
        for quote in (self.plan['scenes'][-1]['consequence'], '两人下周结婚并搬到一起。'):
            with self.subTest(quote=quote):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['first_reward']['quote'] = quote
                with self.assertRaisesRegex(StoryError, '回报引用'):
                    scene_planning.validate_review(response, self.plan)

    def test_internal_reading_role_label_is_not_scene_reward_evidence(self):
        self.plan['scenes'][1]['reading_role'] = 'core_experience'
        self.check()['first_reward']['quote'] = 'core_experience'
        with self.assertRaisesRegex(StoryError, '回报引用'):
            scene_planning.validate_review(self.response, self.plan)

    def test_malformed_allocation_types_fail_as_story_error_without_dispatch(self):
        for field, value in (('action', ['keep']), ('reading_role', {'core_experience': True})):
            with self.subTest(field=field):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['expansion_choices'][0][field] = value
                with self.assertRaisesRegex(StoryError, '具体对照'):
                    scene_planning.validate_review(response, self.plan)
        self.check()['first_reward'].pop('scene_id')
        with self.assertRaisesRegex(StoryError, '首个实质回报的位置'):
            scene_planning.validate_review(self.response, self.plan)

    def test_no_reward_is_explicit_replan_instead_of_a_fabricated_pass(self):
        self.check()['first_reward'].update(scene_id=None, quote='', budget_before=4000, action='replace')
        with self.assertRaisesRegex(StoryError, '缺少回报'):
            scene_planning.validate_review(self.response, self.plan)
        self.check()['status'] = 'needs_replan'
        with self.assertRaisesRegex(StoryError, '退回规划'):
            scene_planning.validate_review(self.response, self.plan)
        self.needs_replan()
        scene_planning.validate_review(self.response, self.plan)

    def test_concrete_cut_or_advance_cannot_coexist_with_effective_pass(self):
        for action in ('compress', 'advance', 'replace'):
            with self.subTest(action=action):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['expansion_choices'][1]['action'] = action
                with self.assertRaisesRegex(StoryError, '仍声称方案有效'):
                    scene_planning.validate_review(response, self.plan)
        self.check()['first_reward']['action'] = 'advance'
        with self.assertRaisesRegex(StoryError, '仍声称方案有效'):
            scene_planning.validate_review(self.response, self.plan)
        self.needs_replan()
        scene_planning.validate_review(self.response, self.plan)

    def test_merge_comparison_covers_actual_adjacent_scenes_and_requires_replan(self):
        first, second, last = self.check()['expansion_choices']
        merged = dict(first, scene_ids=['arrival', 'response'], reading_role='mixed',
                      planned_budget=3000, action='merge',
                      alternative='在门口递票，合并等待与碰面，保留对方当面改签的实际回应。')
        merged['compression_trial'] = copy.deepcopy(first['compression_trial'])
        merged['compression_trial']['retained_beats'] += second['compression_trial']['retained_beats']
        self.check()['expansion_choices'] = [merged, last]
        self.needs_replan()
        scene_planning.validate_review(self.response, self.plan)
        self.check()['expansion_choices'] = [dict(first, action='merge'), second, last]
        with self.assertRaisesRegex(StoryError, '至少两个实际场景'):
            scene_planning.validate_review(self.response, self.plan)

    def test_comparisons_cannot_be_omitted_even_when_every_scene_has_causal_evidence(self):
        for field in ('alternative', 'preserved_value', 'lost_value', 'reason'):
            with self.subTest(field=field):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['expansion_choices'][1].pop(field)
                with self.assertRaisesRegex(StoryError, '具体对照'):
                    scene_planning.validate_review(response, self.plan)
        for field in ('sequence_comparison', 'whole_story_tradeoff'):
            with self.subTest(field=field):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check'].pop(field)
                with self.assertRaisesRegex(StoryError, '全篇展开取舍'):
                    scene_planning.validate_review(response, self.plan)

    def test_structured_comparisons_and_wait_lines_preserve_the_original_reply(self):
        self.check()['sequence_comparison'] = {
            'status': 'effective', 'comparison': '递票与接票分别承担邀请和回应。',
            'result': '保留实际接招，不重复展开等雨过程。'}
        self.check()['whole_story_tradeoff'] = {
            'status': 'effective', 'comparison': '等待只需交代，改签需要现场交锋。',
            'first_reward_tradeoff': '前置预算不等于本场内等待。',
            'result': ['保留邀请与回应。', '后续照顾给出不同体验。']}
        self.check()['first_reward']['within_scene_wait']['before_reward'] = [
            '先到门口递票。', '对方接票后改签。']
        original = copy.deepcopy(self.response)
        result = scene_planning.validate_review(self.response, self.plan)
        self.assertIs(result, self.response)
        self.assertEqual(result, original)

    def test_structured_replan_findings_cannot_hide_under_effective_allocation(self):
        for field in ('sequence_comparison', 'whole_story_tradeoff'):
            for finding in (
                    {'status': 'needs_replan', 'comparison': '换物件但仍重复能力证明。',
                     'result': '需要改变场景方法。'},
                    {'status': 'effective', 'comparison': {
                        'status': 'needs_replan', 'reason': '同类办事重复，需替换胜法。'},
                     'result': '其余场景可以保留。'}):
                with self.subTest(field=field, finding=finding):
                    response = copy.deepcopy(self.response)
                    response['reading_allocation_check'][field] = finding
                    # All scene/actions say keep. The negative comparison must
                    # still prevent an effective allocation from passing.
                    with self.assertRaisesRegex(StoryError, '仍声称方案有效'):
                        scene_planning.validate_review(response, self.plan)

    def test_structured_replan_stays_a_repair_route_with_grounded_issues(self):
        self.check()['sequence_comparison'] = {
            'status': 'needs_replan', 'comparison': '等待和说明重复。',
            'result': '压缩等待，保留改签接招。'}
        self.check()['status'] = 'needs_replan'
        with self.assertRaisesRegex(StoryError, '退回规划'):
            scene_planning.validate_review(self.response, self.plan)
        self.needs_replan()
        original = copy.deepcopy(self.response)
        scene_planning.validate_review(self.response, self.plan)
        self.assertEqual(self.response, original)
        self.response['issues'][0]['path'] = 'plan.scenes[99].budget'
        with self.assertRaisesRegex(StoryError, '真实方案'):
            scene_planning.validate_review(self.response, self.plan)

    def test_malformed_structured_text_and_wait_lines_are_not_equivalent_prose(self):
        for invalid in ({}, {'status': 'effective'}, {'status': ['needs_replan'], 'reason': '原因。'},
                        {'status': 'pass', 'comparison': '对照。'},
                        {'comparison': ''}, {'comparison': ['保留。', 1]}):
            with self.subTest(comparison=invalid):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['sequence_comparison'] = invalid
                with self.assertRaisesRegex(StoryError, '全篇展开取舍'):
                    scene_planning.validate_review(response, self.plan)
        for invalid in ([], [''], ['有效过程。', ' '], ['有效过程。', 1], {'step': '递票。'}):
            with self.subTest(before_reward=invalid):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['first_reward']['within_scene_wait']['before_reward'] = invalid
                with self.assertRaisesRegex(StoryError, '场内等待'):
                    scene_planning.validate_review(response, self.plan)

    def test_new_plan_annotations_must_be_complete_and_source_bound(self):
        self.plan['core_reading_experience'] = '通过实际回应确认同行。'
        with self.assertRaisesRegex(StoryError, '每场标注reading_role'):
            scene_planning.validate_budgets(self.plan, {})
        for scene in self.plan['scenes']:
            scene['reading_role'] = 'core_experience'
        response = review_control(self.plan)
        response['reading_allocation_check']['expansion_choices'][0]['reading_role'] = 'support'
        with self.assertRaisesRegex(StoryError, '阅读角色不属于'):
            scene_planning.validate_review(response, self.plan)
        self.plan['scenes'][0]['reading_role'] = 'unimportant'
        with self.assertRaisesRegex(StoryError, 'reading_role必须区分'):
            scene_planning.validate_budgets(self.plan, {})

    def test_late_reward_has_no_numerical_rejection_threshold(self):
        for scene, budget in zip(self.plan['scenes'], (20000, 9000, 1000)):
            scene['budget'] = budget
        response = review_control(self.plan)
        check = response['reading_allocation_check']
        check['first_reward'].update(scene_id='payoff', quote=self.plan['scenes'][-1]['consequence'],
                                     budget_before=29000)
        # This scripted judgment is not a positive literary benchmark; the
        # validator only confirms that the model really considered this wait.
        scene_planning.validate_review(response, self.plan)

    def test_new_criteria_invalidates_preexisting_pass_receipt(self):
        old_hash = digest({'protocol': 'scene-purpose-and-budget-v4-required-main-question',
                          'plan': scene_planning.PLAN_INSTRUCTION,
                          'review': scene_planning.REVIEW_INSTRUCTION})
        self.assertNotEqual(scene_planning.criteria_hash(), old_hash)
        self.assertIn('不能只写“有因果作用所以保留”', scene_planning.REVIEW_INSTRUCTION)
        self.assertIn('不指定早吻、固定百分比或冲突次数', scene_planning.PLAN_INSTRUCTION)

    def test_first_scene_reward_still_requires_intra_scene_wait_review(self):
        # The old receipt could call this zero waiting merely because scene 0
        # contained the eventual result. It now has to assess its long setup.
        self.plan['scenes'][0]['budget'] = 4000
        response = review_control(self.plan)
        reward = response['reading_allocation_check']['first_reward']
        reward.update(scene_id='arrival', quote=self.plan['scenes'][0]['consequence'], budget_before=0)
        reward['within_scene_wait'] = {
            'before_reward': '先到门口、等雨、反复揣测取消约会的原因，末尾才碰面。',
            'estimate_chars': 3500, 'basis': '计划将碰面放在本场末尾，3500是展开估计而非实际正文定位。'}
        scene_planning.validate_review(response, self.plan)
        reward.pop('within_scene_wait')
        with self.assertRaisesRegex(StoryError, '场内等待'):
            scene_planning.validate_review(response, self.plan)

    def test_unknown_wait_is_explicit_and_invalid_estimates_are_rejected(self):
        # Existing helper explicitly uses null when the plan cannot locate the
        # payoff. It must not turn that uncertainty into a made-up zero.
        scene_planning.validate_review(self.response, self.plan)
        for invalid in (-1, True, 2801, '200'):
            with self.subTest(estimate=invalid):
                response = copy.deepcopy(self.response)
                response['reading_allocation_check']['first_reward']['within_scene_wait']['estimate_chars'] = invalid
                with self.assertRaisesRegex(StoryError, '场内等待估计'):
                    scene_planning.validate_review(response, self.plan)

    def test_result_summary_is_not_a_complete_compression_comparison(self):
        row = self.check()['expansion_choices'][1]
        row['alternative'] = '一句话交代他愿意留下；这种摘要不如原稿，所以保留全部2800字。'
        row.pop('compression_trial')
        with self.assertRaisesRegex(StoryError, '保留必要交锋的压缩试验'):
            scene_planning.validate_review(self.response, self.plan)

    def test_compression_cannot_remove_response_or_borrow_other_scene(self):
        for field, value, message in (
                ('response_quote', '', '不能删掉核心交锋'),
                ('response_quote', self.plan['scenes'][2]['consequence'], '实际回应'),
                ('action_quote', '他捐了一百万元', '实际行动')):
            with self.subTest(field=field, value=value):
                response = copy.deepcopy(self.response)
                beat = response['reading_allocation_check']['expansion_choices'][1]['compression_trial']['retained_beats'][0]
                beat[field] = value
                with self.assertRaisesRegex(StoryError, message):
                    scene_planning.validate_review(response, self.plan)

    def test_each_compression_trial_must_cover_its_actual_group_in_order(self):
        trial = self.check()['expansion_choices'][1]['compression_trial']
        trial['retained_beats'] = self.check()['expansion_choices'][0]['compression_trial']['retained_beats']
        with self.assertRaisesRegex(StoryError, '本组顺序覆盖'):
            scene_planning.validate_review(self.response, self.plan)

    def test_protocol_requires_fair_compression_and_cross_scene_reader_function(self):
        self.assertIn('相同的必要行动、实际接招和后果', scene_planning.PLAN_INSTRUCTION)
        self.assertIn('事件不同、每场都能找出新增事实', scene_planning.REVIEW_INSTRUCTION)
        self.assertIn('keep也必须经过此对照', scene_planning.REVIEW_INSTRUCTION)
        self.assertIn('只有回报在开场直接发生才能估为0', scene_planning.REVIEW_INSTRUCTION)


if __name__ == '__main__':
    unittest.main()
