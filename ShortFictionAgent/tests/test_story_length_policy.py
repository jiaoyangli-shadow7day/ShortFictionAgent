"""Length limits, lead independence and author approval regression tests."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import lead
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest, length_guidance
from short_fiction_agent.story_studio.quality import length_status


class StoryLengthPolicyTests(unittest.TestCase):
    def brief(self, **overrides):
        return StoryBrief.from_dict({
            'format': 'short_story', 'opening_mode': 'lead_then_chapter',
            'author_input': {'premise': '女主拿着旧借条来找我，要我兑现当年的承诺。'},
            **overrides})

    def test_soft_target_has_no_implicit_upper_limit_and_round_trips(self):
        brief = self.brief(length={'target': 24000, 'min': 20001})
        self.assertIsNone(brief.length['max'])
        reloaded = StoryBrief.from_dict(brief.to_dict())
        contract = reloaded.effective_contract()
        self.assertTrue(length_status('字' * 31000, contract)['within_range'])
        self.assertEqual(contract['length']['target'], 24000)
        self.assertFalse(length_status('字' * 19999, contract)['within_range'])

    def test_every_format_default_has_no_obsolete_hard_maximum(self):
        from short_fiction_agent.story_studio.contracts import FORMAT_DEFAULTS
        for format_name, (_, _, maximum) in FORMAT_DEFAULTS.items():
            with self.subTest(format=format_name):
                self.assertIsNone(maximum)
                brief = StoryBrief.from_dict({'format': format_name,
                    'author_input': {'premise': '主人公来取回父亲留下的旧借条'}})
                self.assertIsNone(brief.length['max'])

    def test_review_defaults_to_chief_and_deep_review_requires_opt_in(self):
        self.assertEqual(self.brief().effective_contract()['review_mode'], 'chief_editor')
        self.assertEqual(self.brief(review_mode='deep_review').review_mode, 'deep_review')
        with self.assertRaisesRegex(StoryError, 'review_mode'):
            self.brief(review_mode='automatic_extra_reviews')

    def test_declared_hard_limit_still_blocks_the_complete_manuscript(self):
        contract = self.brief(length={'target': 100, 'min': 80, 'max': 120}).effective_contract()
        self.assertFalse(length_status('字' * 121, contract)['within_range'])
        self.assertNotIn('作者明确', length_guidance(contract))
        self.assertIn('已声明硬上限', length_guidance(contract))

    def test_hard_limit_source_is_explicit_and_platform_source_requires_verification(self):
        length = {'target': 100, 'min': 80, 'max': 120,
                  'hard_max_source': {'kind': 'author', 'reference': '作者明确要求不超过120字'}}
        self.assertIn('作者明确硬上限', length_guidance(self.brief(length=length).length))
        length['hard_max_source'] = {'kind': 'platform', 'reference': '活动后台已确认计字规则'}
        with self.assertRaisesRegex(StoryError, 'hard_max_source'):
            self.brief(length=length)
        length['hard_max_source']['verified'] = True
        self.assertIn('已核实投稿硬上限', length_guidance(self.brief(length=length).length))
        length['max'] = None
        with self.assertRaisesRegex(StoryError, '无硬上限'):
            self.brief(length=length)

    def test_length_guidance_never_renders_none_as_a_numeric_bound(self):
        contract = self.brief(length={'target': 24000, 'max': None}).effective_contract()
        guidance = length_guidance(contract)
        self.assertNotIn('None', guidance)
        self.assertIn('参考目标约24000', guidance)
        self.assertIn('未设整篇硬上限', guidance)
        self.assertEqual(guidance, length_guidance(contract['length']))

    def test_complete_lead_is_kept_even_when_body_used_a_hard_limit(self):
        contract = self.brief(length={'target': 100, 'min': 80, 'max': 120}).effective_contract()
        story = '正文需要完整保留人物行动。' * 20
        text = '她递来那张借条，问我当年的承诺还算不算数。' * 40
        self.assertGreater(len(text), 600)
        prompt = lead.single_candidate_prompt(story, contract)
        self.assertNotIn('每项80至', prompt)
        self.assertEqual(lead.validate_single_candidate({'text': text}, story, contract), text)
        body = lead.assemble(text, story)
        self.assertEqual(lead.split(body)[0], text)
        self.assertFalse(length_status(body, contract)['within_range'])
        self.assertEqual(lead.story_length(contract), contract['length'])

    def test_deep_candidates_do_not_reintroduce_a_lead_length_cap(self):
        contract = self.brief(length={'target': 100, 'min': 80, 'max': 120}).effective_contract()
        story = '她递来了十年前那张写着承诺的借条。' * 15
        rows = [{'id': f'l{i}', 'text': (f'第{i}次见她，我仍记得那张借条。' * 50),
                 'style': lead.STYLES[i], 'time_relation': '预告后回到当天',
                 'reader_question': '她要怎样兑现承诺', 'hook_mechanism': '旧承诺改变眼前选择',
                 'payoff_quote': story[:20]} for i in range(3)]
        self.assertEqual(lead.validate_candidates({'leads': rows}, story, contract), rows)
        self.assertNotIn('80至', lead.candidates_prompt(story, contract, {}))

    def test_single_candidate_rejects_invalid_structure_without_semantic_fabrication(self):
        contract = self.brief().effective_contract()
        for response in ({}, {'text': None}, {'text': '  '}, {'text': '第一章\n误附正文。'},
                         {'text': '导语：这是标题。'}, {'text': '[q000]证据编号误入正文。'}):
            with self.subTest(response=response), self.assertRaises(StoryError):
                lead.validate_single_candidate(response, '完整正文。', contract)

    def test_entry_facts_preserve_author_choice_and_fallback_quotes_no_avoid_list(self):
        contract = self.brief(author_input={
            'premise': '女主递来旧借条，要我兑现承诺。',
            'locked_facts': ['主角从现代穿越到1990年香港', '未来记录随手机穿越且离线运行', '她30岁'],
            'entry_facts': ['旧借条上的签名是我父亲的', '她要我兑现父亲留下的承诺']}).effective_contract()
        self.assertEqual(lead.entry_facts(contract), contract['author_input']['entry_facts'])
        del contract['author_input']['entry_facts']
        self.assertEqual(lead.entry_facts(contract), contract['author_input']['locked_facts'][:2])
        del contract['author_input']['locked_facts']
        contract['constraints']['avoid'] = ['不要写穿越']
        self.assertEqual(lead.entry_facts(contract), ['女主递来旧借条，要我兑现承诺。'])

    def test_self_sufficient_lead_policy_overrides_archived_title_deduplication(self):
        contract = self.brief().effective_contract()
        original = copy.deepcopy(lead.library())
        raw_hash = digest(original)
        prompt = lead.single_candidate_prompt('完整正文。', contract)
        context = lead.context(contract, 'lead')
        self.assertIn('标题已透露的设定若是必要前提，仍须交代', prompt)
        self.assertIn('正文变化后继续核对事实，不自动重选；主审发现实质问题时返回作者', prompt)
        self.assertNotIn('正文变化后只报告具体事实失配', prompt)
        self.assertIn('作者确认的导语保留原文，不自动替换，正文变化后继续核对事实；主审发现实质问题时返回作者', lead.EDIT_INSTRUCTION)
        self.assertNotIn('只报告具体事实失配', lead.EDIT_INSTRUCTION)
        self.assertNotIn('80—600', ''.join(context['principles']))
        self.assertNotIn('标题已给的关系或设定', ''.join(context['principles']))
        self.assertEqual(digest(lead.library()), raw_hash)
        self.assertEqual(lead.library(), original)

    def test_default_prompt_carries_research_checks_and_sentence_examples(self):
        contract = self.brief().effective_contract()
        contract['genre_guidance'] = ['重复派生指导不应再次注入']
        contract['market_guidance'] = {'marker': '重复市场指导'}
        contract['market_basis'] = {'marker': '重复市场来源'}
        contract['publication_guidance'] = {'marker': '重复投稿指导'}
        contract['constraints']['must_keep'] = ['借条上的签名是父亲留下的']
        original_contract = copy.deepcopy(contract)
        story = '她把父亲留下的借条摊在桌上，问我要怎样偿还。'
        prompt = lead.single_candidate_prompt(story, contract)
        data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
        research = data['lead_research']
        self.assertEqual(research['checks'], lead.CHECKS)
        for rule in ('首句与前三句分别审查', '人物实际回应', '下一场邀约',
                     '不能逐字重播导语', '排练过', '实际执行', '核对相邻回应'):
            self.assertIn(rule, prompt)
        self.assertEqual(len(research['sentence_examples']), 2)
        for example in research['sentence_examples']:
            self.assertTrue(example['first_sentence'])
            self.assertTrue(example['first_three_sentences'].startswith(example['first_sentence']))
            self.assertTrue(example['first_sentence_analysis'])
            self.assertTrue(example['actual_change'])
            self.assertTrue(example['limits'])
        self.assertLessEqual(len(research['principles']), 8)
        self.assertIn('可在内部尝试不同入口', prompt)
        self.assertIn('不输出候选列表', prompt)
        self.assertNotIn('三候选', ''.join(research['principles']))
        self.assertEqual(research['library_fingerprint'], digest(lead.library()))
        self.assertIn('精选', research['selection_scope'])
        self.assertIn('lead.py:CHECKS', research['source_paths'])
        for key in ('genre_guidance', 'market_guidance', 'market_basis', 'publication_guidance'):
            self.assertNotIn(key, data['contract'])
        self.assertEqual(data['contract']['author_input'], contract['author_input'])
        self.assertEqual(data['contract']['constraints'], contract['constraints'])
        self.assertEqual(data['story'], story)
        self.assertEqual(contract, original_contract)

    def test_default_prompt_preserves_full_story_above_thirty_thousand_characters(self):
        story = '她将借条交给我，等我作出自己的选择。\n' * 2000
        self.assertGreater(len(story), 30000)
        prompt = lead.single_candidate_prompt(story, self.brief().effective_contract())
        data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
        self.assertEqual(data['story'], story)
        self.assertEqual(data['lead_research']['checks'], lead.CHECKS)

    def test_default_task_uses_author_viewpoint_and_concrete_choices_instead_of_success_summary(self):
        for viewpoint in ('first_person', 'third_person_limited'):
            with self.subTest(viewpoint=viewpoint):
                contract = self.brief(point_of_view=viewpoint).effective_contract()
                story = '我想请她给我留一个版权商谈的位置。她问我，今晚的预言还算不算数。'
                prompt = lead.single_candidate_prompt(story, contract)
                instruction, serialized = prompt.rsplit('\n', 1)
                self.assertIn('本篇作者指定叙述视角为' + viewpoint, instruction)
                self.assertIn('人物自己的说话、判断或行动', instruction)
                self.assertIn('人物选择和实际接招', instruction)
                self.assertIn('本篇独有且尚未演完的局面', instruction)
                self.assertIn('总结不能代替人物现场与追读期待', instruction)
                self.assertIn('可以预告结果', instruction)
                self.assertIn('不硬加对白', instruction)
                self.assertEqual(json.loads(serialized)['contract']['point_of_view'], viewpoint)
                material_prompt = lead.material_candidate_prompt(
                    lead.story_paragraphs(story), contract, {'source_chars': len(story)})
                self.assertIn('本篇作者指定叙述视角为' + viewpoint, material_prompt)
                self.assertIn('人物选择和实际接招', material_prompt)
                self.assertIn('当前窗口只有source_materials中的精选原文', material_prompt)

    def test_author_approval_preserves_lead_and_requests_facts_after_story_change(self):
        contract = self.brief().effective_contract()
        text = '她递来借条，要我兑现当年的承诺。'
        body = lead.assemble(text, '她来到了旧宅，带着那张签过字的借条。')
        receipt = lead.approve_lead(body, contract)
        self.assertFalse(lead.validate_approved_lead(body, contract, receipt)['needs_fact_review'])
        changed = lead.assemble(text, '她来到了车站，那张借条已经被人撕毁。')
        checked = lead.validate_approved_lead(changed, contract, receipt)
        self.assertTrue(checked['needs_fact_review'])
        self.assertTrue(checked['story_changed'])
        self.assertEqual(lead.confirmed_lead(changed, contract, receipt), text)
        self.assertNotIn('needs_fact_review', receipt)

    def test_author_approval_cannot_silently_replace_lead_or_accept_tampered_receipt(self):
        contract = self.brief().effective_contract()
        body = lead.assemble('她递来借条，要我兑现当年的承诺。', '她带着借条走到我面前。')
        receipt = lead.approve_lead(body, contract)
        with self.assertRaisesRegex(StoryError, '确认原文不一致'):
            lead.validate_approved_lead(body.replace('当年', '昨日', 1), contract, receipt)
        tampered = copy.deepcopy(receipt)
        tampered['story_hash'] = digest('伪造正文绑定')
        with self.assertRaisesRegex(StoryError, '被修改'):
            lead.validate_approved_lead(body, contract, tampered)
        altered_contract = copy.deepcopy(contract)
        altered_contract['length']['target'] += 1000
        self.assertTrue(lead.validate_approved_lead(body, altered_contract, receipt)['needs_fact_review'])


if __name__ == '__main__':
    unittest.main()
