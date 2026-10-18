"""Heading syntax must not skip chapter review or duplicate the lead boundary."""
import copy
import json
import re
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, lead, narrative_audit as narrative
from short_fiction_agent.story_studio.contracts import StoryError
from tests.narrative_fixtures import narrative_response

OLD_NARRATIVE_HEADING = re.compile(r'^(?:第[一二三四五六七八九十百零〇两\d]+[章节回](?:[ \t].*)?|[0-9]{1,3}[、.．]?|[一二三四五六七八九十]+、)$')
OLD_LEAD_HEADING = re.compile(r'(?m)^第一章(?:[ \t]+[^\n]*)?[ \t]*$')


class ChapterHeadingTests(unittest.TestCase):
    def body(self, first, second):
        return f'{first}\n我敲了门。\n她递来钥匙。\n{second}\n我摆好第二副碗筷。\n她让我下周再来。'

    def test_colon_and_unspaced_titles_require_every_actual_chapter_tail(self):
        for headings in (('第一章：开门', '第二章：留下'),
                         ('第一章开门', '第二章留下'),
                         ('第一章:开门', '第二章:留下')):
            with self.subTest(headings=headings):
                body = self.body(*headings)
                prompt = chief.prompt(body, {})
                data = json.loads(prompt.rsplit('\n', 1)[1])
                self.assertEqual(data['coverage_plan']['chapter_endings'], [
                    {'boundary_id': 'p2', 'tail_ids': ['p1', 'p2'], 'is_final': False},
                    {'boundary_id': 'p5', 'tail_ids': ['p4', 'p5'], 'is_final': True}])
                raw = narrative_response(prompt)
                raw['narrative_audit']['chapter_handoffs'].pop(0)
                checked = chief.validate_response(raw, body)
                final = narrative.final_gate(checked, body, [checked])
                self.assertEqual(final['recommendation'], 'uncertain')
                self.assertIn('chapter_handoffs.p2', checked['narrative_audit']['missing'])

    def test_lead_split_preserves_prose_and_assemble_does_not_add_duplicate_heading(self):
        for heading in ('第一章：开门', '第一章开门', '第一章:开门', '第一章 开门', '第一章'):
            with self.subTest(heading=heading):
                prose = '我敲了门。\n她递来钥匙。'
                story = heading + '\n' + prose
                assembled = lead.assemble('她说钥匙只留给一个人。', story)
                self.assertEqual(lead.split(assembled), ('她说钥匙只留给一个人。', prose))
                self.assertEqual(assembled.count('第一章'), 1)
                self.assertIn(story, assembled)
                with self.assertRaisesRegex(StoryError, '章标题'):
                    lead.validate_single_candidate({'text': heading + '\n她递来钥匙。'}, story, {})

    def test_inline_mentions_do_not_start_chapters_or_split_the_lead(self):
        body = '她问我第一章：开门是不是写完了。\n我翻到第一章开门，发现夹着钥匙。'
        self.assertEqual(narrative.chapter_endings(chief.paragraphs(body)), [
            {'boundary_id': 'p1', 'tail_ids': ['p0', 'p1'], 'is_final': True}])
        self.assertIsNone(lead.CHAPTER.search(body))
        with self.assertRaises(StoryError):
            lead.split(body)

    def test_existing_spaced_titles_keep_exact_review_and_lead_prompt_bytes(self):
        body = self.body('第一章 开门', '第二章 留下')
        current = chief.prompt(body, {})
        with patch.object(narrative, 'CHAPTER_HEADING', OLD_NARRATIVE_HEADING):
            self.assertEqual(current, chief.prompt(body, {}))
        contract = {'format': 'short_story', 'channels': ['fanqie_short'],
                    'length': {'target': 6000, 'min': 5000, 'max': None}}
        prompt = lead.single_candidate_prompt(body, contract)
        with patch.object(lead, 'CHAPTER', OLD_LEAD_HEADING):
            self.assertEqual(prompt, lead.single_candidate_prompt(body, contract))

    def test_validation_change_invalidates_old_gate_but_does_not_change_prompt_protocol(self):
        body = self.body('第一章 开门', '第二章 留下')
        prompt = chief.prompt(body, {})
        checked = chief.validate_response(narrative_response(prompt), body)
        old = copy.deepcopy(checked)
        old['narrative_audit']['validation_version'] = 'source-bound-narrative-gate-v3'
        self.assertEqual(narrative.final_gate(old, body, [old])['recommendation'], 'uncertain')
        current_hash = chief.criteria_hash()
        with patch.object(narrative, 'VALIDATION_VERSION', 'source-bound-narrative-gate-v3'):
            self.assertNotEqual(current_hash, chief.criteria_hash())
            self.assertEqual(prompt, chief.prompt(body, {}))


if __name__ == '__main__':
    unittest.main()
