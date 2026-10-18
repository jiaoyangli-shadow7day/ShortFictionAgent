import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import research_guidance as research
from short_fiction_agent.story_studio import screenshot_research
from short_fiction_agent.story_studio.contracts import StoryBrief
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow


class ScreenshotResearchTests(unittest.TestCase):
    def contract(self, promise, tags, **overrides):
        return {'format': 'short_story', 'genre': {'primary': 'custom', 'custom_description': promise},
                'reader_promise': promise,
                'research_preferences': {'reader_tags': tags}, **overrides}

    def test_all_archived_short_candidates_are_traceable_fragments(self):
        data = screenshot_research.library()
        self.assertEqual(len(data['evidence']), 44)
        registered = set(data['evidence'])
        self.assertEqual(set(ref for m in data['mechanisms'] for ref in m['evidence_ids']), registered)
        for row in data['evidence'].values():
            self.assertTrue(row['source_refs'])
            self.assertFalse(row['full_text_read'])
            self.assertFalse(row['ending_observed'])
            self.assertNotIn('write', row['supports_stages'])
        self.assertTrue(data['evidence']['B01-36']['title_complete'])
        self.assertEqual(len(data['evidence']['B01-36']['source_refs']), 3)

    def test_family_and_campus_growth_do_not_fall_back_to_romance(self):
        for promise,tags,expected in [('豪门家庭真假少主', ['家庭','男生生活'], 'B02-01'),
                                      ('校园成长现实生活', ['校园','成长'], 'B01-29')]:
            ctx = research.research_context(self.contract(promise,tags), task='story_candidates')
            self.assertEqual(ctx['selected_families'][0], 'family_growth')
            self.assertNotIn('romance',ctx['selected_families'])
            cards=ctx['screenshot_opening_research']['evidence']
            self.assertIn(expected,{r['sample_id'] for r in cards})
            self.assertNotIn('家庭',ctx['unmapped_reader_tags'])
            if '校园' in tags:
                self.assertEqual({r['sample_id'] for r in cards},{'B01-29'})
        love=research.research_context(self.contract('校园恋爱甜宠',['校园','甜宠']),task='opening_candidates')
        self.assertIn('romance',love['selected_families'])
        self.assertNotIn('B02-04',{r['sample_id'] for r in love['screenshot_opening_research']['evidence']})

    def test_fantasy_retrieval_is_bounded_and_every_rule_has_sent_evidence(self):
        ctx=research.research_context(self.contract('修仙脑洞',['玄幻仙侠','男生生活']),task='opening_candidates')
        cards=ctx['screenshot_opening_research']
        refs={r['sample_id'] for r in cards['evidence']}
        self.assertIn('B01-25',refs)
        self.assertNotIn('B02-04',refs)
        self.assertLessEqual(len(refs),4)
        for m in cards['mechanisms']:
            self.assertTrue(m['evidence_ids'])
            self.assertLessEqual(set(m['evidence_ids']),refs)
        model=research.model_research_context(ctx)['screenshot_opening_research']
        self.assertNotIn('likes_snapshot',json.dumps(model))
        sweet=research.research_context(self.contract('修仙与感情结合', ['玄幻仙侠','古言甜宠'],
            research_preferences={'reader_tags':['玄幻仙侠','古言甜宠'],'audience_track':'male'}),
            task='story_candidates')['screenshot_opening_research']
        self.assertNotIn('relationship',sweet['matched_domains'])
        self.assertTrue(sweet['evidence'])
        data=screenshot_research.library()
        self.assertTrue(all('fantasy' in data['evidence'][row['sample_id']]['domains']
                            for row in sweet['evidence']))

    def test_avoid_list_cannot_retrieve_a_premise_and_fragments_do_not_become_full_story_evidence(self):
        contract=self.contract('',['未知标签'],constraints={'avoid':['豪门','修仙','校园','系统']})
        cards=research.research_context(contract,task='story_candidates')['screenshot_opening_research']
        self.assertEqual(cards['evidence'],[])
        for task in ('write_story_scene','continuity_review'):
            ctx=research.research_context(self.contract('豪门家庭',['家庭']),task=task)
            self.assertEqual(ctx['screenshot_opening_research']['evidence'],[])
        serial=self.contract('豪门家庭',['家庭'],format='short_serial')
        self.assertEqual(research.research_context(serial,task='opening_candidates')['screenshot_opening_research']['evidence'],[])

    def test_new_case_semantics_invalidate_research_cache(self):
        old=research.guidance_fingerprint()
        data=copy.deepcopy(screenshot_research.library())
        data['evidence']['B02-01']['opening_observation']='新的经核验开头观察'
        with patch.object(screenshot_research,'library',return_value=data):
            self.assertNotEqual(research.guidance_fingerprint(),old)

    def test_actual_model_boundary_and_usage_receipts_receive_screenshot_cards(self):
        class Capture:
            max_prompt_tokens=50000
            def __init__(self):self.prompts={}
            def generate(self,prompt,task):
                self.prompts[task]=prompt
                return '测试传输响应'
        with tempfile.TemporaryDirectory() as directory:
            project=StoryProject(Path(directory)/'story')
            project.create(StoryBrief.from_dict(self.contract('校园成长现实生活',['校园','成长']) | {
                'length':{'target':6000,'min':4500,'max':7500},'story_engine':'emotional_reframing'}))
            llm=Capture();flow=StoryWorkflow(project,llm)
            tasks=('story_candidates','candidate_comparative_review','opening_candidates',
                   'opening_comparative_review','story_plan','channel_metadata')
            for task in tasks:
                flow.call_text(task,'本次传输检查')
                self.assertIn('user-screenshot:B01-29',llm.prompts[task])
            events=[json.loads(line) for line in (project.root/'runs/events.jsonl').read_text().splitlines()]
            calls=[e for e in events if e['event']=='model_call']
            self.assertEqual(len(calls),len(tasks))
            for e in calls:
                self.assertTrue(e['research_usage']['injected'])
                self.assertEqual(e['research_usage']['screenshot_source_ids'],['user-screenshot:B01-29'])


if __name__=='__main__':
    unittest.main()
