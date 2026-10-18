"""Evidence and workflow regressions; these do not simulate reader retention."""
import copy, json, unittest
import yaml
from unittest.mock import patch
from short_fiction_agent.story_studio import whole_story, reader_experience
from short_fiction_agent.story_studio.contracts import StoryError, digest
from short_fiction_agent.story_studio.quality import review_decision, review_rubric_hash, DIMENSIONS
from short_fiction_agent.story_studio.research_guidance import research_context, guidance_fingerprint, whole_story_library
from test_story_studio import structure_fixture, reader_experience_fixture, reader_basis_fixture, _prompt_object

class WholeStructureTests(unittest.TestCase):
    def setUp(self):
        self.body='主角答应独自承担。\n承担失败，失去同伴信任。\n她承认失误，重新约定交接。'
        self.contract={'format':'short_story','ending_mode':'closed',
            'length':{'unit':'visible_char_v1','scope':'whole_story','min':1,'target':30,'max':500}}

    def assessment(self, verdict='sufficient',scope='none'):
        result=whole_story.validate(structure_fixture(whole_story.paragraphs(self.body),verdict,scope),self.body)
        result['independent_challenge']=whole_story.validate_challenge({'claims':[]},self.body)
        return result

    def _use_deep_review(self):
        brief = self.project.brief()
        brief.review_mode = 'deep_review'
        (self.project.root/'brief.yaml').write_text(
            yaml.safe_dump(brief.to_dict(), allow_unicode=True, sort_keys=False))

    def _set_up_deep_short_story(self, *, max_calls=40):
        import tempfile
        from pathlib import Path
        from short_fiction_agent.story_studio.contracts import StoryBrief
        from short_fiction_agent.story_studio.storage import StoryProject
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        from test_story_studio import ScriptedLLM
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = StoryProject(Path(temp.name)/'story')
        self.project.create(StoryBrief.from_dict({'genre': '现实情感', 'format': 'short_story',
            'review_mode': 'deep_review', 'length': {'target': 7600, 'min': 6000, 'max': 9500},
            'author_input': {'premise': '父亲在婚礼后厨练习祝词', 'locked_facts': []}}))
        self.llm = ScriptedLLM('正文')
        self.flow = StoryWorkflow(self.project, self.llm, max_calls=max_calls)
        self.flow.develop()

    def test_editorial_disagreement_cannot_silently_become_approval(self):
        result=self.assessment()
        result['independent_challenge']=whole_story.validate_challenge({'claims':[{
            'criterion':'consequence_chain','reason':'后果未影响下一场','counterfactual':'移除前场后下一场仍照旧','paragraph_indices':[0,2]}]},self.body)
        self.assertIn('whole_story:consequence_chain:disputed',whole_story.gate(self.body,result))
        self.assertEqual(whole_story.issues(result)[0]['repair_scope'],'replan')

    def test_high_local_scores_cannot_override_thin_whole_story(self):
        reader = reader_experience.validate(
            reader_experience_fixture(reader_experience.windows(self.body)), self.body, self.contract)
        basis_input = _prompt_object(reader_experience.grounding_prompt(self.body, self.contract, reader),
            ('reader_windows', 'reader_basis'))
        grounding = reader_experience.validate_grounding(
            reader_basis_fixture(basis_input['reader_windows'], basis_input['reader_basis']),
            self.body, self.contract, reader)
        report={'content_hash':digest(self.body),'contract_hash':digest(self.contract),
            'review_rubric_hash':review_rubric_hash(), 'review_coverage':{'complete':True,'spans':[[0,len(self.body)]]},
            'dimensions':{key:4 for key in DIMENSIONS},'dimension_evidence':{key:'局部动作清晰' for key in DIMENSIONS},
            'issues':[],'whole_story':self.assessment('thin','replan'),
            'reader_experience': reader, 'reader_grounding': grounding}
        with patch('short_fiction_agent.story_studio.quality.engagement_gate',return_value=[]), \
             patch('short_fiction_agent.story_studio.quality.narrative_setup.gate',return_value=[]):
            decision=review_decision(self.body,self.contract,report)
        self.assertEqual(decision['decision'],'revise_required')
        self.assertIn('whole_story:consequence_chain:thin',decision['blockers'])

    def test_default_chief_global_structure_issue_keeps_actual_evidence_and_blocks(self):
        import test_story_studio
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        from test_story_studio import ScriptedLLM
        test_story_studio.StoryStudioTests.setUp(self)

        class StructuralEditor(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == 'chief_editor_review':
                    self.tasks.append(task_type)
                    data = _prompt_object(prompt, ('author_contract', 'paragraphs'))
                    return json.dumps({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
                        'severity': 'blocker', 'scope': 'global',
                        'evidence_ids': [data['paragraphs'][0]['id']],
                        'reason': '传输测试：核心行动的代价没有改变后续选择',
                        'fix_intent': '让前次选择的后果改变取信方式'}]}, ensure_ascii=False)
                return super().generate(prompt, task_type)

        llm = StructuralEditor(self.body)
        flow = StoryWorkflow(self.project, llm)
        self.project.save_draft(self.body)
        saved_body = self.project.active_draft()[1]
        report = flow.reviewer.review()
        self.assertEqual(self.project.contract()['review_mode'], 'chief_editor')
        self.assertEqual(report['decision'], 'revise_required')
        issue = report['chief_editor']['response']['issues'][0]
        self.assertEqual(issue['scope'], 'global')
        self.assertEqual(issue['evidence'][0]['text'], self.body)
        self.assertEqual(report['content_hash'], digest(saved_body))
        self.assertEqual(llm.tasks, ['chief_editor_first_read', 'chief_editor_review'])
        self.assertEqual(self.project.active_draft()[1], saved_body)
        self.assertEqual(list((self.project.root/'memory/commits').glob('*.json')), [])

    def test_changed_body_or_forged_quotes_invalidate_whole_review(self):
        result=self.assessment()
        with self.assertRaises(StoryError):whole_story.gate(self.body+'新增尾声。',result)
        forged=copy.deepcopy(result);forged['checks'][0]['evidence'][0]['quote']='来自另一篇的台词'
        with self.assertRaises(StoryError):whole_story.gate(self.body,forged)

    def test_replan_issue_cannot_be_sent_as_local_polish(self):
        import test_story_studio
        test_story_studio.StoryStudioTests.setUp(self)
        self._use_deep_review()
        before=len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError,'退回规划层'):
            self.flow._edit(self.body,issues=whole_story.issues(self.assessment('thin','replan')))
        self.assertEqual(len(self.llm.tasks),before)
        self.assertTrue((self.project.root/'structure_repair_request.json').exists())

    def test_cosmetic_replan_keeps_old_questions_and_stops_before_new_prose(self):
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        from short_fiction_agent.story_studio.storage import read_json
        from test_story_studio import ScriptedLLM
        self._set_up_deep_short_story()
        plan=self.flow.plan()
        class CosmeticReplanner(ScriptedLLM):
            def __init__(self):
                super().__init__('正文');self.comparisons=[]
            def generate(self,prompt,task_type):
                if task_type=='whole_story_replan':
                    self.tasks.append(task_type)
                    return json.dumps(plan,ensure_ascii=False)
                if task_type=='whole_story_replan_challenge':
                    self.tasks.append(task_type);self.comparisons.append(prompt)
                    return json.dumps({'claims':[{'criterion':'promise_development',
                        'reason':'解决方式仍然是旧问题指出的服从考核',
                        'counterfactual':'将成长一词换回服从，选择后果不变',
                        'paragraph_indices':[0,1]}]},ensure_ascii=False)
                return super().generate(prompt,task_type)
        llm=CosmeticReplanner();flow=StoryWorkflow(self.project,llm,max_calls=16)
        context=whole_story.repair_context(plan,[{'criterion':'promise_development',
            'reason':'旧稿只用服从考核替代学习自保','fix_intent':'改变解题方式'}],digest(self.body))
        with self.assertRaisesRegex(StoryError,'整篇结构预审仍未通过'):
            flow._preflight_story_structure(plan,self.project.contract(),revision_context=context)
        self.assertEqual(len(llm.comparisons),2)
        self.assertTrue(all('旧稿只用服从考核替代学习自保' in p for p in llm.comparisons))
        self.assertNotIn('write_short_story',llm.tasks)
        self.assertNotIn('write_story_scene',llm.tasks)
        self.assertEqual(read_json(self.project.root/'plan/structure_rejected.json')['scenes'],plan['scenes'])

    def test_generic_survival_does_not_retrieve_stockpiling(self):
        contract={'format':'short_story','story_engine':'survival_dilemma',
            'author_input':{'premise':'普通租客帮助消防员核验住户信息'},
            'research_preferences':{'reader_tags':['求生','都市脑洞']}}
        context=research_context(contract,task='whole_story_review')
        mechanisms=[m['id'] for card in context['topic_guidance'].values() for m in card['mechanisms']]
        self.assertNotIn('apocalypse_stockpile',mechanisms)
        self.assertEqual(context['topic_guidance']['survival']['evidence'],[])
        self.assertEqual(context['continuous_pull']['continuous_reading_evidence']['evidence'],[])
        self.assertNotIn('末日求生',[tag for row in context['whole_story_research']['evidence'] for tag in row['selection_tags']])

    def test_whole_library_covers_all_filter_tags_and_exposes_gaps(self):
        library=whole_story_library()
        self.assertEqual(len(library['taxonomy']),115)
        context=research_context({'format':'short_story','research_preferences':{'reader_tags':['星际','狼人']}},task='story_plan')
        self.assertEqual(context['whole_story_research']['evidence'],[])
        self.assertEqual(context['whole_story_research']['matched_tag_coverage']['狼人']['continuous_readings'],0)
        for tag in ('宫斗宅斗','玄幻仙侠','娱乐圈','年代'):
            context=research_context({'format':'short_story','research_preferences':{'reader_tags':[tag]}},task='story_plan')
            self.assertTrue(context['whole_story_research']['evidence'],tag)
            self.assertLessEqual(len(context['whole_story_research']['evidence']),2)
        broad=research_context({'format':'short_story','research_preferences':{'reader_tags':['男生生活']}},task='story_plan')
        self.assertEqual(broad['whole_story_research']['evidence'],[])
        self.assertGreater(broad['whole_story_research']['matched_tag_coverage']['男生生活']['continuous_readings'],0)

    def test_new_structural_evidence_invalidates_cached_research(self):
        baseline=guidance_fingerprint()
        library=whole_story_library();library['evidence'][0]['transfer']='新的实读机制'
        with patch('short_fiction_agent.story_studio.research_guidance.whole_story_library',return_value=library):
            self.assertNotEqual(guidance_fingerprint(),baseline)

    def test_structural_failure_replans_regenerates_and_reviews_new_body(self):
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        from short_fiction_agent.story_studio.storage import read_json
        from test_story_studio import ScriptedLLM
        # Persistent allowance is chosen before develop; a later workflow
        # cannot silently raise an already-initialized project limit of 40.
        self._set_up_deep_short_story(max_calls=80)
        class Replanner(ScriptedLLM):
            def __init__(self):
                super().__init__('正文');self.full_reviews=0
            def generate(self,prompt,task_type):
                if task_type=='whole_story_review':
                    self.tasks.append(task_type);self.full_reviews+=1
                    data=json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
                    return json.dumps(structure_fixture(data['paragraphs'],
                        'thin' if self.full_reviews==1 else 'sufficient',
                        'replan' if self.full_reviews==1 else 'none'),ensure_ascii=False)
                if task_type=='whole_story_replan':
                    self.tasks.append(task_type)
                    result=json.loads(super().generate(prompt,'story_plan'))
                    result['scenes'][0]['consequence']='前次选择导致同伴退出，下一场须自己承担资源损失'
                    return json.dumps(result,ensure_ascii=False)
                if task_type=='plan_reconciliation_review':
                    self.tasks.append(task_type)
                    return json.dumps({'decision':'pass','issues':[]})
                return super().generate(prompt,task_type)
        llm=Replanner();flow=StoryWorkflow(self.project,llm,max_calls=80)
        result=flow.generate()
        self.assertEqual(result['decision'],'passed')
        self.assertEqual(llm.tasks.count('whole_story_replan'),1)
        self.assertIn('whole_story_replan_challenge',llm.tasks)
        failed_review=llm.tasks.index('whole_story_review')
        replan=llm.tasks.index('whole_story_replan')
        self.assertNotIn('story_edit',llm.tasks[failed_review+1:replan])
        self.assertEqual(llm.tasks.count('blueprint_audit'),2)
        revisions=list((self.project.root/'plan/structure_revisions').glob('*.json'))
        self.assertEqual(len(revisions),1)
        record,body=self.project.active_draft()
        self.assertNotEqual(read_json(revisions[0])['previous_body_hash'],digest(body))
        self.assertEqual(read_json(self.project.root/'generation_receipt.json')['content_hash'],digest(body))
        budget = read_json(self.project.root/'run_control.json')
        self.assertEqual(budget['limit'], 80)
        self.assertEqual(budget['used'], flow.calls)
        self.assertGreater(budget['used'], 40)
        self.assertLessEqual(budget['used'], 80)

if __name__=='__main__':unittest.main()
