"""Check guidance delivery and evidence gates, not simulated reader preferences."""
import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.distribution import (
    PREPARATION_CHECKS, PublicationPreparationAgent, validate_preparation,
)
from short_fiction_agent.story_studio.selection_review import OPENING_RUBRIC
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_opening_binding import OpeningLLM


class OpeningFollowthroughTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = '林禾发现信不见了。' + '她追查记忆交易的线索，决定用自己的记忆换回那封信。' * 31 + '她找回信，也记得代价。'

    def flow(self, fmt='micro_story', target=900):
        project = StoryProject(Path(self.tmp.name) / fmt)
        project.create(StoryBrief.from_dict({'format': fmt,
            'length': {'target': target, 'min': int(target * .7), 'max': int(target * 1.3)},
            'author_input': {'premise': '林禾找信', 'locked_facts': []}}))
        llm = OpeningLLM(self.body)
        return project, llm, StoryWorkflow(project, llm)

    def test_guidance_reaches_actual_planning_writing_editing_and_review(self):
        project, llm, flow = self.flow()
        flow.develop(); flow.generate()
        for task in ('opening_candidates', 'opening_comparative_review', 'story_blueprint',
                     'write_micro_story', 'chief_editor_review'):
            matching = [prompt for name, prompt in llm.prompts if name == task]
            self.assertTrue(matching, task)
            # Review prompts deduplicate the nested causal rubric without losing any rule.
            for prompt in matching:
                if task == 'chief_editor_review':
                    self.assertIn('source_cards', prompt)
                    self.assertIn('导语', prompt)
                    self.assertIn('全文', prompt)
                else:
                    for value in OPENING_RUBRIC.values():
                        self.assertIn(json.dumps(value, ensure_ascii=False), prompt, task)
        # Existing round count is preserved: no dedicated model call added for this rubric.
        self.assertEqual(llm.tasks.count('chief_editor_review'), 1)
        self.assertNotIn('cold_read_review', llm.tasks)

    def test_guidance_reaches_longer_scene_based_story(self):
        _, llm, flow = self.flow('short_story', 8000)
        flow.develop(); plan = flow.plan(); flow._draft_text(plan)
        rubric = json.dumps(OPENING_RUBRIC, ensure_ascii=False)
        for task in ('story_plan', 'write_story_scene'):
            prompts = [p for t, p in llm.prompts if t == task]
            self.assertTrue(prompts)
            self.assertTrue(all(any(json.dumps(OPENING_RUBRIC, ensure_ascii=False, sort_keys=sorted_keys) in p
                                    for sorted_keys in (False, True)) for p in prompts), task)

    def checks(self, body):
        return {'checks': [dict(id=key, verdict='pass', quote=body[:12],
            reason='具体场景形成选择并继续行动', revision='') for key in PREPARATION_CHECKS]}

    def test_missing_new_checks_and_late_evidence_fail_closed(self):
        body = '林禾发现信不见了。' + '甲' * 1100 + '她拿到了那封失而复得的信。'
        result = self.checks(body)
        result['checks'] = [row for row in result['checks'] if row['id'] != 'opening_followthrough']
        with self.assertRaisesRegex(StoryError, '完整覆盖'):
            validate_preparation(result, body)
        for key in ('opening_followthrough', 'title_promise_progress'):
            result = self.checks(body)
            next(row for row in result['checks'] if row['id'] == key)['quote'] = body[-14:]
            with self.assertRaisesRegex(StoryError, '前1000'):
                validate_preparation(result, body)
        # Short quiet prose remains valid; there is no minimum word count for these checks.
        short = '她替我挑走葱花，我才发现她还记得。'
        self.assertEqual(len(validate_preparation(self.checks(short), short)), len(PREPARATION_CHECKS))

    def test_specific_followthrough_revision_blocks_publication_readiness(self):
        project, _, flow = self.flow()
        flow.develop(); flow.generate()
        def reviewer(task, prompt):
            response = self.checks(project.active_draft()[1])
            row = next(row for row in response['checks'] if row['id'] == 'opening_followthrough')
            row.update(verdict='revise', reason='第一句之后反复说明相同调查背景', revision='合并重复说明并保留选择')
            return response
        result = PublicationPreparationAgent(project, reviewer).review(channel='fanqie_short',
            titles=['找回信'], synopsis='失去一封信后的选择', category='custom')
        self.assertNotEqual(result['decision'], 'editorial_ready')

    def test_repeated_early_quote_does_not_validate_a_late_evidence_id(self):
        body = '她看着那封未寄出的信。' * 150
        response = self.checks(body)
        row = next(row for row in response['checks'] if row['id'] == 'opening_followthrough')
        row.pop('quote'); row['evidence_id'] = 'e1040'
        with self.assertRaisesRegex(StoryError, '前1000'):
            validate_preparation(response, body)


if __name__ == '__main__':
    unittest.main()
