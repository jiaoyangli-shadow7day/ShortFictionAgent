"""A factual inference must not silently stand in for emotional progression."""
import copy
import json
import unittest
from short_fiction_agent.story_studio.blueprint import blueprint_to_plan, validate_blueprint
from short_fiction_agent.story_studio.contracts import StoryError
from test_story_studio import ScriptedLLM

class BlueprintEmotionTests(unittest.TestCase):
    def blueprint(self):
        return json.loads(ScriptedLLM('正文').generate('', 'story_blueprint'))

    def test_same_fact_can_support_different_emotional_choices(self):
        first = self.blueprint()
        second = copy.deepcopy(first)
        second['beats'][0]['emotional_change'] = '从焦虑转为警惕：店主藏信，可能利用她'
        self.assertEqual(first['beats'][0]['inference'], second['beats'][0]['inference'])
        a, b = blueprint_to_plan(first)['scenes'][0], blueprint_to_plan(second)['scenes'][0]
        self.assertEqual(a['new_information'], b['new_information'])
        self.assertNotEqual(a['emotional_change'], b['emotional_change'])
        self.assertIn('惧怕', a['emotional_change'])

    def test_legacy_or_malformed_emotion_requires_replanning(self):
        for value in (None, '', '  ', ['焦虑']):
            with self.subTest(value=value):
                raw = self.blueprint()
                if value is None:
                    raw['beats'][0].pop('emotional_change')
                else:
                    raw['beats'][0]['emotional_change'] = value
                with self.assertRaisesRegex(StoryError, '独立情绪变化'):
                    validate_blueprint(raw)
                with self.assertRaises(StoryError):
                    blueprint_to_plan(raw)

if __name__ == '__main__':
    unittest.main()
