"""Real default chapter orchestration with scripted transport controls.

The model responses here verify continuation, repair and provenance. They are
deliberately not evidence that a manuscript is engaging or marketable.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import reader_rewards, research_guidance, target_reader
from short_fiction_agent.story_studio.chapter_production import ChapterReplanRequired
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_text
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, _prompt_object


class ChapterTransportLLM(ScriptedLLM):
    # Output-part splitting has its own controls; these tests isolate actual
    # chapter boundaries from the provider's transport capacity.
    max_story_output_chars = 100000
    max_prompt_tokens = 200000

    def __init__(self, verdicts=()):
        super().__init__('协议控制正文。')
        self.verdicts = list(verdicts)
        self.writer_inputs, self.reader_inputs, self.writer_prompts, self.reader_prompts = [], [], [], []
        self.rewriter_inputs, self.rewriter_prompts = [], []

    def generate(self, prompt, task_type):
        if task_type == 'story_plan':
            response = json.loads(super().generate(prompt, task_type))
            data = _prompt_object(prompt, ('contract', 'candidate', 'opening'))
            # A declared promise requires a valid scene binding even in a
            # transport fixture; this does not claim the scene fulfills it.
            for reward in reader_rewards.effective_spec(data['contract']):
                response['promises'].append({'reader_reward_id': reward['id'],
                    'promise': '传输控制：仅验证原始承诺绑定，不证明兑现：' + reward['payoff'],
                    'payoff_scene': response['scenes'][-1]['scene_id'],
                    'required': reward['required']})
            return json.dumps(response, ensure_ascii=False)
        if task_type == 'write_story_scene':
            self.tasks.append(task_type)
            data = _prompt_object(prompt, ('scene', 'previous_text', 'chapter_unit'))
            self.writer_inputs.append(data)
            self.writer_prompts.append(prompt)
            return f'原章专用事实RAW-{data["scene"]["scene_id"]}：她递来车票，他当面改掉原来的返程车次。'
        if task_type == 'rewrite_story_chapter':
            self.tasks.append(task_type)
            self.rewriter_inputs.append(_prompt_object(prompt, ('contract', 'reader_repair', 'chapter')))
            self.rewriter_prompts.append(prompt)
            return '第一章\n\n修后专用事实FIXED-CH1：他接过车票，当面改签，答应陪她留下来。'
        if task_type == 'chief_editor_chapter_reader':
            data = _prompt_object(prompt, ('chapter_paragraphs', 'observation_windows'))
            self.reader_inputs.append(data)
            self.reader_prompts.append(prompt)
            response = json.loads(super().generate(prompt, task_type))
            last = data['chapter_paragraphs'][-1]
            response['actual_handoff']['facts'] = [{'text': last['text'], 'evidence_ids': [last['id']]}]
            verdict = self.verdicts.pop(0) if self.verdicts else 'continue'
            if verdict != 'continue':
                response['verdict'] = verdict
                response['windows'][0].update(status='thin',
                    repair_scope='structure' if verdict == 'replan' else 'expression',
                    reason='协议负控制：本段重复已经兑现的回应。',
                    fix_intent='重排场景和回报' if verdict == 'replan' else '删去重复解释并保留实际接招')
            return json.dumps(response, ensure_ascii=False)
        return super().generate(prompt, task_type)


class ChapterProductionIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def setup_flow(self, verdicts=(), *, brief_overrides=None):
        project = StoryProject(Path(self.temp.name) / 'story')
        project.create(StoryBrief.from_dict({'format': 'short_story',
            'channels': ['fanqie_short', 'generic_text'],
            'length': {'min': 1, 'target': 6000, 'max': None},
            'author_input': {'premise': '林禾找回一封信，对方作出当面回应'},
            **(brief_overrides or {})}))
        llm = ChapterTransportLLM(verdicts)
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        plan = flow.plan()
        llm.tasks.clear()
        return project, llm, flow, plan

    def test_default_short_story_reads_each_real_chapter_before_writing_next(self):
        _, llm, flow, plan = self.setup_flow()
        body = flow._draft_text(plan)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'] * 4)
        self.assertIn('RAW-s3', body)
        self.assertEqual([row['reading_scope']['chapter_id'] for row in llm.reader_inputs],
                         [row['chapter_id'] for row in plan['chapters']])
        self.assertFalse(llm.reader_inputs[0]['source_history'])
        self.assertTrue(llm.reader_inputs[-1]['reading_scope']['is_final'])

    def test_twice_failed_first_chapter_never_writes_second_or_silently_passes_on_resume(self):
        _, llm, flow, plan = self.setup_flow(('revise', 'revise'))
        with self.assertRaises(ChapterReplanRequired):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks.count('write_story_scene'), 1)
        self.assertEqual(llm.tasks.count('rewrite_story_chapter'), 1)
        self.assertEqual(llm.tasks.count('chief_editor_chapter_reader'), 2)
        before = list(llm.tasks)
        with self.assertRaises(ChapterReplanRequired):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks, before)

    def test_structural_finding_saves_replan_request_without_local_rewrite(self):
        project, llm, flow, plan = self.setup_flow(('replan',))
        with self.assertRaises(ChapterReplanRequired):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'])
        request = read_json(project.root / 'structure_repair_request.json')
        self.assertEqual(request['origin'], 'chapter_reader')
        self.assertEqual(request['chapter_id'], plan['chapters'][0]['chapter_id'])
        self.assertIn('RAW-s0', Path(request['partial_body_path']).read_text(encoding='utf-8'))
        self.assertIsNone(read_json(project.root / 'project.json')['active_artifact_id'])

    def test_local_rewrite_is_reread_and_next_chapter_receives_repaired_source_and_handoff(self):
        _, llm, flow, plan = self.setup_flow(('revise', 'continue'))
        body = flow._draft_text(plan)
        self.assertEqual(llm.tasks[:5], ['write_story_scene', 'chief_editor_chapter_reader',
            'rewrite_story_chapter', 'chief_editor_chapter_reader', 'write_story_scene'])
        self.assertIn('FIXED-CH1', body)
        self.assertNotIn('RAW-s0', body)
        next_input = llm.writer_inputs[1]
        self.assertIn('FIXED-CH1', next_input['previous_text'])
        self.assertNotIn('RAW-s0', next_input['previous_text'])
        self.assertIn('FIXED-CH1', next_input['chapter_reader_handoff'][0]['actual_handoff']['facts'][0]['text'])
        self.assertEqual(llm.tasks.count('rewrite_story_chapter'), 1)

    def test_completed_resume_uses_same_source_and_reviews_without_new_model_calls(self):
        project, llm, flow, plan = self.setup_flow(('revise', 'continue'))
        first = flow._draft_text(plan)
        before = list(llm.tasks)
        resumed = StoryWorkflow(project, llm, max_calls=100)._draft_text(plan)
        self.assertEqual(first, resumed)
        self.assertEqual(llm.tasks, before)

    def test_updated_writer_instruction_does_not_resume_an_old_completed_chapter(self):
        """An outer completed-body cache must not bypass new writing criteria."""
        _, llm, flow, plan = self.setup_flow()
        flow._draft_text(plan, max_chapters=1)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'])
        changed = target_reader.WRITE_INSTRUCTION + '传输回归：本次新写作约束。'
        with patch.object(target_reader, 'WRITE_INSTRUCTION', changed):
            # Research criteria also bind selection. Refresh that prerequisite
            # through the real workflow while retaining the same executable
            # plan, so this test reaches the completed-chapter cache itself.
            flow.develop(refresh=True)
            flow._selection()
            llm.tasks.clear()
            flow._draft_text(plan, max_chapters=1)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'])
        self.assertEqual(len(llm.writer_inputs), 2)
        self.assertIn(changed, llm.writer_prompts[-1])

    def test_writer_and_local_rewrite_receive_original_entry_not_outline_self_description(self):
        """Transmission control; assertions do not establish literary quality."""
        project, llm, flow, plan = self.setup_flow(('revise', 'continue'), brief_overrides={
            'title': '原始标题：十次预测机会',
            'reader_promise': 'ORIGINAL-ENTRY：预测优势改变命运并得到主动偏爱。',
            'audience': {'description': '男频脑洞情感读者',
                         'expectations': ['想亲眼看到主角用信息优势赢下一次机会。']}})
        plan = copy.deepcopy(plan)
        plan['core_reading_experience'] = 'PLAN-SELF-DESCRIPTION：只需学会成熟相处。'
        before = copy.deepcopy(plan)
        flow._draft_text(plan, max_chapters=1)
        expected = target_reader.entry_context(project.contract(), include_author_promise=True)
        original_promise = project.contract()['reader_promise']
        self.assertTrue(expected['enabled'])
        self.assertEqual(llm.writer_inputs[0]['target_reader_context'], expected)
        self.assertEqual(llm.rewriter_inputs[0]['target_reader_context'], expected)
        self.assertIn(target_reader.WRITE_INSTRUCTION, llm.writer_prompts[0])
        self.assertIn(target_reader.WRITE_INSTRUCTION, llm.rewriter_prompts[0])
        self.assertIn(original_promise, str(llm.writer_inputs[0]['target_reader_context']))
        self.assertNotIn('PLAN-SELF-DESCRIPTION', str(llm.writer_inputs[0]['target_reader_context']))
        self.assertNotIn(original_promise, llm.reader_prompts[0])
        self.assertEqual(plan, before)

    def test_tampered_completed_source_is_rejected_without_regeneration(self):
        project, llm, flow, plan = self.setup_flow()
        flow._draft_text(plan)
        original = next((project.root / 'chapter_checkpoints').glob('*/original.md'))
        write_text(original, original.read_text(encoding='utf-8') + '\n被其他稿件污染的正文。')
        before = list(llm.tasks)
        with self.assertRaisesRegex(StoryError, '来源或内容损坏'):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks, before)

    def test_public_prefix_stays_partial_and_does_not_replace_existing_active_manuscript(self):
        project, llm, flow, plan = self.setup_flow()
        old = project.save_draft('原先已保存的完整稿件。')
        sample = flow.draft_prefix(plan, max_chapters=1)
        self.assertFalse(sample['complete'])
        self.assertEqual(sample['scope'], 'prefix_only_not_submission')
        self.assertEqual(sample['completed_chapters'], 1)
        self.assertEqual(sample['planned_chapters'], 4)
        current, current_body = project.active_draft()
        self.assertEqual(current['artifact_id'], old['artifact_id'])
        self.assertEqual(current['revision'], old['revision'])
        self.assertEqual(current_body.strip(), '原先已保存的完整稿件。')
        self.assertFalse((project.root / 'generation_receipts').exists())
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'])

    def test_multiple_scenes_in_one_chapter_get_one_chapter_review(self):
        _, llm, flow, plan = self.setup_flow()
        plan = copy.deepcopy(plan)
        plan['chapters'] = [
            {'chapter_id': 'one', 'title': '第一章 第一次交锋', 'scene_ids': ['s0', 's1']},
            {'chapter_id': 'two', 'title': '第二章 实际后果', 'scene_ids': ['s2', 's3']}]
        flow._draft_text(plan)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader',
            'write_story_scene', 'chief_editor_chapter_reader',
            'write_story_scene', 'write_story_scene', 'chief_editor_chapter_reader'])
        self.assertIn('RAW-s0', str(llm.reader_inputs[0]['chapter_paragraphs']))
        self.assertNotIn('RAW-s1', str(llm.reader_inputs[0]['chapter_paragraphs']))
        self.assertIn('RAW-s1', str(llm.reader_inputs[1]['chapter_paragraphs']))
        self.assertIn('RAW-s0', llm.writer_inputs[1]['previous_text'])
        self.assertEqual(llm.writer_inputs[0]['chapter_unit']['scene_indices'], [0, 1])

    def test_multi_scene_first_chapter_stops_failed_opening_trial_before_second_scene(self):
        project, llm, flow, plan = self.setup_flow(('replan',))
        plan = copy.deepcopy(plan)
        plan['chapters'] = [
            {'chapter_id': 'one', 'title': '第一章 第一次交锋', 'scene_ids': ['s0', 's1']},
            {'chapter_id': 'two', 'title': '第二章 实际后果', 'scene_ids': ['s2', 's3']}]
        with self.assertRaises(ChapterReplanRequired):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader'])
        self.assertEqual(llm.reader_inputs[0]['reading_scope']['chapter_id'], 'one-opening-trial')
        request = read_json(project.root / 'structure_repair_request.json')
        self.assertEqual(request['chapter_id'], 'one-opening-trial')
        partial = Path(request['partial_body_path']).read_text(encoding='utf-8')
        self.assertIn('RAW-s0', partial)
        self.assertNotIn('RAW-s1', partial)
        self.assertIsNone(read_json(project.root / 'project.json')['active_artifact_id'])

    def _format_repair_cannot_downgrade(self, *, keep_verdict, reclassify_detail=False,
                                      reading_value_only=False):
        project, _, _, plan = self.setup_flow()
        class InvalidChapterReaderLLM(ChapterTransportLLM):
            def generate(inner, prompt, task_type):
                if task_type == 'chief_editor_chapter_reader':
                    response = json.loads(super().generate(prompt, task_type))
                    if reading_value_only:
                        response['reading_value_check'] = copy.deepcopy(response['windows'][0])
                        response['windows'][0].update(status='effective', repair_scope='none')
                    problem = response['reading_value_check'] if reading_value_only else response['windows'][0]
                    problem['evidence_ids'] = ['current:p999']
                    inner.completed_negative = copy.deepcopy(response)
                    return json.dumps(response, ensure_ascii=False)
                if task_type == 'json_format_repair':
                    inner.tasks.append(task_type)
                    # An invalid evidence ID authorizes representation recovery,
                    # never an editorial reroll that erases the rejection.
                    response = copy.deepcopy(inner.completed_negative)
                    problem = response['reading_value_check'] if reading_value_only else response['windows'][0]
                    if reclassify_detail:
                        problem.update(evidence_ids=['current:p0'], editorial_priority='copyedit_detail',
                                       reading_effect='金额尾数不影响阅读。')
                    else:
                        problem.update(status='effective', repair_scope='none',
                            reason='伪造的格式恢复通过', fix_intent='保留', evidence_ids=['current:p0'])
                    if not keep_verdict:
                        response['verdict'] = 'continue'
                    return json.dumps(response, ensure_ascii=False)
                return super().generate(prompt, task_type)
        llm = InvalidChapterReaderLLM(('replan',))
        flow = StoryWorkflow(project, llm, max_calls=100)
        expected = '丢失或改写原负面问题' if keep_verdict else '改变原语义结论'
        with self.assertRaisesRegex(StoryError, expected):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks, ['write_story_scene', 'chief_editor_chapter_reader', 'json_format_repair'])
        self.assertEqual(len(llm.writer_inputs), 1)
        self.assertIsNone(read_json(project.root / 'project.json')['active_artifact_id'])

    def test_format_repair_cannot_turn_original_replan_into_continue(self):
        self._format_repair_cannot_downgrade(keep_verdict=False)

    def test_format_repair_cannot_erase_negative_observation_while_keeping_replan_label(self):
        self._format_repair_cannot_downgrade(keep_verdict=True)

    def test_format_repair_cannot_reclassify_core_problem_as_optional_copyediting(self):
        self._format_repair_cannot_downgrade(keep_verdict=True, reclassify_detail=True)

    def test_format_repair_cannot_erase_original_reading_value_problem(self):
        self._format_repair_cannot_downgrade(keep_verdict=True, reading_value_only=True)

    def test_writer_keeps_research_and_guide_while_reader_is_blind_to_future_plan(self):
        _, llm, flow, plan = self.setup_flow()
        plan = copy.deepcopy(plan)
        plan['ending_resolution'] = 'FUTURE-PLAN-SECRET：结尾才能发生的交换结果。'
        flow._draft_text(plan, max_chapters=1)
        writer = llm.writer_prompts[0]
        reader = llm.reader_prompts[0]
        self.assertIn(research_guidance.guidance_fingerprint(), writer)
        self.assertTrue(llm.writer_inputs[0]['submission_guide']['enabled'])
        self.assertTrue(llm.writer_inputs[0]['submission_guide']['semantic_requirements'])
        self.assertIn('FUTURE-PLAN-SECRET', writer)
        self.assertNotIn('FUTURE-PLAN-SECRET', reader)
        self.assertNotIn('whole_story_orientation', reader)
        self.assertNotIn('ending_resolution', reader)
        self.assertNotIn('submission_guide', reader)
        self.assertNotIn(research_guidance.guidance_fingerprint(), reader)


if __name__ == '__main__':
    unittest.main()
