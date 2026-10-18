"""Portable standard-API entry point tests; no remote requests or credentials."""
import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import yaml

from short_fiction_agent.llm import OpenAICompatibleClient
from short_fiction_agent.models import ModelConfig
from short_fiction_agent.story_studio.contracts import StoryBrief


class PublicAPITests(unittest.TestCase):
    def test_public_brief_targets_thirty_thousand_without_implicit_ceiling(self):
        root = Path(__file__).resolve().parents[2]
        brief = StoryBrief.from_dict(yaml.safe_load((root / 'examples/short-story.yaml').read_text()))
        self.assertEqual(brief.format, 'short_story')
        self.assertEqual(brief.length['target'], 30000)
        self.assertEqual(brief.length['min'], 27000)
        self.assertIsNone(brief.length['max'])
        self.assertIn('fanqie_short', brief.channels)

    def test_standard_api_sends_complete_prompt_and_reads_complete_response(self):
        client = OpenAICompatibleClient(ModelConfig(provider='openai_compatible',
            base_url='https://api.example.com/v1/', api_key='test-placeholder', model='test-model'))
        prompt = '导语、正文和审读规则均应完整传入。\n' * 20
        content = json.dumps({'result': '完整回答，保留人物行动、回应和后果。'}, ensure_ascii=False)
        response = Mock(status_code=200)
        response.json.return_value = {'model': 'test-model', 'choices': [
            {'message': {'content': content}, 'finish_reason': 'stop'}]}
        with patch('requests.post', return_value=response) as post:
            self.assertEqual(client.generate(prompt, 'chief_editor_review'), content)
        self.assertEqual(post.call_args.args[0], 'https://api.example.com/v1/chat/completions')
        self.assertEqual(post.call_args.kwargs['json']['messages'][-1]['content'], prompt)
        self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer test-placeholder')

    def test_oversized_input_never_sends_truncated_manuscript(self):
        client = OpenAICompatibleClient(ModelConfig(provider='openai_compatible',
            base_url='https://api.example.com/v1', api_key='test-placeholder',
            model='test-model', max_prompt_tokens=5))
        prompt = '必须完整保留的正文与导语。' * 20
        with patch('requests.post') as post:
            with self.assertRaisesRegex(ValueError, '不会截断正文或规则'):
                client.generate(prompt, 'chief_editor_review')
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
