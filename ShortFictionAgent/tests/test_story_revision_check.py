import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.revision_check import validate_resolutions
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow


class RevisionCheckTests(unittest.TestCase):
    def setUp(self):
        self.findings = [{'finding_id': 'f0'}, {'finding_id': 'f1'}]
        self.body = '她主动打开录音。\n他仍提到从未给出的卡片。'
        self.rows = [{'finding_id': 'f0', 'verdict': 'resolved', 'reason': '已明确许可',
                     'fix_intent': '保留', 'paragraph_index': 0},
                    {'finding_id': 'f1', 'verdict': 'unresolved', 'reason': '仍有卡片',
                     'fix_intent': '删除旧卡片描写', 'paragraph_index': 1}]

    def test_coverage_cannot_hide_unresolved_finding(self):
        for rows in (self.rows[:1], self.rows + self.rows[:1]):
            with self.subTest(rows=rows), self.assertRaises(StoryError):
                validate_resolutions({'resolutions': rows}, self.findings, self.body)

    def test_evidence_is_extracted_from_current_body(self):
        result = validate_resolutions({'resolutions': self.rows}, self.findings, self.body)
        self.assertEqual(result[1]['quote'], '他仍提到从未给出的卡片。')
        for index in (-1, 2, True):
            row = {**self.rows[1], 'paragraph_index': index}
            with self.subTest(index=index), self.assertRaises(StoryError):
                validate_resolutions({'resolutions': [self.rows[0], row]}, self.findings, self.body)

    def test_remaining_cross_paragraph_error_requires_repair_and_recheck(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'micro_story', 'review_mode': 'deep_review',
                                                'genre': {'primary': 'romance'},
                                                'reader_promise': '赠礼中的相互理解'}))
            flow = StoryWorkflow(project, None)
            tasks = []
            def call(task, prompt):
                tasks.append(task)
                if task == 'story_edit':
                    if tasks.count(task) == 1:
                        return {'back_to_plan': False, 'edits': [{'quote': '他送了卡片。', 'replacement': '他转了钱。',
                                          'kind': 'rewrite', 'benefit': '按事实修正', 'risk': '后文须统一'}]}
                    return {'back_to_plan': False, 'edits': [{'quote': '她拿着卡片。', 'replacement': '她收起手机。',
                                      'kind': 'rewrite', 'benefit': '统一道具', 'risk': '无'}]}
                return {'resolutions': [{'finding_id': 'f0',
                    'verdict': 'unresolved' if tasks.count(task) == 1 else 'resolved',
                    'reason': '后文仍有卡片' if tasks.count(task) == 1 else '两处已统一',
                    'fix_intent': '修正后文道具', 'paragraph_index': 1}]}
            flow.call_json = call
            edited = flow._edit('他送了卡片。\n她拿着卡片。', issues=[{
                'origin': 'independent_full_read', 'quote': '他送了卡片。', 'reason': '改用转账',
                'fix_intent': '核对所有卡片出现'}])
            self.assertEqual(edited, '他转了钱。\n她收起手机。\n')
            self.assertEqual(tasks, ['story_edit', 'revision_resolution_review',
                                     'story_edit', 'revision_resolution_review'])
            self.assertEqual(len(list((project.root/'independent_revision_checks').glob('*.md'))), 2)

    def test_twice_unresolved_stops_instead_of_returning_prose(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'micro_story', 'review_mode': 'deep_review',
                                                'reader_promise': '赠礼中的相互理解'}))
            flow = StoryWorkflow(project, None)
            flow.call_json = lambda task, prompt: {'back_to_plan': False, 'edits': []} if task == 'story_edit' else {
                'resolutions': [{'finding_id': 'f0', 'verdict': 'unresolved',
                    'reason': '仍有冲突', 'fix_intent': '继续修正', 'paragraph_index': 0}]}
            with self.assertRaisesRegex(StoryError, '仍未解决'):
                flow._edit('她仍拿着卡片。', issues=[{'origin': 'independent_full_read',
                    'reason': '道具没有来源', 'fix_intent': '核对来源'}])
            self.assertEqual(len(list((project.root/'independent_revision_checks').glob('*.json'))), 2)


if __name__ == '__main__': unittest.main()
