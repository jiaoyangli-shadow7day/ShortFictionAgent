"""Generation capacity must never redefine an author's length contract."""
import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio.contracts import StoryBrief, visible_count
from short_fiction_agent.story_studio.quality import length_status
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from short_fiction_agent.story_studio import whole_story
from test_story_studio import ScriptedLLM


class GenerationStrategyTests(unittest.TestCase):
    def project(self, target=12000, minimum=11000, maximum=15000, reader_promise='', review_mode='chief_editor'):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = StoryProject(Path(tmp.name) / 'story')
        project.create(StoryBrief.from_dict({'format': 'short_story', 'review_mode': review_mode,
            'length': {'target': target, 'min': minimum, 'max': maximum},
            'reader_promise': reader_promise,
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        return project

    def test_twelve_and_twenty_thousand_default_checks_chapters_before_continuing(self):
        for target in (12000, 20000):
            with self.subTest(target=target):
                project = self.project(target, target - 1000, target + 2000)
                class Capture(ScriptedLLM):
                    max_prompt_tokens = 200000
                    def generate(inner, prompt, task_type):
                        if task_type == 'story_plan':
                            data = json.loads(super().generate(prompt, task_type))
                            scene_count = 6 if target == 12000 else 8
                            template = data['scenes'][0]
                            data['scenes'] = [{**template, 'scene_id': f's{i}'}
                                              for i in range(scene_count)]
                            data['chapters'] = [{'chapter_id': f'c{i}', 'title': '找信', 'scene_ids': [f's{i}']}
                                                for i in range(scene_count)]
                            return json.dumps(data, ensure_ascii=False)
                        if task_type == 'write_story_scene':
                            inner.tasks.append(task_type)
                            inner.writer_prompt = prompt
                            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                            return '人' * data['target_visible_chars']
                        return super().generate(prompt, task_type)
                llm = Capture('人' * target)
                flow = StoryWorkflow(project, llm)
                flow.develop()
                # The fixture plan tests routing; production plans have their own preflight.
                plan = flow.plan()
                before = project.contract()
                body = flow._draft_text(plan)
                self.assertTrue(length_status(body, before)['within_range'])
                self.assertNotIn('write_short_story', llm.tasks)
                self.assertEqual(llm.tasks.count('write_story_scene'), len(plan['chapters']))
                self.assertEqual(llm.tasks.count('chief_editor_chapter_reader'), len(plan['chapters']))
                self.assertIn(whole_story.WRITE_INSTRUCTION, llm.writer_prompt)
                self.assertEqual(project.contract(), before)

    def test_short_whole_response_falls_back_and_resumes_without_lowering_minimum(self):
        project = self.project(7600, 6000, 9500, review_mode='deep_review')
        llm = ScriptedLLM('不足字数的整篇输出。')
        flow = StoryWorkflow(project, llm)
        flow.develop()
        plan = flow.plan()
        before = project.contract()
        body = flow._draft_text(plan)
        self.assertEqual(llm.scene_number, 4)
        self.assertTrue(length_status(body, before)['within_range'])
        raw = next((project.root / 'draft_attempts').glob('*/body.md'))
        self.assertIn('不足字数', raw.read_text())
        events = [json.loads(line) for line in
                  (project.root / 'runs/events.jsonl').read_text().splitlines()]
        fallback = next(e for e in events if e['event'] == 'draft_strategy_fallback')
        self.assertEqual(fallback['length_contract'], before['length'])
        calls, scenes = len(llm.tasks), llm.scene_number
        self.assertEqual(flow._draft_text(plan), body)
        self.assertEqual((len(llm.tasks), llm.scene_number), (calls, scenes))
        self.assertEqual(project.contract(), before)
        # A fast but undersized response remains blocked by the original gate.
        self.assertFalse(length_status(raw.read_text(), before)['within_range'])

    def test_provider_scene_strategy_keeps_length_and_runs_unified_chapter_review(self):
        project = self.project(7600, 6000, 9500)
        llm = ScriptedLLM('不能使用此整篇输出')
        flow = StoryWorkflow(project, llm, whole_draft_limit=0)
        flow.develop()
        body = flow._draft_text(flow.plan())
        self.assertTrue(length_status(body, project.contract())['within_range'])
        self.assertEqual(llm.tasks.count('scene_progression_review'), 0)
        self.assertEqual(llm.tasks.count('chief_editor_chapter_reader'), 4)
        self.assertFalse((project.root / 'draft_attempts').exists())

    def test_twenty_thousand_can_fall_back_to_eight_full_budget_scenes(self):
        project = self.project(20000, 19000, 22000)
        class TruncatedLLM(ScriptedLLM):
            max_prompt_tokens = 200000  # Explicit larger-capacity test provider; CLI defaults conservatively to 50000.
            def generate(inner, prompt, task_type):
                if task_type == 'story_plan':
                    data = json.loads(super().generate(prompt, task_type))
                    template = data['scenes'][0]
                    data['scenes'] = [{**template, 'scene_id': f's{i}'} for i in range(8)]
                    data['chapters'] = [{'chapter_id': f'c{i}', 'title': '找信', 'scene_ids': [f's{i}']}
                                        for i in range(8)]
                    return json.dumps(data, ensure_ascii=False)
                if task_type == 'write_story_scene':
                    inner.tasks.append(task_type)
                    inner.scene_number += 1
                    data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                    prefix = f'第{inner.scene_number}场。'
                    return prefix + '人' * (data['target_visible_chars'] - visible_count(prefix))
                return super().generate(prompt, task_type)
        llm = TruncatedLLM('整篇响应被截短。')
        flow = StoryWorkflow(project, llm)
        flow.develop()
        body = flow._draft_text(flow.plan())
        self.assertTrue(length_status(body, project.contract())['within_range'])
        self.assertEqual(llm.tasks.count('scene_progression_review'), 0)
        self.assertEqual(llm.tasks.count('chief_editor_chapter_reader'), 8)
        self.assertEqual(project.contract()['length']['min'], 19000)

    def test_whole_generation_still_receives_complete_final_review(self):
        project = self.project(7600, 6000, 9500, reader_promise='找回信并承担代价')
        class PromisedLLM(ScriptedLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'plan_payoff_chain_review':
                    inner.tasks.append(task_type)
                    return json.dumps({'checks': [{'id': 'core_promise',
                        'verdict': 'supported', 'reason': '流程测试桩，非语义质量结论',
                        'missing_link': '', 'repair_scope': 'none', 'scene_ids': ['s0']}]})
                output = super().generate(prompt, task_type)
                if task_type == 'story_plan':
                    data = json.loads(output)
                    data['promises'][0]['reader_reward_id'] = 'core_promise'
                    return json.dumps(data, ensure_ascii=False)
                return output
        llm = PromisedLLM('林禾开始调查。' + '她找回了信，也决定承担失去记忆的代价。' * 400)
        flow = StoryWorkflow(project, llm)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report['decision'], 'passed')
        self.assertTrue(report['review_coverage']['complete'])
        self.assertIn('chief_editor_review', llm.tasks)
        for task in ('cold_read_review', 'continuity_review', 'causal_challenge',
                     'reader_reward_review', 'whole_story_review', 'scene_progression_review'):
            self.assertNotIn(task, llm.tasks)


if __name__ == '__main__':
    unittest.main()
