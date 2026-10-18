"""Finite resource budgets for chapter production; no literary quality claims."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from short_fiction_agent.story_studio import cli, run_control
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from tests.narrative_fixtures import generated_scene_plan_control
from test_story_studio import ScriptedLLM, _prompt_object


class ThirtyChapterLLM(ScriptedLLM):
    max_prompt_tokens = 200000

    def generate(self, prompt, task_type):
        if task_type == 'story_plan':
            plan = json.loads(super().generate(prompt, task_type))
            template = plan['scenes'][0]
            plan['scenes'] = []
            for index in range(30):
                scene = copy.deepcopy(template)
                scene.update(scene_id=f's{index}', budget=1000)
                plan['scenes'].append(scene)
            plan.pop('chapters')
            return json.dumps(generated_scene_plan_control(plan), ensure_ascii=False)
        if task_type == 'write_story_scene':
            self.tasks.append(task_type)
            data = _prompt_object(prompt, ('scene', 'scene_count'))
            return '答' * data['target_visible_chars']
        return super().generate(prompt, task_type)


class ChapterCallBudgetTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def project(self, name='story', story_format='short_story', review_mode='chief_editor'):
        project = StoryProject(self.root / name)
        project.create(StoryBrief.from_dict({'format': story_format, 'review_mode': review_mode,
            'channels': ['generic_text'], 'length': {'min': 1, 'target': 30000, 'max': None},
            'author_input': {'premise': '林禾找回一封信'}}))
        return project

    def test_default_chapter_budget_completes_thirty_chapters_and_resumes_without_new_calls(self):
        project = self.project()
        llm = ThirtyChapterLLM('预算传输控制。')
        flow = StoryWorkflow(project, llm)
        self.assertEqual(flow.max_calls, 120)
        flow.develop()
        plan = flow.plan()
        body = flow._draft_text(plan)
        self.assertEqual(llm.tasks.count('write_story_scene'), 30)
        self.assertEqual(llm.tasks.count('chief_editor_chapter_reader'), 30)
        state = run_control.load(project)
        self.assertGreater(state['used'], 60)
        self.assertLess(state['used'], state['limit'])
        calls = len(llm.tasks)
        resumed = StoryWorkflow(StoryProject(project.root), llm)
        self.assertEqual(resumed._draft_text(plan), body)
        self.assertEqual(len(llm.tasks), calls)
        self.assertEqual(run_control.load(project), state)

    def test_explicit_small_limit_wins_and_cannot_be_expanded_by_reopening(self):
        project = self.project()
        llm = ScriptedLLM('调用控制。')
        flow = StoryWorkflow(project, llm, max_calls=2)
        self.assertEqual(flow.max_calls, 2)
        flow.call_text('write_micro_story', '第一回')
        flow.call_text('write_micro_story', '第二回')
        for limit in (None, 200):
            with self.subTest(limit=limit):
                resumed = StoryWorkflow(StoryProject(project.root), llm, max_calls=limit)
                self.assertEqual(resumed.max_calls, 2)
                with self.assertRaisesRegex(StoryError, '累计模型调用预算'):
                    resumed.call_text('write_micro_story', '不能借恢复扩容')
        self.assertEqual(len(llm.tasks), 2)

    def test_existing_forty_call_ledger_stays_forty_until_explicit_continue(self):
        project = self.project()
        run_control.reserve(project, limit=40)
        resumed = StoryWorkflow(project, ScriptedLLM('预算控制。'))
        self.assertEqual(resumed.max_calls, 40)
        self.assertEqual(run_control.load(project)['limit'], 40)
        run_control.authorize_continue(project, extra_calls=5)
        self.assertEqual(StoryWorkflow(project, resumed.llm).max_calls, 45)

    def test_explicit_lower_limit_tightens_existing_default_and_survives_resume(self):
        project = self.project()
        llm = ScriptedLLM('调用控制。')
        StoryWorkflow(project, llm).call_text('write_micro_story', '已用一次默认预算')
        self.assertEqual(run_control.load(project)['limit'], 120)
        tightened = StoryWorkflow(project, llm, max_calls=2)
        self.assertEqual(tightened.max_calls, 2)
        self.assertEqual(run_control.load(project)['limit'], 2)
        tightened.call_text('write_micro_story', '只剩一次')
        resumed = StoryWorkflow(StoryProject(project.root), llm)
        with self.assertRaisesRegex(StoryError, '累计模型调用预算'):
            resumed.call_text('write_micro_story', '恢复不能撤销作者收紧')
        self.assertEqual(len(llm.tasks), 2)

    def test_cli_and_stop_before_first_call_use_the_same_finite_default(self):
        project = self.project()
        with (patch.object(cli, 'load_model_config', return_value=SimpleNamespace(provider='test')),
              patch.object(cli, 'create_llm_client', return_value=ScriptedLLM('传输控制。'))):
            flow = cli._workflow(project)
        self.assertEqual(flow.max_calls, 120)
        stopped = run_control.stop(project)
        self.assertEqual(stopped['limit'], 120)
        self.assertEqual(stopped['used'], 0)
        with self.assertRaisesRegex(StoryError, '已停止'):
            flow.call_text('write_micro_story', '停止不得调用')

    def test_other_paths_keep_forty_and_invalid_explicit_limits_are_rejected(self):
        for story_format, review_mode in (('micro_story', 'chief_editor'),
                                          ('short_story', 'deep_review')):
            with self.subTest(format=story_format, review_mode=review_mode):
                project = self.project(story_format + review_mode, story_format, review_mode)
                self.assertEqual(StoryWorkflow(project, ScriptedLLM('预算控制。')).max_calls, 40)
        for limit in (0, -1, True, 2.5):
            with self.subTest(limit=limit), self.assertRaisesRegex(StoryError, '预算必须是正整数'):
                StoryWorkflow(self.project('invalid-' + str(limit)), ScriptedLLM('预算控制。'), max_calls=limit)


if __name__ == '__main__':
    unittest.main()
