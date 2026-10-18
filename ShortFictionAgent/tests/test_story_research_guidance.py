import unittest
from unittest.mock import patch
from short_fiction_agent.story_studio.research_guidance import research_context, guidance_fingerprint, VERSION, research_library, desire_payoff_library
from short_fiction_agent.story_studio.selection_review import comparison_input_hash, opening_comparison_input_hash
from short_fiction_agent.story_studio.quality import review_rubric_hash
import test_story_studio

class ResearchIntegrationTests(unittest.TestCase):
    def setUp(self):
        test_story_studio.StoryStudioTests.setUp(self)

    def test_native_pipeline_delivers_guidance_to_models(self):
        prompts = {}
        generate = self.llm.generate
        def capture(prompt, task):
            prompts.setdefault(task, []).append(prompt)
            return generate(prompt, task)
        self.llm.generate = capture
        self.flow.develop()
        self.flow.generate()
        for task in ('story_candidates', 'candidate_comparative_review', 'opening_candidates',
                     'opening_comparative_review', 'story_blueprint', 'write_micro_story',
                     'chief_editor_review', 'story_edit'):
            if task == 'story_edit' and task not in prompts:
                self.flow._edit(self.project.active_draft()[1])
            self.assertIn(task, prompts)
            self.assertIn(VERSION, prompts[task][0])
            if task == 'chief_editor_review':
                self.assertIn('source_cards', prompts[task][0])
                self.assertIn('source_path', prompts[task][0])
            else:
                self.assertIn('不是平台规则' if task in ('story_candidates', 'story_edit') else 'internal_editorial_hypotheses', prompts[task][0])

    def test_profiles_follow_explicit_contract_and_custom_is_not_guessed(self):
        romance = research_context({'genre': {'primary': 'romance'}, 'story_engine': 'relationship_choice'})
        self.assertEqual(list(romance['profiles']), ['relationship'])
        self.assertNotIn('mystery', romance['profiles'])
        custom = research_context({'genre': {'primary': 'custom'}, 'story_engine': 'unknown'})
        self.assertEqual(custom['profile_selection'], 'choose_by_author_promise')
        self.assertTrue({'power','adventure','professional','care','historical','satire'} <= set(custom['profiles']))

    def test_resume_after_opening_transport_failure_reuses_completed_candidates(self):
        calls = []
        original = self.llm.generate
        def interrupted(prompt, task):
            calls.append(task)
            if task == 'opening_candidates':
                raise RuntimeError('transport interrupted')
            return original(prompt, task)
        self.llm.generate = interrupted
        with self.assertRaises(RuntimeError):
            self.flow.develop()
        def resumed(prompt, task):
            calls.append(task)
            return original(prompt, task)
        self.llm.generate = resumed
        self.flow.develop()
        self.assertEqual(calls.count('story_candidates'), 1)
        # Also ensure the resumed call did not silently generate another set.
        from short_fiction_agent.story_studio.storage import read_json
        saved = read_json(self.project.root / 'candidates.json')
        self.assertEqual(saved['research_guidance_hash'], guidance_fingerprint())

    def test_continuous_cases_are_bounded_and_follow_topic(self):
        for tags, group in ((['甜宠'],'sweet'), (['婆媳'],'family'),
                            (['规则怪谈'],'suspense_speculative')):
            context = research_context({'format':'short_story',
                'research_preferences':{'reader_tags':tags}},task='write_story_scene')
            evidence = context['continuous_pull']['continuous_reading_evidence']
            self.assertTrue(evidence['evidence'])
            self.assertLessEqual(len(evidence['evidence']),2)
            self.assertTrue(all(card['group']==group for card in evidence['evidence']))
            self.assertTrue(evidence['deferred_source_ids'])
            self.assertFalse(set(evidence['deferred_source_ids']) &
                             {card['book_id'] for card in evidence['evidence']})
        serial = research_context({'format':'short_serial',
            'research_preferences':{'reader_tags':['甜宠']}},task='continuity_review')
        self.assertEqual(serial['continuous_pull']['continuous_reading_evidence']['evidence'],[])

    def test_reader_tags_stage_and_audience_are_bounded_and_distinct(self):
        cases = [('male', ['甜宠'], 'male_relationship'),
                 ('female', ['婆媳'], 'family_growth'),
                 ('general', ['规则怪谈'], 'mystery_rules'),
                 ('general', ['科幻末世'], 'survival')]
        for track, tags, expected in cases:
            contract = {'research_preferences': {'reader_tags': tags, 'audience_track': track}}
            opening = research_context(contract, task='opening_comparative_review')
            package = research_context(contract, task='channel_metadata')
            self.assertEqual(opening['stage'], 'opening')
            self.assertEqual(package['stage'], 'package')
            self.assertIn(expected, opening['selected_families'])
            self.assertNotEqual(opening['topic_guidance'][expected]['rules'], package['topic_guidance'][expected]['rules'])
            self.assertLessEqual(len(opening['selected_families']), 3)
            for card in opening['topic_guidance'].values():
                self.assertLessEqual(len(card['evidence']), 4)

    def test_deep_cases_are_retrieved_for_relevant_positive_premises(self):
        contract = {'format': 'short_story',
                    'research_preferences': {'reader_tags': ['甜宠']},
                    'author_input': {'premise': '成年发小与驻唱歌手通过音乐创作走近'},
                    'constraints': {'avoid': ['民政局', '领证', '同居', '猫']}}
        evidence = research_context(contract)['continuous_pull']['continuous_reading_evidence']
        self.assertEqual(evidence['evidence'][0]['book_id'], '7606728686829571134')
        self.assertIn('音乐', evidence['matched_terms']['7606728686829571134'])
        self.assertNotIn('领证', [term for terms in evidence['matched_terms'].values() for term in terms])
        contract['author_input']['premise'] = '陌生人先婚后爱，从领证开始同居并照顾猫'
        evidence = research_context(contract)['continuous_pull']['continuous_reading_evidence']
        self.assertEqual(evidence['evidence'][0]['book_id'], '7655368153093901336')

    def test_forbidden_terms_and_character_gender_do_not_choose_genre(self):
        contract = {'genre': {'primary': 'custom'}, 'story_engine': 'unknown',
                    'constraints': {'avoid': ['规则怪谈', '婆媳']},
                    'characters': [{'gender': 'male'}]}
        self.assertEqual(research_context(contract)['selected_families'], [])
        self.assertEqual(research_context(contract)['audience_track'], 'general')
        implied = research_context({'genre': {'primary': 'custom', 'custom_description': '科幻末世'}, 'story_engine': 'unknown'})
        self.assertEqual(implied['selected_families'], ['survival'])

    def test_packaged_library_covers_inventory_and_reports_unknown_tags(self):
        import tempfile, os
        library = research_library()
        self.assertEqual(len(library['tags']), 115)
        for family in library['families'].values():
            self.assertTrue(set(family['evidence_ids']).issubset(library['evidence']))
            self.assertEqual(set(family['stages']), {'ideation','opening','plan','write','review','package'})
        ctx = research_context({'research_preferences': {'reader_tags': ['十日衍生','新标签']}})
        self.assertEqual(ctx['unmapped_reader_tags'], ['新标签'])
        self.assertIn('专项补证', ctx['tag_limitations']['十日衍生'])
        with tempfile.TemporaryDirectory() as directory:
            previous = os.getcwd()
            try:
                os.chdir(directory)
                self.assertEqual(len(research_library()['tags']), 115)
            finally:
                os.chdir(previous)

    def test_family_evidence_has_correct_emotion_and_reading_scope(self):
        romance = research_context({'research_preferences': {'reader_tags':['甜宠']}}, task='write_short_story')
        titles = {row['title'] for row in romance['topic_guidance']['romance']['evidence']}
        self.assertIn('我把暗恋写成小说，他把重逢变成蓄谋', titles)
        self.assertNotIn('大小姐失忆，送电子厂打工', titles)
        self.assertNotIn('同事死后，我娶了他的老婆', titles)
        survival = research_context({'research_preferences': {'reader_tags':['末日求生']}}, task='write_story_scene')
        cards = survival['topic_guidance']['survival']['evidence']
        self.assertIn('末日降临人人都有系统，我不激活系统只老实囤物资', {row['title'] for row in cards})
        self.assertTrue(cards)
        self.assertTrue(all('write' in row['supports_stages'] and row.get('read_depth') != 'opening_only' for row in cards))

    def test_rebuild_timestamp_does_not_invalidate_identical_research(self):
        library = research_library()
        baseline = guidance_fingerprint()
        library['built_at'] = 'different timestamp'
        with patch('short_fiction_agent.story_studio.research_guidance.research_library', return_value=library):
            self.assertEqual(guidance_fingerprint(), baseline)

    def test_each_sent_mechanism_has_sent_evidence_even_with_two_routes(self):
        ctx = research_context({'research_preferences': {'reader_tags':['宫斗宅斗','古代言情','权谋'],
            'topic_family':'ancient_power'}}, task='write_story_scene')
        card = ctx['topic_guidance']['ancient_power']
        self.assertEqual(len(card['mechanisms']), 2)
        for mechanism in card['mechanisms']:
            self.assertTrue(mechanism['evidence_ids'])
            self.assertTrue(set(mechanism['evidence_ids']).issubset(card['evidence_ids']))

    def test_usage_receipt_and_packaging_receive_research(self):
        from short_fiction_agent.story_studio.storage import read_json
        self.flow.call_json('channel_metadata', '为短故事提供标题')
        rows = [__import__('json').loads(line) for line in (self.project.root / 'runs' / 'events.jsonl').read_text().splitlines()]
        call = next(row for row in rows if row.get('event') == 'model_call')
        self.assertEqual(call['research_usage']['stage'], 'package')
        self.assertIn('evidence_ids', call['research_usage'])

    def test_review_quote_cannot_silently_anchor_other_paragraph(self):
        from short_fiction_agent.story_studio.quality import ground_issue
        from short_fiction_agent.story_studio.contracts import StoryError
        body = '他走了。\n她笑了。\n他走了。'
        for issue in ({'quote':'不存在', 'paragraph_index':0},
                      {'quote':'她笑了。', 'paragraph_index':0}, {'quote':'他走了。'}):
            with self.assertRaises(StoryError): ground_issue(issue, body)
        anchored = ground_issue({'quote':'他走了。','paragraph_index':2}, body)
        self.assertEqual(anchored['span'], [10, 14])

    def test_new_research_invalidates_comparison_and_review_inputs(self):
        old = (comparison_input_hash({}, [], ''), opening_comparison_input_hash({}, {}, []), review_rubric_hash())
        with patch('short_fiction_agent.story_studio.selection_review.guidance_fingerprint', return_value='new'), patch('short_fiction_agent.story_studio.quality.guidance_fingerprint', return_value='new'):
            new = (comparison_input_hash({}, [], ''), opening_comparison_input_hash({}, {}, []), review_rubric_hash())
        self.assertTrue(all(a != b for a,b in zip(old,new)))

    def test_system_tag_cannot_bypass_source_premise_exclusion(self):
        contract = {'format': 'short_story', 'reader_promise': '夺回系统、获得财富和双向吸引',
                    'author_input': {'premise': '都市维修工与女总裁，反抗越权任务代理'},
                    'research_preferences': {'reader_tags': ['系统','男生情感','爽文'], 'audience_track': 'male'}}
        for task in ('story_candidates', 'write_story_scene', 'continuity_review'):
            context = research_context(contract, task=task)
            cases = [r for card in context['topic_guidance'].values() for r in card['evidence']]
            cases += context['continuous_pull']['continuous_reading_evidence']['evidence']
            cases += context['whole_story_research']['evidence']
            self.assertFalse(any('末世' in r['title'] or '末日' in r['title'] for r in cases))
            for card in context['topic_guidance'].values():
                for mechanism in card['mechanisms']:
                    self.assertTrue(mechanism['evidence_ids'])
                    self.assertTrue(set(mechanism['evidence_ids']) <= set(card['evidence_ids']))

    def test_new_desire_mechanisms_are_scoped_and_traceable(self):
        library = desire_payoff_library()
        self.assertEqual(len(library['mechanisms']), 11)
        contract = {'format': 'short_story', 'reader_promise': '拿到三十万并夺回系统权限',
                    'author_input': {'premise': '男主攻略女总裁，反抗系统越界任务'},
                    'research_preferences': {'reader_tags': ['系统','男生情感','爽文'],
                                             'audience_track': 'male'}}
        for task in ('story_candidates', 'write_story_scene', 'continuity_review'):
            ctx = research_context(contract, task=task)['desire_payoff_research']
            ids = {row['id'] for row in ctx['mechanisms']}
            self.assertIn('system_refusal_with_gain', ids)
            self.assertIn('male_ceo_system_reward', ids)
            self.assertTrue(all(row['evidence_ids'] for row in ctx['mechanisms']))
            self.assertIn('本轮没有直接证明', next(row for row in ctx['mechanisms']
                          if row['id'] == 'male_ceo_system_reward')['evidence_limit'])
        quiet = research_context({'format':'short_story', 'reader_promise':'山洞里全家吃火锅',
                   'research_preferences':{'reader_tags':['末日求生']}},
                   task='write_story_scene')['desire_payoff_research']
        self.assertIn('quiet_comfort_payoff', {row['id'] for row in quiet['mechanisms']})
        self.assertNotIn('male_ceo_system_reward', {row['id'] for row in quiet['mechanisms']})
        serial = research_context({**contract, 'format':'short_serial'})['desire_payoff_research']
        self.assertEqual(serial['mechanisms'], [])

    def test_reward_guidance_changes_all_research_bound_cache_keys(self):
        old = (guidance_fingerprint(), comparison_input_hash({}, [], ''),
               opening_comparison_input_hash({}, {}, []), review_rubric_hash())
        with patch('short_fiction_agent.story_studio.reader_rewards.GUIDANCE', 'changed reward instructions'):
            new = (guidance_fingerprint(), comparison_input_hash({}, [], ''),
                   opening_comparison_input_hash({}, {}, []), review_rubric_hash())
        self.assertTrue(all(a != b for a,b in zip(old,new)))

    def test_research_updates_refresh_cached_candidates(self):
        self.flow.develop()
        before = self.llm.tasks.count('story_candidates')
        with patch('short_fiction_agent.story_studio.workflow.guidance_fingerprint', return_value='new'):
            self.flow.develop()
        self.assertGreater(self.llm.tasks.count('story_candidates'), before)

    def test_short_scene_pipeline_and_checkpoint_research_version(self):
        import test_story_resume
        test_story_resume.ResumeTests.setUp(self)
        prompts = {}
        original = self.llm.generate
        def capture(prompt, task):
            prompts.setdefault(task, []).append(prompt)
            return original(prompt, task)
        self.llm.generate = capture
        plan = self.flow.plan()
        self.flow._draft_text(plan)
        for task in ('story_plan', 'write_story_scene'):
            self.assertIn(VERSION, prompts[task][0])
        audit = prompts.get('chief_plan_review', prompts.get('blueprint_audit'))
        self.assertTrue(audit)
        self.assertIn('plan', audit[0])
        writes = len(prompts['write_story_scene'])
        self.flow._draft_text(plan)
        self.assertEqual(len(prompts['write_story_scene']), writes)
        with patch('short_fiction_agent.story_studio.workflow.guidance_fingerprint', return_value='new'):
            self.flow._draft_text(plan)
        self.assertEqual(len(prompts['write_story_scene']), 2 * writes)
