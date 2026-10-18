"""Chapter review coverage and repair controls, not literary quality scores."""
import copy
import unittest

from short_fiction_agent.story_studio import scene_planning
from short_fiction_agent.story_studio.contracts import StoryError, digest
from test_story_scene_purpose_budget import example_plan, review_control


def chapter_plan():
    plan = example_plan()
    for scene in plan['scenes']:
        scene['progression_beats'] = [{
            'reader_question': scene['goal'], 'action': scene['choice'],
            'response': scene['consequence'], 'change': scene['new_information'],
            'reward': scene['emotional_change'], 'next_expectation': scene['purpose']}]
    plan['chapters'] = [
        {'chapter_id': 'meeting', 'title': '门口的选择', 'scene_ids': ['arrival', 'response']},
        {'chapter_id': 'together', 'title': '一起走', 'scene_ids': ['payoff']}]
    return plan


class ChapterPlanReviewTests(unittest.TestCase):
    def test_budget_context_is_computed_from_actual_chapter_scenes_without_mutating_plan(self):
        plan = chapter_plan()
        before = copy.deepcopy(plan)
        context = scene_planning.chapter_budget_context(plan)
        self.assertEqual(context, [
            {'chapter_id': 'meeting', 'scene_ids': ['arrival', 'response'], 'planned_budget': 3000},
            {'chapter_id': 'together', 'scene_ids': ['payoff'], 'planned_budget': 1000}])
        context[0]['scene_ids'].append('fabricated')
        self.assertEqual(plan, before)

    def test_missing_duplicate_omitted_and_unknown_chapters_cannot_pass(self):
        plan = chapter_plan()
        for defect in ('missing', 'empty', 'omitted', 'duplicate', 'unknown', 'bad_type'):
            with self.subTest(defect=defect):
                response = review_control(plan)
                if defect == 'missing':
                    response.pop('chapter_checks')
                elif defect == 'empty':
                    response['chapter_checks'] = []
                elif defect == 'omitted':
                    response['chapter_checks'].pop()
                elif defect == 'duplicate':
                    response['chapter_checks'][1] = copy.deepcopy(response['chapter_checks'][0])
                elif defect == 'unknown':
                    response['chapter_checks'][0]['chapter_id'] = 'imagined'
                else:
                    response['chapter_checks'][0]['chapter_id'] = ['meeting']
                before = copy.deepcopy(response)
                with self.assertRaisesRegex(StoryError, '恰好覆盖每个实际chapter_id'):
                    scene_planning.validate_review(response, plan)
                self.assertEqual(response, before)

    def test_current_chapter_evidence_cannot_use_another_chapter_or_invent_a_scene(self):
        plan = chapter_plan()
        for key in ('payoff', 'imagined', ['arrival']):
            with self.subTest(scene_id=key):
                response = review_control(plan)
                response['chapter_checks'][0]['evidence'][0]['scene_id'] = key
                with self.assertRaisesRegex(StoryError, '当前章实际场景'):
                    scene_planning.validate_review(response, plan)

    def test_beat_index_and_quote_must_bind_the_same_actual_beat(self):
        plan = chapter_plan()
        for field, value in (('beat_index', -1), ('beat_index', 1), ('beat_index', True),
                ('beat_index', '0'), ('beat_index', None), ('quote', '原计划没有的接吻'),
                ('quote', plan['scenes'][2]['consequence'])):
            with self.subTest(field=field, value=value):
                response = review_control(plan)
                response['chapter_checks'][0]['evidence'][0][field] = value
                with self.assertRaisesRegex(StoryError, '对应实际progression_beats'):
                    scene_planning.validate_review(response, plan)
        response = review_control(plan)
        response['chapter_checks'][0]['evidence'][0].pop('beat_index')
        with self.assertRaisesRegex(StoryError, '当前章实际场景'):
            scene_planning.validate_review(response, plan)

    def test_status_reason_and_source_evidence_are_required(self):
        plan = chapter_plan()
        for field, value in (('status', 'pass'), ('status', []), ('reason', ''),
                ('reason', None), ('evidence', []), ('evidence', None)):
            with self.subTest(field=field, value=value):
                response = review_control(plan)
                response['chapter_checks'][0][field] = value
                with self.assertRaisesRegex(StoryError, '逐章'):
                    scene_planning.validate_review(response, plan)

    def test_adverse_chapter_check_cannot_pass_or_be_disposed_on_an_unrelated_chapter(self):
        plan = chapter_plan()
        response = review_control(plan)
        response['chapter_checks'][0].update(status='needs_replan',
            reason='第一章的准备重复了同一试探，应合并准备步骤，再留下完整递票回应。')
        response['issues'] = [{'path': 'plan.chapters[0].scene_ids',
            'reason': '重复准备造成等待。', 'missing_link': '保留一次完整交锋，重新安排分章。'}]
        with self.assertRaisesRegex(StoryError, '退回规划'):
            scene_planning.validate_review(response, plan)
        response['decision'] = 'revise'
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['issues'][0]['path'] = 'plan.chapters[1].title'
        with self.assertRaisesRegex(StoryError, '本章真实chapters或scenes路径'):
            scene_planning.validate_review(response, plan)
        response['issues'][0]['path'] = 'plan.chapters[99].scene_ids'
        with self.assertRaisesRegex(StoryError, '本章真实chapters或scenes路径'):
            scene_planning.validate_review(response, plan)
        response['issues'][0]['path'] = 'plan.scenes[1].choice'
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        # Repartitioning the complete map, or replacing the complete route,
        # legitimately touches the current chapter too; no duplicate issue is
        # needed for each chapter affected by the same structural edit.
        for path in ('plan.chapters', 'plan.scenes'):
            response['issues'][0]['path'] = path
            self.assertEqual(scene_planning.validate_review(response, plan), response)

    def test_numeric_and_real_id_paths_resolve_to_the_same_chapter_for_repair(self):
        plan = chapter_plan()
        response = review_control(plan)
        response['decision'] = 'revise'
        response['chapter_checks'][0].update(status='needs_replan',
            reason='当前章递票前的等待步骤重复，应改实际节拍。')
        for path in ('plan.scenes[1].progression_beats',
                'plan.scenes[scene_id=response].progression_beats',
                'scenes[scene_id=response].progression_beats[0].response',
                'plan.chapters[0].scene_ids',
                'plan.chapters[chapter_id=meeting].scene_ids[1]'):
            with self.subTest(path=path):
                response['issues'] = [{'path': path, 'reason': '当前章重复准备。',
                    'missing_link': '改写本章准备与接招的实际分配。'}]
                before = copy.deepcopy(response)
                self.assertTrue(scene_planning._path_exists(path, plan))
                self.assertEqual(scene_planning.validate_review(response, plan), response)
                self.assertEqual(response, before)

    def test_existing_id_selectors_from_another_chapter_cannot_dispose_current_failure(self):
        plan = chapter_plan()
        response = review_control(plan)
        response['decision'] = 'revise'
        response['chapter_checks'][0]['status'] = 'needs_replan'
        for path in ('plan.scenes[2].progression_beats',
                'plan.scenes[scene_id=payoff].progression_beats',
                'plan.chapters[1].scene_ids',
                'plan.chapters[chapter_id=together].scene_ids'):
            with self.subTest(path=path):
                self.assertTrue(scene_planning._path_exists(path, plan))
                response['issues'] = [{'path': path, 'reason': '另章的合法问题。',
                    'missing_link': '不能代替当前章结构修复。'}]
                with self.assertRaisesRegex(StoryError, '本章真实chapters或scenes路径'):
                    scene_planning.validate_review(response, plan)

    def test_unknown_ambiguous_and_malformed_id_selectors_never_get_numeric_fallback(self):
        plan = chapter_plan()
        for path in ('plan.scenes[scene_id=absent].progression_beats',
                'plan.chapters[chapter_id=absent].scene_ids',
                'plan.scenes[chapter_id=meeting].choice',
                'plan.scenes[scene_id=response].imagined',
                'plan.scenes[scene_id=response].progression_beats[999]',
                'plan.scenes[scene_id=response].progression_beats.trailing()',
                'plan.scenes[scene_id=response'):
            with self.subTest(path=path):
                self.assertFalse(scene_planning._path_exists(path, plan))
        plan['scenes'].append(copy.deepcopy(plan['scenes'][1]))
        self.assertFalse(scene_planning._path_exists('plan.scenes[scene_id=response].choice', plan))

    def test_one_real_parent_array_issue_can_route_multiple_adverse_chapters(self):
        plan = chapter_plan()
        response = review_control(plan)
        response['decision'] = 'revise'
        for row in response['chapter_checks']:
            row.update(status='needs_replan', reason='重复确认同一次邀请，需统一重排分章。')
        for path in ('plan.chapters', 'plan.scenes'):
            response['issues'] = [{'path': path, 'reason': '跨章重复同一确认。',
                'missing_link': '合并重复步骤，保留递票和共同出行的回应。'}]
            self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['issues'][0]['path'] = 'plan.main_question'
        with self.assertRaisesRegex(StoryError, '本章真实chapters或scenes路径'):
            scene_planning.validate_review(response, plan)

    def test_adverse_chapter_without_an_issue_cannot_disappear_after_validation(self):
        plan = chapter_plan()
        response = review_control(plan)
        response['chapter_checks'][0]['status'] = 'needs_replan'
        response['decision'] = 'revise'
        with self.assertRaisesRegex(StoryError, '本章真实chapters或scenes路径'):
            scene_planning.validate_review(response, plan)

    def test_quiet_reciprocity_and_a_necessary_long_chapter_are_not_length_failures(self):
        plan = chapter_plan()
        plan['scenes'][1]['budget'] = 4200
        response = review_control(plan)
        response['chapter_checks'][0]['reason'] = (
            '门口等待与递票接票在同一次见面中完成；安静回应把试探变成共同出行，'
            '本次保留连贯选择，章末接到两人实际走入雨中，而非再确认一次邀请。')
        self.assertEqual(scene_planning.chapter_budget_context(plan)[0]['planned_budget'], 4400)
        self.assertEqual(scene_planning.validate_review(response, plan), response)

    def test_legacy_without_explicit_chapters_is_not_given_fabricated_chapter_checks(self):
        plan = example_plan()
        response = review_control(plan)
        self.assertEqual(scene_planning.chapter_budget_context(plan), [])
        response.pop('chapter_checks')
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['chapter_checks'] = []
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['chapter_checks'] = [{'chapter_id': 'imagined'}]
        with self.assertRaisesRegex(StoryError, '不能补造章节检查'):
            scene_planning.validate_review(response, plan)

    def test_explicit_legacy_chapter_without_beats_can_only_quote_its_actual_scene(self):
        plan = chapter_plan()
        for scene in plan['scenes']:
            scene.pop('progression_beats')
        response = review_control(plan)
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['chapter_checks'][0]['evidence'][0]['beat_index'] = 0
        with self.assertRaisesRegex(StoryError, '无节拍'):
            scene_planning.validate_review(response, plan)

    def test_malformed_explicit_map_does_not_receive_legacy_compatibility(self):
        plan = chapter_plan()
        plan['chapters'] = None
        with self.assertRaisesRegex(StoryError, '非空章节映射数组'):
            scene_planning.chapter_budget_context(plan)

    def test_new_criteria_do_not_reuse_old_review_receipts(self):
        prior = digest({'protocol': 'scene-purpose-and-budget-v10-chapter-budget-evidence',
            'plan': scene_planning.PLAN_INSTRUCTION, 'review': scene_planning.REVIEW_INSTRUCTION})
        self.assertNotEqual(scene_planning.criteria_hash(), prior)


if __name__ == '__main__':
    unittest.main()
