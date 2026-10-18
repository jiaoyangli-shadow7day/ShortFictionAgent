"""Input, privacy and cache regressions, not evidence of literary quality."""
import copy
import json
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import target_reader, research_guidance


class TargetReaderTests(unittest.TestCase):
    def setUp(self):
        self.contract = {
            'channels': ['fanqie_short'], 'title': '回到2018',
            'audience': {'description': '喜欢重生恋爱的男性读者',
                         'expectations': ['查询发挥实际作用', '女孩主动争取']},
            'genre': {'primary': 'romance', 'secondary': [],
                      'custom_description': '重生脑洞校园恋爱', 'private_note': 'PRIVATE-GENRE'},
            'tone': '轻松、有欲望', 'point_of_view': 'first_person',
            'reader_promise': 'AUTHOR-PROMISE：重生优势和相互选择',
            'author_input': {'entry_facts': ['回到高考前，拥有十次查询'],
                             'premise': 'PRIVATE-PREMISE', 'locked_facts': ['PRIVATE-LOCK']},
            'constraints': {'avoid': ['PRIVATE-AVOID：复仇分手']},
            'ending_resolution': 'PRIVATE-ENDING',
            'planned_reader_promise': {'main_question': 'PRIVATE-PLAN'},
        }

    def test_cold_context_preserves_audience_without_future_plan_or_private_constraints(self):
        result = target_reader.entry_context(self.contract)
        self.assertEqual(result['audience_expectations'], self.contract['audience']['expectations'])
        self.assertEqual(result['genre']['custom_description'], '重生脑洞校园恋爱')
        self.assertEqual(result['tone'], '轻松、有欲望')
        self.assertEqual(result['entry_facts'], ['回到高考前，拥有十次查询'])
        self.assertNotIn('reader_promise', result)
        encoded = json.dumps(result, ensure_ascii=False)
        for word in ('PRIVATE-', 'AUTHOR-PROMISE', 'planned_reader_promise', 'constraints'):
            self.assertNotIn(word, encoded)

    def test_author_promise_is_explicit_and_cannot_be_overridden_by_plan(self):
        result = target_reader.entry_context(self.contract, include_author_promise=True)
        self.assertEqual(result['reader_promise'], self.contract['reader_promise'])
        self.assertNotIn('PRIVATE-PLAN', json.dumps(result))
        result['genre']['secondary'].append('mutated')
        result['audience_expectations'].append('mutated')
        self.assertEqual(self.contract['genre']['secondary'], [])
        self.assertEqual(len(self.contract['audience']['expectations']), 2)

    def test_new_same_family_evidence_reaches_model_research_context(self):
        # This asserts actual research projection, not that a model obeys it.
        contract = {**self.contract, 'format': 'short_story',
                    'story_engine': 'relationship_choice',
                    'research_preferences': {'reader_tags': ['男生情感', '重生', '校园']}}
        context = research_guidance.model_research_context(
            research_guidance.research_context(contract, task='write_story_scene'))
        selected = context['target_reader_research']['mechanisms']
        self.assertEqual({row['id'] for row in selected},
                         {'rebirth_actual_advantage', 'romance_specific_choice'})
        self.assertTrue(all(row['sources'] and row['scope_limit'] for row in selected))
        self.assertEqual(context['target_reader_research']['stage'], 'write')

    def test_forbidden_plots_do_not_select_examples_and_other_channels_are_not_assumed_fanqie(self):
        quiet = {'channels': ['fanqie_short'], 'title': '雨后的散步',
                 'constraints': self.contract['constraints']}
        self.assertEqual(target_reader.research_context(quiet, 'plan')['mechanisms'], [])
        other = {**self.contract, 'channels': ['generic_text']}
        self.assertFalse(target_reader.entry_context(other)['enabled'])
        self.assertEqual(target_reader.research_context(other, 'write')['mechanisms'], [])

    def test_research_update_invalidates_research_bound_cache(self):
        before = research_guidance.guidance_fingerprint()
        data = copy.deepcopy(target_reader.library())
        data['mechanisms'][0]['realization'] += '新的已核验结论。'
        with patch.object(target_reader, 'library', return_value=data):
            self.assertNotEqual(research_guidance.guidance_fingerprint(), before)

    def test_research_can_use_positive_premise_without_leaking_it_to_cold_reader(self):
        contract = {'channels': ['fanqie_short'], 'title': '香港往事',
                    'author_input': {'premise': '利用手机查询未来资料',
                                     'locked_facts': ['青梅恋爱']},
                    'constraints': {'avoid': ['反击复仇']}}
        selected = target_reader.research_context(contract, 'plan')['mechanisms']
        self.assertEqual([row['id'] for row in selected], ['rebirth_actual_advantage'])
        cold = json.dumps(target_reader.entry_context(contract), ensure_ascii=False)
        self.assertNotIn('未来资料', cold)
        self.assertNotIn('青梅恋爱', cold)


if __name__ == '__main__':
    unittest.main()
