"""Preserve old event facts and avoid fragile long-text retranscription."""
import copy
import unittest

from short_fiction_agent.story_studio import scene_planning
from short_fiction_agent.story_studio.contracts import StoryError
from test_story_chapter_plan_review import chapter_plan
from test_story_scene_purpose_budget import review_control


class LegacyPlanCandidateTests(unittest.TestCase):
    def setUp(self):
        self.source = chapter_plan()
        for scene in self.source['scenes']:
            scene['reading_role'] = (
                'support' if scene['scene_role'] == 'transition' else 'core_experience')
        self.source['scenes'][1]['new_information'] = '卡片背面写着她为他拒绝了另一场约会。'
        self.source['scenes'][1]['emotional_change'] = '他看完卡片，把返程票退掉，留下来陪她。'
        self.source['scenes'][1]['progression_beats'][0]['reward'] = '她把自己的名字写进同行栏，正式认领搭档。'
        self.source['scenes'][1]['purpose'] = '不能以策划者自评证明这场戏精彩。'
        self.source['scenes'][1]['dramatic_chain'] = {
            key: '本场真实接招已经发生。' for key in scene_planning.REVIEW_CHAIN_FIELDS}
        self.source['scenes'][1]['dramatic_chain']['deletion_loss'] = '这句价值辩护仍不进入主审。'

    def test_old_only_location_events_are_preserved_with_exact_original_paths_and_indices(self):
        before = copy.deepcopy(self.source)
        view = scene_planning.review_view(self.source)
        self.assertEqual(view['scenes'][1]['legacy_material'], [
            {'source_path': 'plan.scenes[1].new_information', 'text': self.source['scenes'][1]['new_information']},
            {'source_path': 'plan.scenes[1].emotional_change', 'text': self.source['scenes'][1]['emotional_change']}])
        self.assertEqual(view['scenes'][1]['progression_beats'][0]['legacy_material'], [
            {'source_path': 'plan.scenes[1].progression_beats[0].reward',
             'text': self.source['scenes'][1]['progression_beats'][0]['reward']}])
        self.assertNotIn('purpose', view['scenes'][1])
        self.assertNotIn('deletion_loss', view['scenes'][1]['dramatic_chain'])
        self.assertEqual(self.source, before)

    def test_actual_legacy_events_remain_citable_in_scene_budget_and_current_beat_checks(self):
        view = scene_planning.review_view(self.source)
        response = review_control(view)
        response['scene_checks'][1]['change_quote'] = self.source['scenes'][1]['new_information']
        reward = self.source['scenes'][1]['progression_beats'][0]['reward']
        response['reading_allocation_check']['first_reward']['quote'] = reward
        response['chapter_checks'][0]['evidence'] = [{'scene_id': 'response', 'beat_index': 0, 'quote': reward}]
        # The quotes prove these events were supplied. They do not establish
        # that either the planner's claims or the scripted verdict are sound.
        self.assertEqual(scene_planning.validate_review(response, self.source, evidence_plan=view), response)

    def test_provenance_metadata_cannot_be_used_as_an_actual_story_quote(self):
        view = scene_planning.review_view(self.source)
        response = review_control(view)
        response['scene_checks'][1]['change_quote'] = 'plan.scenes[1].new_information'
        with self.assertRaisesRegex(StoryError, '引用不属于'):
            scene_planning.validate_review(response, self.source, evidence_plan=view)
        response = review_control(view)
        response['chapter_checks'][0]['evidence'] = [{'scene_id': 'response', 'beat_index': 0,
            'quote': 'plan.scenes[1].progression_beats[0].reward'}]
        with self.assertRaisesRegex(StoryError, '不属于对应实际'):
            scene_planning.validate_review(response, self.source, evidence_plan=view)

    def test_fixed_core_experience_reference_binds_the_real_field_without_copying_long_text(self):
        self.source['core_reading_experience'] = '她是曾经遥不可及的人，这一次却主动为他留下。' * 20
        view = scene_planning.review_view(self.source)
        response = review_control(view)
        allocation = response['reading_allocation_check']
        allocation.pop('core_experience_quote')
        allocation['core_experience_ref'] = 'plan.core_reading_experience'
        self.assertEqual(scene_planning.validate_review(response, self.source, evidence_plan=view), response)
        for wrong in ('plan.main_question', 'plan.nonexistent', 'contract.reader_promise'):
            with self.subTest(source_ref=wrong):
                allocation['core_experience_ref'] = wrong
                with self.assertRaisesRegex(StoryError, '核心体验'):
                    scene_planning.validate_review(response, self.source, evidence_plan=view)
        self.source.pop('core_reading_experience')
        self.source['story_question'] = self.source.pop('main_question')
        view = scene_planning.review_view(self.source)
        response = review_control(view)
        response['reading_allocation_check'].pop('core_experience_quote')
        response['reading_allocation_check']['core_experience_ref'] = 'plan.story_question'
        self.assertEqual(scene_planning.validate_review(response, self.source, evidence_plan=view), response)

    def test_a_correct_reference_does_not_repair_or_hide_an_inaccurate_old_quote(self):
        self.source['core_reading_experience'] = '她曾经遥不可及，如今主动选择与他同行。'
        view = scene_planning.review_view(self.source)
        response = review_control(view)
        allocation = response['reading_allocation_check']
        allocation['core_experience_ref'] = 'plan.core_reading_experience'
        allocation['core_experience_quote'] = self.source['core_reading_experience'].replace('及', '遥')
        before = copy.deepcopy(response)
        with self.assertRaisesRegex(StoryError, '核心体验'):
            scene_planning.validate_review(response, self.source, evidence_plan=view)
        self.assertEqual(response, before)
        allocation['core_experience_quote'] = self.source['core_reading_experience']
        self.assertEqual(scene_planning.validate_review(response, self.source, evidence_plan=view), response)


if __name__ == '__main__':
    unittest.main()
