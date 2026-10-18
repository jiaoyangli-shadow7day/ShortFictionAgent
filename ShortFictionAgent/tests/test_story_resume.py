"""Regression tests for failures observed during the 2026-10-03 story run."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from test_story_studio import ScriptedLLM
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, visible_count
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow, scene_budget


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = StoryProject(Path(self.temp.name) / 'story')
        self.project.create(StoryBrief.from_dict({'genre':'现实情感','format':'short_story',
            'review_mode': 'deep_review',  # Explicit legacy preflight and scene checkpoints.
            'length':{'target':7600,'min':6000,'max':9500},
            'author_input':{'premise':'父亲在婚礼后厨练习祝词','locked_facts':[]}}))
        self.llm = ScriptedLLM('正文')
        self.flow = StoryWorkflow(self.project,self.llm)
        self.flow.develop()

    def test_long_story_preflight_and_plan_resume(self):
        plan = self.flow.plan()
        count = len(self.llm.tasks)
        self.assertIn('blueprint_audit',self.llm.tasks)
        self.assertEqual(self.flow.plan(),plan)
        self.assertEqual(len(self.llm.tasks),count)
        # A changed saved plan cannot inherit an old successful preflight.
        plan['ending_resolution']='另一个结局'
        (self.project.root/'plan/scenes.json').write_text(json.dumps(plan))
        self.flow.plan()
        self.assertEqual(self.llm.tasks.count('blueprint_audit'),2)

    def test_long_story_rejected_before_prose(self):
        class Reject(ScriptedLLM):
            def generate(self,prompt,task_type):
                if task_type=='blueprint_audit':
                    return json.dumps({'decision':'revise','issues':[
                        {'path':'scenes[0]','reason':'违反作者禁止项','missing_link':'修改动机'},
                        {'path':'scenes[1]','reason':'人物无从知道秘密','missing_link':'可见线索'}]})
                return super().generate(prompt,task_type)
        llm=Reject('正文');flow=StoryWorkflow(self.project,llm)
        with self.assertRaisesRegex(StoryError,'写前因果预审'):
            flow.plan()
        self.assertNotIn('write_story_scene',llm.tasks)

    def test_repeated_preflight_keeps_each_plan_judgment(self):
        plan = self.flow.plan()
        revised = copy.deepcopy(plan)
        revised['ending_resolution'] = '修订后的结局'
        self.flow._preflight_plan(revised, self.project.contract())
        history = [json.loads(p.read_text()) for p in
                   (self.project.root / 'plan/audit_history').glob('*.json')]
        self.assertEqual(len(history), 2)
        self.assertEqual({h['plan']['ending_resolution'] for h in history},
                         {plan['ending_resolution'], revised['ending_resolution']})
        self.assertTrue(all(h['audit']['decision'] == 'pass' for h in history))

    def test_resume_preserves_completed_scenes_and_full_context(self):
        class Interrupted(ScriptedLLM):
            def __init__(self):
                super().__init__('正文');self.indices=[];self.failed=False;self.contexts=[]
            def generate(self,prompt,task_type):
                if task_type=='write_story_scene':
                    data=json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0];index=data['scene_index']
                    self.indices.append(index);self.contexts.append(data)
                    if index==1 and not self.failed:
                        self.failed=True;raise RuntimeError('simulated transport interruption')
                    return f'FIRST-FACT-{index}'+('人'*1800)
                return super().generate(prompt,task_type)
        llm=Interrupted();flow=StoryWorkflow(self.project,llm);plan=flow.plan()
        with self.assertRaisesRegex(RuntimeError,'transport'):
            flow._draft_text(plan)
        body=flow._draft_text(plan)
        self.assertEqual(llm.indices,[0,1,1,2,3])
        self.assertTrue(6000 <= visible_count(body) <= 9500)
        self.assertIn('FIRST-FACT-0',llm.contexts[-1]['previous_text'])
        self.assertNotIn('FIRST-FACT-0',llm.contexts[-1]['previous_tail'])
        # Altered canonical facts invalidate all scenes that used those facts.
        updated=copy.deepcopy(plan);updated['continuity_map']['motives'].append('父亲主动要上台')
        flow._draft_text(updated)
        self.assertEqual(llm.indices[-4:],[0,1,2,3])

    def test_checkpoint_tampering_is_not_silently_reused(self):
        plan=self.flow.plan();self.flow._draft_text(plan)
        path=next((self.project.root/'scene_checkpoints').glob('*/ready.md'))
        path.write_text(path.read_text()+'篡改')
        with self.assertRaisesRegex(StoryError,'续写记录损坏'):
            self.flow._draft_text(plan)

    def test_revised_plan_does_not_receive_superseded_candidate_plot(self):
        class Captured(ScriptedLLM):
            def __init__(self):
                super().__init__('正文')
                self.writer_inputs = []
                self.writer_prompts = []

            def generate(self, prompt, task_type):
                if task_type == 'story_candidates':
                    data = json.loads(super().generate(prompt, task_type))
                    for item in data['candidates']:
                        item.update(goal='OBSOLETE_GOAL', core_scene='OBSOLETE_RESCUE',
                                    ending='OBSOLETE_VIRAL_PAYOFF')
                    return json.dumps(data, ensure_ascii=False)
                if task_type == 'write_story_scene':
                    data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                    self.writer_inputs.append(data)
                    self.writer_prompts.append(prompt)
                return super().generate(prompt, task_type)

        project = StoryProject(Path(self.temp.name) / 'revised-plan')
        project.create(StoryBrief.from_dict({'genre': '现实情感', 'format': 'short_story',
            'review_mode': 'deep_review',
            'length': {'target': 7600, 'min': 6000, 'max': 9500},
            'author_input': {'premise': '母女创业', 'locked_facts': []}}))
        llm = Captured()
        flow = StoryWorkflow(project, llm)
        selected = flow.develop()['candidate']
        plan = flow.plan()
        plan['scenes'][0]['choice'] = 'REVISED_CHOICE_WITH_REAL_COST'
        plan['ending_resolution'] = 'REVISED_EARNED_PAYOFF'
        flow._draft_text(plan)
        first = llm.writer_inputs[0]
        self.assertEqual(first['candidate'], {'id': selected['id'],
                                             'protagonist': selected['protagonist']})
        self.assertEqual(first['scene']['choice'], 'REVISED_CHOICE_WITH_REAL_COST')
        self.assertEqual(first['ending_resolution'], 'REVISED_EARNED_PAYOFF')
        self.assertIn('previous_text', first['planning_authority'])
        for prompt in llm.writer_prompts:
            for obsolete in ('OBSOLETE_GOAL', 'OBSOLETE_RESCUE', 'OBSOLETE_VIRAL_PAYOFF'):
                self.assertNotIn(obsolete, prompt)
        self.assertIn('第1场', llm.writer_inputs[-1]['previous_text'])
        # The original selection remains intact for provenance and audit.
        self.assertEqual(flow._selection()[0], selected)

    def test_edit_response_resumes_when_only_review_tracking_id_changes(self):
        class PendingResponse:
            def __init__(self):
                self.pending = None
                self.responses = {}

            def generate(self, prompt, task_type):
                if prompt not in self.responses:
                    self.pending = prompt
                    raise RuntimeError('response pending')
                return self.responses[prompt]

        llm = PendingResponse()
        flow = StoryWorkflow(self.project, llm)
        issue = {'id': 'first-review', 'quote': '星期一', 'category': 'coherence',
                 'severity': 'warning', 'reason': '时间错误', 'fix_intent': '改为星期二'}
        with self.assertRaisesRegex(RuntimeError, 'response pending'):
            flow._edit('星期一见面', issues=[issue])
        llm.responses[llm.pending] = json.dumps({'back_to_plan': False, 'edits': [{
            'quote': '星期一', 'replacement': '星期二', 'kind': 'rewrite',
            'benefit': '修正日期', 'risk': '无'}]})
        resumed_issue = {**issue, 'id': 'second-review'}
        self.assertEqual(flow._edit('星期一见面', issues=[resumed_issue]).strip(), '星期二见面')
        self.assertEqual(issue['id'], 'first-review')
        with self.assertRaisesRegex(RuntimeError, 'response pending'):
            flow._edit('星期一见面', issues=[{**resumed_issue, 'reason': '另一处时间错误'}])

    def test_failed_length_repairs_resume_raw_scene_without_redrafting(self):
        class RepairFailure(ScriptedLLM):
            def __init__(self):
                super().__init__('正文');self.writes=[];self.repairs=0
            def generate(self,prompt,task_type):
                if task_type=='write_story_scene':
                    data=json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0];index=data['scene_index']
                    self.writes.append(index)
                    return '长'*4000 if index==0 else '正文'*950
                if task_type=='repair_story_scene_length':
                    self.repairs+=1
                    return '长'*4000 if self.repairs<=2 else '修'*1900
                return super().generate(prompt,task_type)
        llm=RepairFailure();flow=StoryWorkflow(self.project,llm);plan=flow.plan()
        with self.assertRaisesRegex(StoryError,'修复后仍越界'):
            flow._draft_text(plan)
        body=flow._draft_text(plan)
        self.assertEqual(llm.writes,[0,1,2,3])
        self.assertEqual(llm.repairs,3)
        self.assertEqual(visible_count(body),7600)

    def test_uneven_scene_budgets_keep_total_bounds(self):
        length={'target':7800,'min':6500,'max':10000}
        first=scene_budget(length,4,0,0)
        self.assertLess(first['min'],1625)
        self.assertGreater(first['max'],2500)
        last=scene_budget(length,4,3,4900)
        self.assertEqual(last['min'],1600)
        self.assertEqual(last['max'],5100)
        with self.assertRaisesRegex(StoryError,'剩余篇幅'):
            scene_budget(length,4,3,10000)


    def test_multiple_localized_continuity_issues_receive_one_repair(self):
        class LocalRepair(ScriptedLLM):
            def __init__(self):
                super().__init__('正文'); self.audits = 0; self.repairs = 0
            def generate(self, prompt, task_type):
                if task_type == 'blueprint_audit':
                    self.audits += 1
                    if self.audits == 1:
                        return json.dumps({'decision':'revise','issues':[
                            {'path':'continuity_map.timeline','reason':'时间顺序反了','missing_link':'按场景顺序'},
                            {'path':'continuity_map.knowledge','reason':'知情时间反了','missing_link':'对齐场景'}]})
                    return '{"decision":"pass","issues":[]}'
                if task_type == 'story_plan_repair':
                    self.repairs += 1
                    return super().generate(prompt, 'story_plan')
                return super().generate(prompt, task_type)
        llm = LocalRepair()
        flow = StoryWorkflow(self.project, llm)
        with self.assertRaisesRegex(StoryError, '仍有核心硬伤'):
            flow.plan()
        self.assertEqual(llm.repairs, 1)
        self.assertEqual(llm.audits, 1)
        self.assertNotIn('write_story_scene', llm.tasks)
