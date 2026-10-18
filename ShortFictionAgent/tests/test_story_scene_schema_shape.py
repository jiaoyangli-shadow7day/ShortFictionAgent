"""Nested scene-chain validation controls; these do not measure story quality."""
from copy import deepcopy
import unittest

from short_fiction_agent.story_studio import scene_planning
from short_fiction_agent.story_studio.contracts import StoryError
from test_story_scene_purpose_budget import example_plan


CHAIN_FIELDS = ('attempt', 'response', 'adjustment', 'change',
                'next_effect', 'deletion_loss')
ERROR_PREFIX = 'dramatic_chain缺少行动、接招、调整、变化或删场作用'


def complete_plan():
    """Add an explicit nested schema without changing the legacy fixture."""
    plan = deepcopy(example_plan())
    for scene in plan['scenes']:
        if scene['scene_role'] != 'core':
            continue
        scene['dramatic_chain'] = {
            'attempt': scene['choice'],
            'response': scene['consequence'],
            'adjustment': '她确认对方留下，再提出下一次同行的安排。',
            'change': scene['new_information'],
            'next_effect': '下一场双方可以根据这次回应继续作出选择。',
            'deletion_loss': '会失去已经发生的回应及双方关系的递进。',
        }
    return plan


class SceneSchemaShapeTests(unittest.TestCase):
    def assert_rejected_without_mutation(self, plan, path):
        before = deepcopy(plan)
        with self.assertRaises(StoryError) as raised:
            scene_planning.validate_budgets(plan, {})
        self.assertIn(ERROR_PREFIX, str(raised.exception))
        self.assertIn(path, str(raised.exception))
        self.assertEqual(plan, before)

    def test_complete_nested_core_chains_keep_budgets_and_source(self):
        legacy_before = example_plan()
        plan = complete_plan()
        before = deepcopy(plan)
        self.assertEqual(scene_planning.validate_budgets(plan, {}), [200, 2800, 1000])
        self.assertEqual(plan, before)
        self.assertEqual(example_plan(), legacy_before)
        self.assertNotIn('dramatic_chain', legacy_before['scenes'][1])

    def test_each_missing_nested_key_reports_its_actual_scene_index(self):
        for index in (1, 2):
            for field in CHAIN_FIELDS:
                with self.subTest(index=index, field=field):
                    plan = complete_plan()
                    del plan['scenes'][index]['dramatic_chain'][field]
                    self.assert_rejected_without_mutation(
                        plan, f'scenes[{index}].dramatic_chain.{field}')

    def test_each_empty_nested_key_reports_its_actual_scene_index(self):
        for index in (1, 2):
            for field in CHAIN_FIELDS:
                with self.subTest(index=index, field=field):
                    plan = complete_plan()
                    plan['scenes'][index]['dramatic_chain'][field] = ''
                    self.assert_rejected_without_mutation(
                        plan, f'scenes[{index}].dramatic_chain.{field}')

    def test_each_whitespace_nested_key_reports_its_actual_scene_index(self):
        for index in (1, 2):
            for field in CHAIN_FIELDS:
                with self.subTest(index=index, field=field):
                    plan = complete_plan()
                    plan['scenes'][index]['dramatic_chain'][field] = ' \t\n '
                    self.assert_rejected_without_mutation(
                        plan, f'scenes[{index}].dramatic_chain.{field}')

    def test_scene_root_deletion_loss_cannot_replace_nested_value(self):
        for index in (1, 2):
            with self.subTest(index=index):
                plan = complete_plan()
                scene = plan['scenes'][index]
                scene['deletion_loss'] = scene['dramatic_chain'].pop('deletion_loss')
                self.assert_rejected_without_mutation(
                    plan, f'scenes[{index}].dramatic_chain.deletion_loss')
                self.assertNotIn('deletion_loss', scene['dramatic_chain'])

    def test_plan_root_deletion_loss_cannot_replace_nested_value(self):
        for index in (1, 2):
            with self.subTest(index=index):
                plan = complete_plan()
                chain = plan['scenes'][index]['dramatic_chain']
                plan['deletion_loss'] = chain.pop('deletion_loss')
                self.assert_rejected_without_mutation(
                    plan, f'scenes[{index}].dramatic_chain.deletion_loss')
                self.assertNotIn('deletion_loss', chain)

    def test_non_null_non_object_chain_reports_the_chain_path(self):
        for index in (1, 2):
            # Legacy None remains optional; the fresh-plan gate is stricter.
            for malformed in ([], '已有六个字段', 1, False):
                with self.subTest(index=index, malformed=malformed):
                    plan = complete_plan()
                    plan['scenes'][index]['dramatic_chain'] = malformed
                    self.assert_rejected_without_mutation(
                        plan, f'scenes[{index}].dramatic_chain')


if __name__ == '__main__':
    unittest.main()
