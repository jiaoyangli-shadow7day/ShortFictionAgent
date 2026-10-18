"""Chapter source coverage controls, not tests of literary chapter quality."""
import copy
import unittest

from short_fiction_agent.story_studio.chapter_structure import chapters, validate_outline_points
from short_fiction_agent.story_studio.contracts import StoryError


class ChapterStructureTests(unittest.TestCase):
    def setUp(self):
        self.plan = {'scenes': [{'scene_id': key} for key in ('a', 'b', 'c')],
                     'chapters': [
                         {'chapter_id': 'first', 'title': '第一章 当面回应', 'scene_ids': ['a', 'b']},
                         {'chapter_id': 'second', 'title': '第二章 再次选择', 'scene_ids': ['c']}]}

    def test_chapter_can_contain_multiple_scenes_without_becoming_output_chunks(self):
        before = copy.deepcopy(self.plan)
        result = chapters(self.plan, allow_legacy=False)
        self.assertEqual(result[0], {'chapter_id': 'first', 'title': '第一章 当面回应',
            'scene_ids': ['a', 'b'], 'scene_indices': [0, 1], 'legacy_inferred': False})
        self.assertEqual(result[1]['scene_indices'], [2])
        self.assertEqual(self.plan, before)
        result[0]['scene_ids'].append('fabricated')
        self.assertEqual(self.plan, before)

    def test_new_plan_cannot_omit_mapping_but_legacy_plan_gets_explicit_review_units(self):
        self.plan.pop('chapters')
        before = copy.deepcopy(self.plan)
        with self.assertRaisesRegex(StoryError, '新方案缺少chapters'):
            chapters(self.plan, allow_legacy=False)
        result = chapters(self.plan)
        self.assertEqual([row['scene_indices'] for row in result], [[0], [1], [2]])
        self.assertTrue(all(row['legacy_inferred'] for row in result))
        self.assertEqual(len({row['chapter_id'] for row in result}), 3)
        self.assertEqual(self.plan, before)

    def test_invalid_explicit_mapping_never_uses_legacy_fallback(self):
        for invalid in (None, [], {}, '第一章'):
            with self.subTest(mapping=invalid):
                self.plan['chapters'] = invalid
                with self.assertRaisesRegex(StoryError, '非空章节映射数组'):
                    chapters(self.plan, allow_legacy=True)

    def test_mapping_rejects_unknown_duplicate_gap_and_reordered_scenes(self):
        for keys in (['a', 'a'], ['a', 'c'], ['b', 'a'], ['a', 'unknown']):
            with self.subTest(scene_ids=keys):
                plan = copy.deepcopy(self.plan)
                plan['chapters'][0]['scene_ids'] = keys
                with self.assertRaisesRegex(StoryError, '顺序连续覆盖'):
                    chapters(plan)
        self.plan['chapters'].pop()
        with self.assertRaisesRegex(StoryError, '遗漏实际场景'):
            chapters(self.plan)

    def test_ids_titles_and_scene_lists_are_required_and_well_typed(self):
        for field, invalid in (('chapter_id', ''), ('chapter_id', []), ('title', None),
                ('title', '  '), ('scene_ids', []), ('scene_ids', 'a'), ('scene_ids', [True])):
            with self.subTest(field=field, value=invalid):
                plan = copy.deepcopy(self.plan)
                plan['chapters'][0][field] = invalid
                with self.assertRaisesRegex(StoryError, '须含chapter_id'):
                    chapters(plan)
        self.plan['chapters'][1]['chapter_id'] = 'first'
        with self.assertRaisesRegex(StoryError, 'chapter_id重复'):
            chapters(self.plan)

    def test_actual_scene_ids_must_be_unique_and_nonempty(self):
        self.plan['scenes'][1]['scene_id'] = 'a'
        with self.assertRaisesRegex(StoryError, '实际scene_id重复'):
            chapters(self.plan)
        self.plan['scenes'][1]['scene_id'] = ''
        with self.assertRaisesRegex(StoryError, '缺少实际场景'):
            chapters(self.plan)


class OutlineReadingPointTests(unittest.TestCase):
    """Evidence-reference controls only; these labels do not prove quality."""

    def setUp(self):
        self.plan = {'scenes': [
            {'scene_id': 'quiet', 'progression_beats': [{
                'reader_question': '她会接受这次普通的晚饭邀请吗？',
                'action': '我把加班后的两份饭摆上桌，问她明晚还要不要一起吃。',
                'response': '她把自己的杯子挪到我这边，说周五也给她留一份。',
                'change': '一次照顾变成她主动提出的固定约定。',
                'reward': '她接下邀请，又主动把下一次约定延长。',
                'next_expectation': '周五她会怎样赴约？'}]},
            {'scene_id': 'next', 'progression_beats': [{'response': '她按约赴约。'}]}],
            'chapters': [
                {'chapter_id': 'first', 'title': '第一章 留一份饭', 'scene_ids': ['quiet'],
                 'reading_points': [
                     {'kind': 'emotion', 'scene_id': 'quiet', 'beat_index': 0,
                      'note': '她将杯子移近并主动约周五，把被照顾改成双向邀约。'},
                     {'kind': 'hook', 'scene_id': 'quiet', 'beat_index': 0,
                      'note': '她主动延长到周五，让读者期待这次约会怎样发生。'}]},
                {'chapter_id': 'second', 'title': '第二章 赴约', 'scene_ids': ['next'],
                 'reading_points': [
                     {'kind': 'emotion', 'scene_id': 'next', 'beat_index': 0,
                      'note': '她实际赴约，回应先前邀请。'},
                     {'kind': 'hook', 'scene_id': 'next', 'beat_index': 0,
                      'note': '传输控制：约定兑现后选择一起回家，不证明吸引力。'}]}]}

    def test_quiet_reciprocal_emotion_and_hook_can_share_one_actual_beat(self):
        before = copy.deepcopy(self.plan)
        self.assertIsNone(validate_outline_points(self.plan))
        self.assertEqual(self.plan, before)

    def test_missing_and_same_kind_points_are_not_inferred_or_relabelled(self):
        for defect in ('missing', 'empty', 'one', 'same_kind'):
            with self.subTest(defect=defect):
                plan = copy.deepcopy(self.plan)
                row = plan['chapters'][0]
                if defect == 'missing':
                    row.pop('reading_points')
                elif defect == 'empty':
                    row['reading_points'] = []
                elif defect == 'one':
                    row['reading_points'].pop()
                else:
                    row['reading_points'][1]['kind'] = 'emotion'
                before = copy.deepcopy(plan)
                with self.assertRaisesRegex(StoryError, 'reading_points'):
                    validate_outline_points(plan)
                self.assertEqual(plan, before)
                # Legacy chapter reading remains a source mapping operation.
                self.assertEqual(chapters(plan)[0]['scene_ids'], ['quiet'])

    def test_foreign_future_and_nonexistent_scene_references_fail(self):
        for scene_id in ('next', 'imagined', '', None, ['quiet']):
            with self.subTest(scene_id=scene_id):
                plan = copy.deepcopy(self.plan)
                plan['chapters'][0]['reading_points'][0]['scene_id'] = scene_id
                with self.assertRaisesRegex(StoryError, '本章实际场景'):
                    validate_outline_points(plan)

    def test_beat_references_must_be_real_zero_based_integers(self):
        for index in (-1, 1, 99, True, False, 0.0, '0', None):
            with self.subTest(beat_index=index):
                plan = copy.deepcopy(self.plan)
                plan['chapters'][0]['reading_points'][0]['beat_index'] = index
                with self.assertRaisesRegex(StoryError, '实际progression_beats'):
                    validate_outline_points(plan)
        for invalid in (None, [], {}, ['imagined beat']):
            with self.subTest(beats=invalid):
                plan = copy.deepcopy(self.plan)
                plan['scenes'][0]['progression_beats'] = invalid
                with self.assertRaisesRegex(StoryError, '实际progression_beats'):
                    validate_outline_points(plan)

    def test_kinds_and_notes_cannot_be_empty_wrong_types_or_labels_alone(self):
        for field, value in (('kind', 'surprise'), ('kind', []), ('kind', None),
                ('note', ''), ('note', '  '), ('note', None), ('note', []),
                ('note', 'emotion'), ('note', '钩子')):
            with self.subTest(field=field, value=value):
                plan = copy.deepcopy(self.plan)
                plan['chapters'][0]['reading_points'][0][field] = value
                with self.assertRaisesRegex(StoryError, field):
                    validate_outline_points(plan)


if __name__ == '__main__':
    unittest.main()
