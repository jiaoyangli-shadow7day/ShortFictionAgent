"""Check flow and evidence integrity, not human engagement or model quality."""
import json
from pathlib import Path
import tempfile
import unittest
import yaml

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.engagement import validate_scene_review, windows
from short_fiction_agent.story_studio.research_guidance import guidance_fingerprint
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, _prompt_object


def input_data(prompt):
    return json.JSONDecoder().raw_decode(prompt[prompt.index('{"previous_text":'):])[0]


class SceneLLM(ScriptedLLM):
    def __init__(self, *, stalled=False, unknown=False, never_fixed=False):
        super().__init__('正文')
        self.stalled, self.unknown, self.never_fixed = stalled, unknown, never_fixed
        self.scene_inputs, self.review_inputs, self.chief_inputs = [], [], []

    def generate(self, prompt, task_type):
        if task_type == 'chief_editor_review':
            self.chief_inputs.append(_prompt_object(prompt, ('author_contract', 'source_cards', 'paragraphs')))
        if task_type == 'write_story_scene':
            self.tasks.append(task_type)
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
            self.scene_inputs.append(data)
            prefix = '停滞标记' if self.stalled and data['scene_index'] == 0 else '安静回应'
            return prefix + '。' + f"当前场{data['scene_index']}。" + ('她留下口粮，他主动承诺陪她共同承担代价。' * 85)
        if task_type == 'scene_progression_review':
            data = input_data(prompt)
            self.review_inputs.append(data)
            if self.unknown:
                self.tasks.append(task_type)
                raise RuntimeError('transport outcome unknown')
            response = json.loads(super().generate(prompt, task_type))
            first = data['paragraphs'][0]
            if '停滞标记' in first or self.never_fixed:
                response['progression_observations'][0].update(verdict='stalled',
                    reason='测试桩：同一期待反复解释', fix_intent='改为女方主动回应')
            response['actual_handoff']['facts'][0]['text'] = (
                '她第一次回应了他的照顾' if '第一次回应' in first else '双方通过安静照顾作出承诺')
            return json.dumps(response, ensure_ascii=False)
        if task_type == 'repair_story_scene_progression':
            self.tasks.append(task_type)
            data = input_data(prompt)
            quote = '停滞标记' if '停滞标记' in data['body'] else '第一次回应'
            replacement = '第一次回应' if quote == '停滞标记' else '再次主动回应'
            return json.dumps({'edits':[{'quote':quote,'replacement':replacement,'kind':'rewrite',
                                        'benefit':'行动替代解释','risk':'须保持既有承诺'}]},ensure_ascii=False)
        return super().generate(prompt, task_type)


class SceneProgressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name)/'story')
        self.project.create(StoryBrief.from_dict({'genre':'甜宠','format':'short_story',
            'length':{'target':7600,'min':6000,'max':9500},
            'author_input':{'premise':'两人共同承担照顾家人的代价','locked_facts':[]}}))

    def flow(self, llm, *, review_mode='chief_editor'):
        if review_mode != 'chief_editor':
            brief = self.project.brief()
            brief.review_mode = review_mode
            (self.project.root/'brief.yaml').write_text(
                yaml.safe_dump(brief.to_dict(), allow_unicode=True, sort_keys=False))
        flow = StoryWorkflow(self.project, llm, max_calls=100)
        flow.develop()
        return flow, flow.plan()

    def test_repair_is_rechecked_before_next_scene_and_actual_handoff_is_used(self):
        llm = SceneLLM(stalled=True)
        flow, plan = self.flow(llm, review_mode='deep_review')
        body = flow._draft_text(plan)
        self.assertIn('第一次回应', body)
        self.assertNotIn('停滞标记', body)
        tasks = llm.tasks[llm.tasks.index('write_story_scene'):]
        self.assertEqual(tasks[:5], ['write_story_scene','scene_progression_review',
                                    'repair_story_scene_progression','scene_progression_review',
                                    'write_story_scene'])
        handoff = llm.scene_inputs[1]['prior_actual_progression'][0]['actual_handoff']
        self.assertEqual(handoff['facts'][0]['text'], '她第一次回应了他的照顾')
        self.assertIn('第一次回应', handoff['facts'][0]['quote'])
        self.assertNotIn('ending_resolution', llm.review_inputs[0])
        self.assertNotIn('scenes', llm.review_inputs[0])
        self.assertEqual(llm.review_inputs[0]['reader_contract']['reader_promise'],
                         self.project.contract()['reader_promise'])
        self.assertNotIn('ending_resolution', llm.review_inputs[0]['reader_contract'])
        # All review and repair inputs are bound to text; resume performs no new calls.
        count = len(llm.tasks)
        self.assertEqual(flow._draft_text(plan), body)
        self.assertEqual(len(llm.tasks), count)

    def test_quiet_interaction_passes_without_forced_new_conflict(self):
        llm = SceneLLM()
        flow, plan = self.flow(llm, review_mode='deep_review')
        flow._draft_text(plan)
        self.assertEqual(len(llm.scene_inputs),4)
        self.assertNotIn('repair_story_scene_progression',llm.tasks)
        self.assertEqual(len(llm.review_inputs),4)

    def test_default_chief_receives_complete_quiet_story_contract_and_research(self):
        llm = SceneLLM()
        flow, plan = self.flow(llm)
        body = flow._draft_text(plan)
        self.project.save_draft(body)
        report = flow.reviewer.review()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(report['review_mode'], 'chief_editor')
        self.assertEqual(len(llm.scene_inputs), 4)
        self.assertEqual(llm.review_inputs, [])
        self.assertNotIn('repair_story_scene_progression', llm.tasks)
        self.assertEqual(llm.tasks.count('chief_editor_review'), 1)
        data = llm.chief_inputs[0]
        contract = self.project.contract()
        self.assertEqual(data['author_contract']['author_input'], contract['author_input'])
        self.assertEqual(data['author_contract']['length'], contract['length'])
        self.assertEqual(data['source_cards']['fingerprint'], guidance_fingerprint())
        self.assertTrue(data['source_cards']['cards'])
        self.assertTrue(all(row['source_path'] for row in data['source_cards']['cards']))
        self.assertEqual([row['text'] for row in data['paragraphs']],
                         [line for line in body.splitlines() if line.strip()])
        self.assertEqual(report['content_hash'], digest(body))
        self.assertEqual(report['review_coverage']['spans'], [[0, len(body)]])

    def test_flowing_secondary_task_cannot_bypass_off_promise_repair(self):
        class OffPromiseLLM(SceneLLM):
            def generate(self, prompt, task_type):
                result = super().generate(prompt, task_type)
                if task_type == 'scene_progression_review':
                    response = json.loads(result)
                    for row in response['progression_observations']:
                        row['verdict'] = 'flowing'
                    if '停滞标记' in input_data(prompt)['paragraphs'][0]:
                        response['promise_alignment'].update(verdict='off_promise',
                            reason='步骤变化仅推动次要任务，未服务关系承诺',
                            fix_intent='改为已有关系的主动回应')
                    return json.dumps(response, ensure_ascii=False)
                return result
        llm = OffPromiseLLM(stalled=True)
        flow, plan = self.flow(llm, review_mode='deep_review')
        body = flow._draft_text(plan)
        self.assertIn('第一次回应', body)
        self.assertNotIn('停滞标记', body)
        self.assertEqual(llm.tasks[llm.tasks.index('write_story_scene'):][:5],
            ['write_story_scene','scene_progression_review','repair_story_scene_progression',
             'scene_progression_review','write_story_scene'])

    def test_still_stalled_after_two_repairs_blocks_continuation_and_restart(self):
        llm = SceneLLM(stalled=True,never_fixed=True)
        flow, plan = self.flow(llm, review_mode='deep_review')
        with self.assertRaisesRegex(StoryError,'两次修订'):
            flow._draft_text(plan)
        self.assertEqual(len(llm.scene_inputs),1)
        self.assertEqual(llm.tasks.count('repair_story_scene_progression'),2)
        before = len(llm.tasks)
        with self.assertRaisesRegex(StoryError,'两次修订'):
            flow._draft_text(plan)
        self.assertEqual(len(llm.tasks),before)
        checkpoint = next((self.project.root/'scene_checkpoints').iterdir())
        self.assertEqual(read_json(checkpoint/'state.json')['progression_repair_count'],2)
        self.assertEqual(len(list((checkpoint/'progression_reviews').glob('*.json'))),3)
        self.assertEqual(len(list((checkpoint/'progression_repairs').glob('*/before.md'))),2)

    def test_unknown_review_outcome_is_not_retried_or_followed_by_more_prose(self):
        llm = SceneLLM(unknown=True)
        flow, plan = self.flow(llm, review_mode='deep_review')
        with self.assertRaisesRegex(RuntimeError,'unknown'):
            flow._draft_text(plan)
        self.assertEqual(llm.tasks.count('scene_progression_review'),1)
        self.assertEqual(len(llm.scene_inputs),1)
        self.assertNotIn('repair_story_scene_progression',llm.tasks)
        checkpoint = next((self.project.root/'scene_checkpoints').iterdir())
        self.assertEqual(read_json(checkpoint/'state.json')['phase'],'ready')

    def test_handoff_evidence_must_be_in_actual_current_scene(self):
        body='她把围巾留下。\n他拒绝替她做决定，等她回答。'
        grouped=windows(body)
        response={'progression_observations':[{
            'window_id':grouped[0]['window_id'],'verdict':'flowing','reader_question':'她是否留下',
            'change':'主动留下围巾','reward':'得到回应','next_expectation':'等她回答',
            'reason':'行动回应期待','fix_intent':'保留','evidence_paragraph_indices':[0]}],
            'actual_handoff':{'facts':[{'text':'围巾被留下','paragraph_index':0}],
                              'answered_questions':[],'pending_questions':[]},
            'promise_alignment':{'verdict':'aligned','paragraph_index':0,
                                 'reason':'围巾的选择推进关系','fix_intent':'保留'}}
        result=validate_scene_review(response,grouped,body)
        self.assertEqual(result['actual_handoff']['facts'][0]['quote'],'她把围巾留下。')
        for invalid in (2,True,-1):
            response['actual_handoff']['facts'][0]['paragraph_index']=invalid
            with self.subTest(invalid=invalid),self.assertRaises(StoryError):
                validate_scene_review(response,grouped,body)

    def test_cached_response_tampering_cannot_inherit_a_pass(self):
        llm = SceneLLM()
        flow, plan = self.flow(llm, review_mode='deep_review')
        flow._draft_text(plan)
        path = next((self.project.root/'scene_checkpoints').glob('*/progression_reviews/*.json'))
        record = read_json(path)
        record['response']['actual_handoff']['facts'][0]['text']='篡改事实'
        path.write_text(json.dumps(record,ensure_ascii=False))
        with self.assertRaisesRegex(StoryError,'缓存损坏'):
            flow._draft_text(plan)


if __name__=='__main__': unittest.main()
