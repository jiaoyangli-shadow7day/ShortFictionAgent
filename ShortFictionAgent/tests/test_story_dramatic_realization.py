"""Core scene evidence must survive high scores and summary-only fulfillment.

Fixtures test the decision protocol, not whether a model can judge literature.
"""
import copy
import unittest

from short_fiction_agent.story_studio import whole_story
from short_fiction_agent.story_studio.contracts import StoryError
from test_story_studio import structure_fixture


class DramaticRealizationTests(unittest.TestCase):
    def setUp(self):
        self.body = ('她把两只杯子推过来，说只给我留了位置。\n'
                     '我问她是不是每个人都这样，她收回一只杯子，叫我自己猜。\n'
                     '我抓住杯柄，说想让她只等我，她抬起头答应。\n'
                     '她靠在我肩上，我们喝完了那杯茶。')
        self.raw = structure_fixture(whole_story.paragraphs(self.body))

    def dramatic(self, raw=None):
        return next(row for row in (raw or self.raw)['checks']
                    if row['id'] == 'dramatic_realization')

    def test_core_scene_audit_cannot_be_omitted(self):
        del self.dramatic()['scene_checks']
        with self.assertRaisesRegex(StoryError, 'scene_checks'):
            whole_story.validate(self.raw, self.body)

    def test_declared_scene_needs_all_four_grounded_parts(self):
        self.dramatic()['scene_checks'][0]['beat_evidence']['response'] = []
        with self.assertRaisesRegex(StoryError, '缺少环节证据'):
            whole_story.validate(self.raw, self.body)

    def test_summary_cannot_coexist_with_sufficient_verdict(self):
        self.dramatic()['scene_checks'][0]['verdict'] = 'summarized'
        with self.assertRaisesRegex(StoryError, '不能给充分结论'):
            whole_story.validate(self.raw, self.body)

    def test_missing_scene_process_requires_replan_even_when_all_other_checks_pass(self):
        row = self.dramatic()
        row.update(verdict='thin', repair_scope='replan', reason='高潮只有打了一夜的结果',
                   fix_intent='重建对手回应与主角改写局面的过程')
        row['scene_checks'][0].update(verdict='summarized')
        row['scene_checks'][0]['beat_evidence']['turn'] = []
        result = whole_story.validate(self.raw, self.body)
        self.assertIn('whole_story:dramatic_realization:thin', whole_story.gate(self.body, result))
        self.assertEqual(whole_story.issues(result)[0]['repair_scope'], 'replan')

    def test_quiet_mutual_affection_can_supply_all_parts_without_an_accident(self):
        beats = self.dramatic()['scene_checks'][0]['beat_evidence']
        beats.update(attempt=[0], response=[1], turn=[2], payoff=[3])
        result = whole_story.validate(self.raw, self.body)
        self.assertEqual(whole_story.gate(self.body, result), [])
        self.assertEqual(result['checks'][-1]['scene_checks'][0]['evidence']['payoff'][0]['quote'],
                         self.body.splitlines()[3])

    def test_saved_scene_quotes_cannot_drift(self):
        result = whole_story.validate(self.raw, self.body)
        result['checks'][-1]['scene_checks'][0]['evidence']['turn'][0]['quote'] = '另一个故事'
        with self.assertRaisesRegex(StoryError, '不匹配'):
            whole_story.gate(self.body, result)

    def test_out_of_range_scene_evidence_is_rejected(self):
        self.dramatic()['scene_checks'][0]['beat_evidence']['payoff'] = [100]
        with self.assertRaisesRegex(StoryError, '编号无效'):
            whole_story.validate(self.raw, self.body)

    def test_old_four_criterion_reports_do_not_pass_new_protocol(self):
        old = copy.deepcopy(self.raw)
        old['checks'] = old['checks'][:-1]
        with self.assertRaisesRegex(StoryError, '检查缺失'):
            whole_story.validate(old, self.body)

    def test_verified_cases_reach_model_and_change_guidance_cache(self):
        from unittest.mock import patch
        from short_fiction_agent.story_studio import research_guidance as r
        context = r.research_context({'format': 'short_story',
            'research_preferences': {'reader_tags': ['宫斗宅斗']}}, task='write_short_story')
        cards = r.model_research_context(context)['dramatic_realization_research']['evidence']
        self.assertEqual(len(cards), 3)
        self.assertTrue(any(row['read_scope'] == 'full_story_and_ending' for row in cards))
        baseline = r.guidance_fingerprint()
        library = r.dramatic_realization_library()
        library['rules'].append('新的经核验场景机制')
        with patch.object(r, 'dramatic_realization_library', return_value=library):
            self.assertNotEqual(baseline, r.guidance_fingerprint())

    def test_author_rejection_blocks_same_prose_and_follows_revision_prompts(self):
        import test_story_studio
        from short_fiction_agent.story_studio.quality import current_review
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        test_story_studio.StoryStudioTests.setUp(self)
        record = self.project.save_draft(self.body)
        rejection = self.project.reject_draft('核心决战只概述结果，作者要求重写')
        self.assertEqual(rejection['content_hash'], record['content_hash'])
        with self.assertRaisesRegex(StoryError, '独立复核否决'):
            current_review(self.project)
        revised = self.project.save_draft(self.body + '\n新的正文。',
            artifact_id=record['artifact_id'], parent_revision=record['revision'])
        self.assertNotEqual(revised['content_hash'], rejection['content_hash'])
        class Capture:
            max_prompt_tokens = 100000
            def generate(self, prompt, task):
                self.prompt = prompt
                return '响应'
        llm = Capture()
        StoryWorkflow(self.project, llm).call_text('whole_story_review', '审读当前正文')
        self.assertIn('核心决战只概述结果', llm.prompt)
        self.assertIn('不能用旧稿通过', llm.prompt)


if __name__ == '__main__':
    unittest.main()
