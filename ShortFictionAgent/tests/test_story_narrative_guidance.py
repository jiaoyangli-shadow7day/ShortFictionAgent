import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import narrative_guidance as guide
from short_fiction_agent.story_studio.research_guidance import guidance_fingerprint


class NarrativeGuidanceTests(unittest.TestCase):
    def test_examples_are_labelled_authored_not_market_evidence(self):
        result = guide.writing_guidance({'genre': {'primary': 'romance'}})
        self.assertIn('不是热门作品原文', result['evidence_scope'])
        self.assertEqual(len(result['examples']), 1)
        self.assertIn('quiet_choice', {item['id'] for item in result['examples']})

    def test_avoid_terms_do_not_select_romance(self):
        result = guide.writing_guidance({'genre': {'primary': 'custom'},
            'reader_promise': '客户与经理的责任争夺',
            'constraints': {'avoid': ['恋爱', '甜宠', '情侣']}})
        self.assertEqual({item['id'] for item in result['examples']},
                         {'professional_stakes'})

    def test_repair_example_is_not_imposed_on_all_relationships(self):
        plain = guide.writing_guidance({'genre': {'primary': 'romance'}})
        repair = guide.writing_guidance({'genre': {'primary': 'romance'},
                                        'reader_promise': '失言后修复误会'})
        self.assertNotIn('explanation_to_response', {x['id'] for x in plain['examples']})
        self.assertIn('explanation_to_response', {x['id'] for x in repair['examples']})

    def test_coaching_change_invalidates_prompt_reuse(self):
        before = guidance_fingerprint()
        with patch.object(guide, 'RULES', guide.RULES + ['新增的实质表达规则']):
            self.assertNotEqual(before, guidance_fingerprint())


if __name__ == '__main__':
    unittest.main()
