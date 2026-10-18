"""Release checks protect provenance while keeping editorial requirements active."""
import hashlib
import json
from pathlib import Path
import unittest

from short_fiction_agent.story_studio import submission_guide


ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'ShortFictionAgent/short_fiction_agent/story_studio/data'


class PublicMaterialsTests(unittest.TestCase):
    def test_attributed_summaries_preserve_scoped_editor_requirements(self):
        profile = submission_guide._guides()['profiles']['fanqie_editor_dami']
        self.assertEqual({r['id'] for r in profile['semantic_requirements']}, {
            'mainline_expectation', 'entry_promise', 'causal_progression',
            'prose_value', 'chapter_pull', 'complete_payoff'})
        contract = {'channels': ['fanqie_short']}
        for stage in ('plan', 'lead', 'draft', 'review', 'revision'):
            prompt = submission_guide.submission_guide_guidance(contract, stage)
            for row in profile['semantic_requirements']:
                if stage in row['stages']:
                    self.assertIn(row['requirement'], prompt)
                    self.assertIn(row['acceptance'], prompt)
        for source in profile['source_documents']:
            self.assertEqual(source['content_kind'], 'project_authored_summary')
            self.assertTrue(source['url'].startswith('https://docs.qq.com/doc/'))
            self.assertEqual(hashlib.sha256(source['text'].encode()).hexdigest(), source['sha256'])
            self.assertNotEqual(source['sha256'], source['original_sha256'])
            self.assertEqual((ROOT / source['snapshot_path']).read_text(), source['text'])
        self.assertFalse(list((ROOT / 'docs/reference').glob('submission-guide-[123].txt')))

    def test_external_calibration_bodies_are_replaced_with_original_examples(self):
        cases = json.loads((DATA / 'prose_reader_calibration.json').read_text())['cases']
        for row in cases:
            self.assertIn(row['source']['path'], {
                'author-owned-example', 'project-authored-synthetic-example'})
            self.assertEqual(hashlib.sha256(row['body'].encode()).hexdigest(),
                             row['source']['excerpt_sha256'])

    def test_limited_lead_quotations_keep_provenance_and_separate_rights(self):
        research = json.loads((DATA / 'lead_research.json').read_text())
        self.assertEqual(len(research['worked_examples']), 72)
        notice = (ROOT / 'THIRD_PARTY_NOTICES.md').read_text()
        for row in research['worked_examples']:
            if row.get('excerpt'):
                self.assertLessEqual(len(row['excerpt']), 60)
                self.assertTrue(row.get('source') or row.get('url'))
                self.assertTrue(row['mechanism'])
                self.assertIn(row['id'], notice)
        self.assertIn('短引文属于对应作品权利人，不纳入 MIT 授权', notice)
        self.assertEqual((ROOT / 'ShortFictionAgent/NOTICE').read_text(), notice)
        self.assertEqual((ROOT / 'LICENSE').read_text(), (ROOT / 'ShortFictionAgent/LICENSE').read_text())


if __name__ == '__main__':
    unittest.main()
