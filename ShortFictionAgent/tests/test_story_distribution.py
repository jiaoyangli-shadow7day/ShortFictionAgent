import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import yaml

from short_fiction_agent.story_studio.campaigns import match_campaigns
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.distribution import (
    PublicationPreparationAgent, PREPARATION_CHECKS, audit_workflow, validate_preparation, evidence_bank)
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.storage import StoryProject, write_json, write_text, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class DistributionTests(unittest.TestCase):
    def test_evidence_table_covers_body_once_including_tiny_and_whitespace_tails(self):
        for body in ('甲' * 80 + '乙', '甲' * 160 + '尾声\n', '甲' * 80 + ' ' * 10, '短文', ''):
            with self.subTest(length=len(body)):
                rows = list(evidence_bank(body).values())
                self.assertEqual(''.join(row['quote'] for row in rows), body)
                cursor = 0
                for row in rows:
                    self.assertEqual(row['span'][0], cursor)
                    self.assertEqual(body[slice(*row['span'])], row['quote'])
                    cursor = row['span'][1]
                self.assertEqual(cursor, len(body))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name)/'story')
        self.project.create(StoryBrief.from_dict({'title': '找信', 'format': 'micro_story',
            'channels': ['fanqie_short'],
            'length': {'target':900, 'min':600, 'max':1100},
            'author_input': {'premise':'林禾找信', 'locked_facts':[]}}))
        self.body = '林禾发现信不见了。' + '她追查记忆交易的线索，决定用自己的记忆换回那封信。'*31 + '她找回信，也记得代价。'
        self.llm = ScriptedLLM(self.body)
        self.flow = StoryWorkflow(self.project, self.llm)
        self.inputs = dict(channel='fanqie_short',titles=['找信'],synopsis='找回一封信',category='custom')
    def generate(self):
        self.flow.develop();self.flow.generate()
    def test_fanqie_export_blocks_corner_quotes_without_rewriting_reviewed_body(self):
        self.body += '她说：「信找到了。」'
        self.llm = ScriptedLLM(self.body)
        self.flow = StoryWorkflow(self.project, self.llm)
        self.generate()
        before = self.project.active_draft()[1]
        with self.assertRaisesRegex(StoryError, '中文双引号'):
            StoryExporter(self.project, self.flow.call_json).export('fanqie_short')
        self.assertEqual(self.project.active_draft()[1], before)
        self.assertFalse(list((self.project.root/'exports').glob('*/manifest.json')))
    def response(self):
        return {'checks':[dict(id=k,verdict='pass',quote=self.body[:12],reason='主线有找信行动',revision='无需修改')
                          for k in PREPARATION_CHECKS]}
    def test_auto_select_compares_and_uses_recommended_not_first(self):
        original=self.flow.call_json
        def call(task,prompt):
            result=original(task,prompt)
            if task=='candidate_comparative_review':
                result['comparisons'][0]['rank']=3;result['comparisons'][2]['rank']=1
                result['recommended_id']=2
            return result
        self.flow.call_json=call
        result=self.flow.develop()
        self.assertEqual(result['selection']['candidate_index'],2)
        self.assertEqual(result['selection']['selection_method'],'model_comparison')
        self.assertIn('comparison_hash',result['selection'])
    def test_explicit_selection_is_kept_and_cache_reuses_exact_input(self):
        self.flow.develop(candidate_index=2);self.flow.develop(candidate_index=3)
        self.assertEqual(self.llm.tasks.count('candidate_comparative_review'),1)
        self.assertEqual(json.loads((self.project.root/'selection.json').read_text())['candidate_index'],3)
    def test_comparison_duplicate_or_nonviable_recommendation_fails_closed(self):
        from short_fiction_agent.story_studio.selection_review import validate_comparison
        options=[dict(id=i,viable=True) for i in range(4)]
        r=json.loads(self.llm.generate('', 'candidate_comparative_review'))
        bad=copy.deepcopy(r);bad['comparisons'][1]['candidate_id']=0
        with self.assertRaises(StoryError):validate_comparison(bad,options)
        options[0]['viable']=False
        with self.assertRaises(StoryError):validate_comparison(r,options)
    def test_unchosen_candidate_change_also_invalidates_comparison(self):
        self.flow.develop();p=self.project.root/'candidates.json';data=json.loads(p.read_text())
        data['candidates'][3]['ending']='另一个结尾';write_json(p,data)
        with self.assertRaisesRegex(StoryError,'比较输入'):self.flow._selection()
    def test_contract_change_not_rehashed_into_old_selection(self):
        self.flow.develop();before=(self.project.root/'selection.json').read_bytes()
        brief=self.project.brief();brief.title='新承诺';write_text(self.project.brief_path,yaml.safe_dump(brief.to_dict(),allow_unicode=True))
        with self.assertRaisesRegex(StoryError,'简报已更改'):self.flow._selection()
        self.assertEqual(before,(self.project.root/'selection.json').read_bytes())
    def test_invalid_quote_missing_check_and_outside_opening_fail(self):
        for kind in ('quote','missing','opening'):
            r=self.response()
            if kind=='quote':r['checks'][0]['quote']='正文没有的引文'
            if kind=='missing':r['checks'].pop()
            if kind=='opening':r['checks'][1]['quote']='她找回信，也记得代价。'
            with self.subTest(kind=kind),self.assertRaises(StoryError):
                validate_preparation(r,self.body[:12]+('很长的中间段落'*300)+'她找回信，也记得代价。')
    def test_preparation_requires_current_review(self):
        self.project.save_draft(self.body)
        with self.assertRaises(StoryError):PublicationPreparationAgent(self.project,self.flow.call_json).review(**self.inputs)
    def test_evidence_id_extracts_exact_source_and_rejects_unknown_or_conflicting_quote(self):
        from short_fiction_agent.story_studio.distribution import evidence_bank
        response=self.response()
        for row in response['checks']:
            row.pop('quote');row['evidence_id']='e0'
        rows=validate_preparation(response,self.body)
        self.assertEqual(rows[0]['quote'],self.body[:80])
        self.assertEqual(rows[0]['span'],[0,80])
        for bad in ('e999999',[],{'a':1}):
            response['checks'][0]['evidence_id']=bad
            with self.assertRaises(StoryError):validate_preparation(response,self.body)
        response['checks'][0].update(evidence_id='e0',quote='完全不同的原文引用')
        with self.assertRaises(StoryError):validate_preparation(response,self.body)
    def test_evidence_bank_preserves_short_final_payoff(self):
        from short_fiction_agent.story_studio.distribution import evidence_bank
        body='前面的情节'*16+'她离开了。'
        spans=[entry['span'] for entry in evidence_bank(body).values()]
        covered=set(i for start,end in spans for i in range(start,end))
        self.assertEqual(covered,set(range(len(body))))
    def test_exact_cache_title_category_body_and_contract_invalidation(self):
        self.generate();agent=PublicationPreparationAgent(self.project,self.flow.call_json)
        r=agent.review(**self.inputs);agent.review(**self.inputs)
        self.assertEqual(self.llm.tasks.count('publication_preparation'),1)
        for changed in ({'titles':['新标题']},{'category':'现言甜宠'}):
            data={**self.inputs,**changed}
            with self.assertRaises(StoryError):PublicationPreparationAgent(self.project).review(**data)
        record,body=self.project.active_draft();self.project.save_draft(body+'后记',artifact_id=record['artifact_id'],parent_revision=record['revision'])
        with self.assertRaises(StoryError):agent.review(**self.inputs)
    def test_forged_overall_pass_cannot_override_revise(self):
        self.generate();r=self.response();r['decision']='editorial_ready';r['checks'][0]['verdict']='revise'
        actual=PublicationPreparationAgent(self.project,lambda *_:r).review(**self.inputs)
        self.assertEqual(actual['decision'],'needs_revision')
        self.assertEqual(actual['platform_review_result'],'unknown')
    def test_unknown_transport_result_is_never_retried(self):
        self.generate(); calls=[]
        def unknown(*args):
            calls.append(args)
            raise RuntimeError('transport result unknown')
        with self.assertRaises(RuntimeError):
            PublicationPreparationAgent(self.project,unknown).review(**self.inputs)
        self.assertEqual(len(calls),1)
    def test_invalid_completed_evidence_has_one_bounded_retry(self):
        self.generate(); calls=[]
        def call(*args):
            calls.append(args)
            result=self.response()
            if len(calls)==1:result['checks'][0]['quote']='并不存在的连续原文'
            return result
        self.assertEqual(PublicationPreparationAgent(self.project,call).review(**self.inputs)['decision'],
                         'editorial_ready')
        self.assertEqual(len(calls),2)
        self.assertEqual(len(list((self.project.root/'publication_preparation_errors').glob('*.json'))),1)
    def test_valid_but_altered_comparison_cache_cannot_silently_reselect(self):
        self.flow.develop();path=self.project.root/'candidate_comparison.json'
        data=json.loads(path.read_text());data['comparisons'][0]['weakness']='外部修改的短板'
        write_json(path,data)
        with self.assertRaisesRegex(StoryError,'缓存候选比较'):self.flow.develop()
    def test_chief_formal_export_reuses_review_without_another_model_call(self):
        self.generate()
        calls = len(self.llm.tasks)
        direct = StoryExporter(self.project).export('fanqie_short')
        prep = read_json(direct / 'publication_preparation.json')
        self.assertEqual(prep['mode'], 'chief_editor')
        self.assertEqual(prep['basis'], 'current_full_chief_review_and_verbatim_excerpt')
        self.assertEqual(len(self.llm.tasks), calls)
        self.assertEqual(audit_workflow(self.project)['status'], 'complete')
        self.assertTrue(StoryExporter(self.project).export('fanqie_short',draft=True).is_dir())
        package=StoryExporter(self.project,self.flow.call_json).export('fanqie_short')
        manifest=json.loads((package/'manifest.json').read_text())
        self.assertEqual(manifest['files_sha256']['publication_preparation.json'],
                         digest((package/'publication_preparation.json').read_text()))
    def test_audit_read_only_and_no_historical_receipt_manufactured(self):
        self.generate();PublicationPreparationAgent(self.project,self.flow.call_json).review(**self.inputs)
        before={str(p):p.read_bytes() for p in self.project.root.rglob('*') if p.is_file()}
        self.assertEqual(audit_workflow(self.project)['status'],'complete')
        after={str(p):p.read_bytes() for p in self.project.root.rglob('*') if p.is_file()}
        self.assertEqual(before,after)
        (self.project.root/'generation_receipt.json').unlink()
        self.assertEqual(audit_workflow(self.project)['status'],'incomplete')
        self.assertFalse((self.project.root/'generation_receipt.json').exists())

    def test_formal_export_preserves_and_checks_supplied_submission_metadata(self):
        raw = self.project.brief().to_dict()
        raw['review_mode'] = 'deep_review'
        write_text(self.project.brief_path, yaml.safe_dump(raw, allow_unicode=True))
        self.generate()
        PublicationPreparationAgent(self.project,self.flow.call_json).review(**self.inputs)
        before = self.llm.tasks.count('publication_preparation')
        package=StoryExporter(self.project).export('fanqie_short',titles=['找信'],
                                                  synopsis='找回一封信',category='custom')
        self.assertEqual(read_json(package/'submission_info.json')['synopsis'],'找回一封信')
        self.assertEqual(self.llm.tasks.count('publication_preparation'),before)
        with self.assertRaisesRegex(StoryError,'发布准备'):
            StoryExporter(self.project).export('fanqie_short',titles=['另一标题'],
                                               synopsis='找回一封信',category='custom')
        with self.assertRaisesRegex(StoryError,'同时提供'):
            StoryExporter(self.project).export('fanqie_short',titles=['找信'])


    def test_new_chief_metadata_requires_grounded_reading_and_tampering_fails(self):
        from short_fiction_agent.story_studio.distribution import chief_preparation, validate_chief_preparation
        self.generate()
        with self.assertRaisesRegex(StoryError, '新标题或简介'):
            StoryExporter(self.project).export('fanqie_short', titles=['另一标题'], synopsis='新简介')
        calls = []
        def same_editor(task, prompt):
            calls.append((task, prompt))
            from tests.narrative_fixtures import narrative_response
            return narrative_response(prompt)
        prepared = chief_preparation(self.project, same_editor, **self.inputs)
        self.assertEqual([task for task, _ in calls],
                         ['chief_editor_publication', 'chief_editor_publication_expectation_ledger'])
        self.assertIn('找回一封信', calls[0][1])
        self.assertTrue(validate_chief_preparation(self.project, prepared, self.project.active_draft()[1]))
        package = StoryExporter(self.project).export('fanqie_short', **{k:v for k,v in self.inputs.items() if k!='channel'})
        self.assertEqual(read_json(package/'submission_info.json')['synopsis'], '找回一封信')
        corrupted = copy.deepcopy(prepared)
        corrupted['inputs']['titles'] = ['偷换标题']
        with self.assertRaises(StoryError):
            validate_chief_preparation(self.project, corrupted, self.project.active_draft()[1])

    def test_chief_preparation_does_not_reuse_changed_category_or_channel(self):
        from short_fiction_agent.story_studio.distribution import chief_preparation
        self.generate()
        body = self.project.active_draft()[1]
        baseline = dict(channel='fanqie_short', titles=['找信'],
                        synopsis=body[:180].strip().replace('\n', ''), category='')
        self.assertEqual(chief_preparation(self.project, None, **baseline)['decision'], 'editorial_ready')
        for change in ({'category': '悬疑'}, {'channel': 'douyin_carousel'}):
            with self.subTest(change=change), self.assertRaisesRegex(StoryError, '尚未核对'):
                chief_preparation(self.project, None, **{**baseline, **change})

    def test_long_metadata_reads_all_prose_with_bounded_prompts_and_resumes(self):
        from short_fiction_agent.story_studio import chief_editor
        from short_fiction_agent.story_studio.quality import StoryReviewer
        from short_fiction_agent.story_studio.distribution import chief_preparation, validate_chief_preparation
        project = StoryProject(Path(self.tmp.name) / 'long-publication')
        project.create(StoryBrief.from_dict({'title': '旧标题', 'format': 'micro_story',
            'length': {'target': 35000, 'min': 10}, 'channels': ['fanqie_short'],
            'author_input': {'premise': '林禾找回信。'}}))
        body = '\n\n'.join(f'第{i}次她找到新的线索，决定继续追查。' * 12 for i in range(150))
        project.save_draft(body)
        def ready(task, prompt):
            from tests.narrative_fixtures import narrative_response
            return narrative_response(prompt)
        # Include scoped guide, all research families and mandatory reader-
        # expectation source anchors. This still forces multiple read turns.
        capacity = 30000
        StoryReviewer(project, ready, max_prompt_chars=capacity).review()
        inputs = dict(channel='fanqie_short', titles=['找信'], synopsis='寻找新线索', category='')
        calls, read_ids = [], set()
        source_by_id = {p['id']: p['text'] for p in chief_editor.paragraphs(project.active_draft()[1])}
        def record_readings(payload):
            for row in payload['paragraphs']:
                if 'synthesis_protocol' in payload:
                    self.assertEqual(payload['synthesis_protocol'], chief_editor.SYNTHESIS_PROTOCOL)
                    self.assertIsInstance(row, list)
                    self.assertEqual(len(row), 2)
                    paragraph_id, text = row
                else:
                    paragraph_id, text = row['id'], row['text']
                self.assertEqual(text, source_by_id[paragraph_id])
                read_ids.add(paragraph_id)
        def interrupted(task, prompt):
            calls.append((task, prompt))
            self.assertLessEqual(len(prompt), capacity)
            if len(calls) == 2:
                raise RuntimeError('unknown transport result')
            payload = json.loads(prompt.split('\n')[-1])
            record_readings(payload)
            return ready(task, prompt)
        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            chief_preparation(project, interrupted, **inputs)
        first_prompt = calls[0][1]
        def continued(task, prompt):
            self.assertNotEqual(prompt, first_prompt)
            self.assertLessEqual(len(prompt), capacity)
            calls.append((task, prompt))
            payload = json.loads(prompt.split('\n')[-1])
            record_readings(payload)
            actual_contract = payload['brief'] if task.endswith('_expectation_ledger') else payload['author_contract']
            self.assertEqual(actual_contract['publication_metadata']['titles'], ['找信'])
            return ready(task, prompt)
        result = chief_preparation(project, continued, **inputs)
        self.assertTrue(validate_chief_preparation(project, result, project.active_draft()[1]))
        self.assertEqual(read_ids, {p['id'] for p in chief_editor.paragraphs(project.active_draft()[1])})
        self.assertGreater(len(result['metadata_reading']['chunks']), 1)
        self.assertEqual(result['platform_review_result'], 'unknown')

class CampaignTests(unittest.TestCase):
    def setUp(self):
        self.rule=dict(id='test',source_url='https://fanqienovel.com/writer/zone/article/1',
                       checked_at='2026-10-01',start='2026-10-01',end='2026-11-30',
                       platform='fanqie',entry_type='short_story',categories=['现言甜宠'],
                       min_words=8000,max_words=24900,benefit='条件奖励',external_conditions=['编辑入选'])
        self.kw=dict(platform='fanqie',entry_type='short_story',category='现言甜宠',word_count=9146,as_of='2026-10-01')
    def test_fit_is_potential_only_and_two_stackable_campaigns_are_not_conflict(self):
        other={**self.rule,'id':'other'}
        results=match_campaigns([self.rule,other],**self.kw)
        self.assertEqual([r['status'] for r in results],['potential_match']*2)
        self.assertIn('签约日期待确认',results[0]['pending'])
    def test_long_form_expired_stale_or_wrong_category_never_match(self):
        self.assertEqual(match_campaigns([{**self.rule,'entry_type':'long_serial'}],**self.kw),[])
        for changes in ({'end':'2026-10-01'},{'checked_at':'2026-09-01'},{'categories':['年代']}):
            kwargs={**self.kw,'as_of':'2026-10-02'}
            self.assertEqual(match_campaigns([{**self.rule,**changes}],**kwargs)[0]['status'],'not_matched')
    def test_exact_ambiguous_boundary_is_exposed(self):
        rule={**self.rule,'min_words':20000,'max_words':None,'ambiguous_word_boundaries':[20000]}
        self.assertEqual(match_campaigns([rule],**{**self.kw,'word_count':20000})[0]['status'],'requires_confirmation')
    def test_expired_ambiguity_still_rejects(self):
        rule={**self.rule,'ambiguous_word_boundaries':[9146],'end':'2026-10-01'}
        self.assertEqual(match_campaigns([rule],**{**self.kw,'as_of':'2026-10-02'})[0]['status'],'not_matched')
    def test_signature_before_window_rejects(self):
        self.assertEqual(match_campaigns([self.rule],signed_at='2026-09-30',**self.kw)[0]['status'],'not_matched')

if __name__=='__main__':unittest.main()
