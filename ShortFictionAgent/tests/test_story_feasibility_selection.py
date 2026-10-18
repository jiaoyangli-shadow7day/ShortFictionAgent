"""Generator self-assessment cannot bypass an independent grounded veto."""
import copy
import json
import unittest
import test_story_distribution as distribution
from short_fiction_agent.story_studio.contracts import StoryError
from short_fiction_agent.story_studio.selection_review import validate_comparison
from short_fiction_agent.story_studio.storage import read_json


class FeasibilitySelectionTests(unittest.TestCase):
    setUp = distribution.DistributionTests.setUp
    def block(self, response, index, quote='找回信'):
        response['comparisons'][index]['feasibility'] = {
            'decision': 'revise', 'basis': '核心解法未建立',
            'blocking_claims': [{'quote': quote, 'reason': '职业或自评不证明有此权限'}]}

    def test_independent_veto_overrides_generator_viable(self):
        options = json.loads(self.llm.generate('', 'story_candidates'))['candidates']
        result = json.loads(self.llm.generate('', 'candidate_comparative_review'))
        self.block(result, 0)
        with self.assertRaisesRegex(StoryError, '核心缺口'):
            validate_comparison(result, options)
        result['recommended_id'] = 1
        self.assertEqual(validate_comparison(result, options)['recommended_index'], 1)

    def test_blocker_quote_must_belong_to_the_corresponding_candidate(self):
        options = json.loads(self.llm.generate('', 'story_candidates'))['candidates']
        options[1]['goal'] = '只存在于第二项的权限'
        result = json.loads(self.llm.generate('', 'candidate_comparative_review'))
        self.block(result, 0, '只存在于第二项的权限')
        result['recommended_id'] = 1
        with self.assertRaisesRegex(StoryError, '准确引用'):
            validate_comparison(result, options)

    def test_missing_or_contradictory_assessment_is_rejected(self):
        options = json.loads(self.llm.generate('', 'story_candidates'))['candidates']
        result = json.loads(self.llm.generate('', 'candidate_comparative_review'))
        invalid = copy.deepcopy(result)
        invalid['comparisons'][0].pop('feasibility')
        with self.assertRaises(StoryError):
            validate_comparison(invalid, options)
        self.block(result, 0)
        result['comparisons'][0]['feasibility']['decision'] = 'pass'
        with self.assertRaisesRegex(StoryError, '矛盾'):
            validate_comparison(result, options)

    def intercept(self, task, indexes, *, all_blocked=False):
        original = self.flow.call_json
        def call(name, prompt):
            response = original(name, prompt)
            if name == task:
                for index in indexes:
                    self.block(response, index, '信被取走了' if task.startswith('opening') else '找回信')
                response['recommended_id'] = None if all_blocked else (
                    'opening-1' if task.startswith('opening') else 1)
            return response
        self.flow.call_json = call

    def test_all_rejected_judgments_saved_without_format_retry_or_opening_generation(self):
        self.intercept('candidate_comparative_review', range(4), all_blocked=True)
        with self.assertRaisesRegex(StoryError, '全部候选'):
            self.flow.develop()
        self.assertEqual(self.llm.tasks.count('candidate_comparative_review'), 1)
        self.assertNotIn('opening_candidates', self.llm.tasks)
        self.assertIsNone(read_json(self.project.root / 'candidate_comparison.json')['recommended_id'])

    def test_explicit_candidate_choice_does_not_bypass_veto(self):
        self.intercept('candidate_comparative_review', [0])
        with self.assertRaisesRegex(StoryError, '核心缺口'):
            self.flow.develop(candidate_index=0)
        self.assertNotIn('opening_candidates', self.llm.tasks)

    def test_explicit_opening_choice_does_not_bypass_veto(self):
        self.intercept('opening_comparative_review', [0])
        with self.assertRaisesRegex(StoryError, '核心缺口'):
            self.flow.develop(opening_index=0)
        self.assertFalse((self.project.root / 'selection.json').exists())

    def test_all_openings_rejected_preserves_judgment_and_stops_selection(self):
        self.intercept('opening_comparative_review', range(3), all_blocked=True)
        with self.assertRaisesRegex(StoryError, '全部开篇'):
            self.flow.develop()
        self.assertEqual(self.llm.tasks.count('opening_comparative_review'), 1)
        self.assertIsNone(read_json(self.project.root / 'opening_comparison.json')['recommended_id'])
        self.assertFalse((self.project.root / 'selection.json').exists())

    def test_plan_fallback_skips_vetoed_candidates_and_uses_editorial_rank(self):
        original = self.flow.call_json
        def call(task, prompt):
            result = original(task, prompt)
            if task == 'candidate_comparative_review':
                self.block(result, 1)
                result['comparisons'][2]['rank'] = 4
                result['comparisons'][3]['rank'] = 3
            return result
        self.flow.call_json = call
        self.flow.develop()
        plan = self.flow.plan
        first = True
        def rejected_once():
            nonlocal first
            if first:
                first = False
                raise StoryError('写前因果预审发现多处核心硬伤')
            return plan()
        self.flow.plan = rejected_once
        self.assertEqual(self.flow.generate()['decision'], 'passed')
        self.assertEqual(read_json(self.project.root / 'selection.json')['candidate_index'], 3)
