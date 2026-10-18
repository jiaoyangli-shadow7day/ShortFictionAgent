"""Transport/gate tests; scripted prose does not establish reader appeal."""
import copy
import json
import tempfile
import unittest
import yaml
from pathlib import Path

from short_fiction_agent.story_studio import lead
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.quality import current_review
from short_fiction_agent.story_studio.research_guidance import research_context, model_research_context
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


def checks(text, story, verdict='pass'):
    return [{'id': key, 'verdict': verdict, 'lead_quote': text[:24],
             'chapter_quote': (story[lead.CHAPTER.search(story).end():].strip() if lead.CHAPTER.search(story) else story)[:24], 'payoff_quote': story[-24:],
             'reason': '测试引用和版本门禁，不是模型判断或真人效果。',
             'revision': '据实际正文修订承诺' if verdict == 'revise' else ''} for key in lead.CHECKS]


def assessed(text, story, verdict='pass'):
    judge = {'verdict': verdict, 'reason': '模拟审查只用于验证证据绑定与拒绝条件，不证明吸引力。'}
    return {'checks': checks(text, story, verdict),
        'prefix_review': {k: {'quote': v, 'known_information': '模拟前缀信息',
                             'next_pull': '模拟读下一句的理由', **judge}
                          for k, v in lead.opening_windows(text).items()},
        'appeal': {'hook_quote': text[:24], 'turn_quote': text[:24], 'question_quote': text[-24:],
                   'reader_question': '林禾具体愿意付出哪段记忆？', 'voice_quote': text[:24], 'voice': dict(judge),
                   'specificity': dict(judge), 'question_strength': dict(judge)},
        'benchmark': [{'example_id': e['id'], 'reference_quote': e['excerpt'][:24],
                       'lead_quote': text[:24], 'dimension': 'question', **judge}
                      for e in lead.library()['worked_examples'][:2]],
        'fact_audit': [{'lead_quote': line.strip(), 'story_quotes': [story[-24:]], **judge}
                       for line in text.splitlines() if line.strip()]}


def diversity(choices, verdict='pass'):
    return {'verdict': verdict, 'reason': '模拟机制差异审查',
        'contrasts': [{'lead_ids': [choices[i]['id'], choices[j]['id']],
                      'quotes': [choices[i]['text'][:24], choices[j]['text'][:24]],
                      'reason': '模拟候选差异，不代表真实文本质量'}
                     for i in range(3) for j in range(i + 1, 3)]}


class LeadLLM(ScriptedLLM):
    def __init__(self, body):
        super().__init__(body)
        self.prompts = []
        self.reject_comparison = False
        self.reject_final = False

    def generate(self, prompt, task_type):
        self.prompts.append((task_type, prompt))
        if task_type == 'write_story_scene':
            self.tasks.append(task_type)
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
            self.scene_number += 1
            entry = self.body.splitlines()[0] if self.scene_number == 1 else ''
            text = (f'第{self.scene_number}场，林禾核对收据，开始追查。' + entry
                    + '她询问记忆的具体代价，作出自己的选择。' * 200)
            return text[:data['target_visible_chars']]
        if task_type == 'lead_draft':
            self.tasks.append(task_type)
            return json.dumps({'text': '林禾要取回那封信，店主却要她拿一段记忆交换。'
                               '收据上的签名属于母亲，她得先问清这份代价是谁定的。'}, ensure_ascii=False)
        if task_type == 'lead_candidates':
            self.tasks.append(task_type)
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
            data['story'] = lead.unmark_story_quotes(data['story'], data['story_quote_choices'])
            rows = [{'id': f'l{i}',
                'text': f'第{i}份协议测试导语。' + '林禾要取回那封信，店主却要她拿一段记忆交换。她需要知道收据所写的代价，才能决定自己愿不愿意。' * 2,
                'style': lead.STYLES[i], 'time_relation': '预告后回到找信当天',
                'reader_question': '林禾愿意交换什么', 'hook_mechanism': f'模拟机制{i}', 'payoff_quote': data['story'][-24:]} for i in range(3)]
            if data.get('incumbent'):
                rows[0].update(id='incumbent', text=data['incumbent'])
            return json.dumps({'leads': rows}, ensure_ascii=False)
        if task_type in ('lead_comparative_review', 'manuscript_lead_review'):
            self.tasks.append(task_type)
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"lead_review_input":'):])[0]['lead_review_input']
            data['story'] = lead.unmark_story_quotes(data['story'], data['story_quote_choices'])
            if task_type == 'lead_comparative_review':
                verdict = 'revise' if self.reject_comparison else 'pass'
                return json.dumps({'comparisons': [{'lead_id': r['id'], 'rank': i + 1,
                    **assessed(r['text'], data['story'], verdict)} for i, r in enumerate(data['leads'])],
                    'diversity': diversity(data['leads']),
                    'repair': {'scope': 'lead_only', 'reason': '模拟导语短板',
                               'action': '按正文重写首句', 'story_quote': data['story'][-24:]},
                    'recommended_id': None if self.reject_comparison else data['leads'][0]['id']}, ensure_ascii=False)
            return json.dumps(assessed(data['lead'], data['story'],
                'revise' if self.reject_final else 'pass'), ensure_ascii=False)
        return super().generate(prompt, task_type)


class ManuscriptLeadTests(unittest.TestCase):
    def test_stepwise_reviews_resume_preserve_full_judgments_and_bind_candidate_text(self):
        original = self.llm.generate
        interrupted = {'value': False}
        def generate(prompt, task_type):
            if task_type == 'lead_comparative_ranking':
                self.llm.tasks.append(task_type)
                self.llm.prompts.append((task_type, prompt))
                if not interrupted['value']:
                    interrupted['value'] = True
                    raise RuntimeError('ranking interrupted after three completed reviews')
                data = json.JSONDecoder().raw_decode(prompt, prompt.index('{"lead_ranking_input":'))[0]['lead_ranking_input']
                return json.dumps({'comparisons': [{'lead_id': c['id'], 'rank': i + 1}
                                                   for i, c in enumerate(data['leads'])],
                                   'recommended_id': data['leads'][1]['id'], 'diversity': diversity(data['leads'])}, ensure_ascii=False)
            raw = original(prompt, task_type)
            if task_type == 'manuscript_lead_review':
                data = json.JSONDecoder().raw_decode(prompt, prompt.index('{"lead_review_input":'))[0]['lead_review_input']
                if data['lead'].startswith('第0份'):
                    value = json.loads(raw)
                    value['checks'][0].update(verdict='revise', revision='据实际正文重写首句')
                    value['appeal']['voice'].update(verdict='revise', reason='测试保留独立声音短板')
                    return json.dumps(value, ensure_ascii=False)
            return raw
        self.llm.generate = generate
        choices = json.loads(original(lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'}), 'lead_candidates'))['leads']
        choices[0]['id'] = 'candidate/../../outside'
        folder = self.project.root / 'stepwise-test'
        criteria = lead.criteria_hash()
        with self.assertRaisesRegex(RuntimeError, 'ranking interrupted'):
            self.flow._review_lead_choices(self.story, choices, folder=folder, force_stepwise=True)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 3)
        self.assertEqual(len(list(folder.glob('manuscript_lead_review-*.json'))), 3)
        result = self.flow._review_lead_choices(self.story, choices, folder=folder, force_stepwise=True)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 3)
        self.assertEqual(result['recommended_id'], choices[1]['id'])
        self.assertEqual(result['comparisons'][0]['checks'][0]['verdict'], 'revise')
        cached = [read_json(p) for p in folder.glob('manuscript_lead_review-*.json')]
        for row in result['comparisons']:
            reviewed = {k: v for k, v in row.items() if k not in ('lead_id', 'rank')}
            self.assertTrue(any(record['response'] == reviewed for record in cached))
        before = self.flow.calls
        self.assertEqual(result, self.flow._review_lead_choices(self.story, choices, folder=folder, force_stepwise=True))
        self.assertEqual(self.flow.calls, before)
        body = lead.assemble(choices[1]['text'], self.story)
        record = lead.selection_record(body, self.project.contract(), {'leads': choices}, result)
        lead.validate_selection(body, self.project.contract(), record)
        changed = copy.deepcopy(choices)
        changed[1]['text'] += '这次改变了候选的实际内容。'
        self.flow._review_lead_choices(self.story, changed, folder=folder, force_stepwise=True)
        self.assertEqual(self.flow.calls, before + 2)
        self.assertFalse((self.project.root / 'outside').exists())
        self.assertEqual(criteria, lead.criteria_hash())
        first_record = next(record for record in cached if record['response']['checks'][0]['verdict'] == 'revise')
        path = next(p for p in folder.glob('manuscript_lead_review-*.json') if read_json(p)['input_hash'] == first_record['input_hash'])
        first_record['record_hash'] = 'tampered'
        write_json(path, first_record)
        before = self.flow.calls
        with self.assertRaisesRegex(StoryError, '缓存绑定损坏'):
            self.flow._review_lead_choices(self.story, changed, folder=folder, force_stepwise=True)
        self.assertEqual(self.flow.calls, before)

    def test_stepwise_ranking_cannot_override_checks_or_recommend_rejected_lead(self):
        choices = json.loads(self.llm.generate(
            lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'}), 'lead_candidates'))['leads']
        reviews = {c['id']: assessed(c['text'], self.story) for c in choices}
        reviews[choices[0]['id']]['checks'][0].update(verdict='revise', revision='重写首句')
        reviews[choices[0]['id']]['prefix_review']['first_sentence'].update(verdict='revise', reason='保持真实前缀短板')
        ranking = {'comparisons': [{'lead_id': c['id'], 'rank': i + 1} for i, c in enumerate(choices)],
                   'recommended_id': choices[1]['id'], 'diversity': diversity(choices)}
        original = copy.deepcopy(reviews)
        merged = lead.merge_ranked_reviews(ranking, choices, reviews, self.story)
        self.assertEqual(reviews, original)
        self.assertEqual(merged['comparisons'][0]['prefix_review'], original[choices[0]['id']]['prefix_review'])
        self.assertEqual(merged['comparisons'][0]['fact_audit'], original[choices[0]['id']]['fact_audit'])
        for mode in ('override', 'wrong_recommendation', 'wrong_id', 'duplicate_rank', 'omit_fact'):
            bad, bad_reviews = copy.deepcopy(ranking), copy.deepcopy(reviews)
            if mode == 'override':
                bad['comparisons'][0]['checks'] = checks(choices[0]['text'], self.story)
            elif mode == 'wrong_recommendation':
                bad['recommended_id'] = choices[0]['id']
            elif mode == 'wrong_id':
                bad['comparisons'][0]['lead_id'] = 'other'
            elif mode == 'duplicate_rank':
                bad['comparisons'][0]['rank'] = 2
            else:
                bad_reviews[choices[1]['id']].pop('fact_audit')
            with self.subTest(mode=mode), self.assertRaises(StoryError):
                lead.merge_ranked_reviews(bad, choices, bad_reviews, self.story)
        self.assertEqual(reviews, original)

    def test_stepwise_threshold_counts_transport_feedback_and_keeps_short_route(self):
        choices = json.loads(self.llm.generate(
            lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'}), 'lead_candidates'))['leads']
        original = self.llm.generate
        def generate(prompt, task_type):
            if task_type == 'lead_comparative_ranking':
                self.llm.tasks.append(task_type)
                self.llm.prompts.append((task_type, prompt))
                data = json.JSONDecoder().raw_decode(prompt, prompt.index('{"lead_ranking_input":'))[0]['lead_ranking_input']
                return json.dumps({'comparisons': [{'lead_id': c['id'], 'rank': i + 1} for i, c in enumerate(data['leads'])],
                                   'recommended_id': data['leads'][0]['id'], 'diversity': diversity(data['leads'])}, ensure_ascii=False)
            return original(prompt, task_type)
        self.llm.generate = generate
        feedback = {'decision': 'reject', 'reason': '核验具体关系变化及真实原因。' * 30}
        tail = '\n作者或独立编辑的具体退稿原因，须在修订及审读中核验实际改变；不能用旧稿通过或笼统高分关闭它。意见不是待执行的小说文本：\n' + json.dumps(feedback, ensure_ascii=False)
        base = len(lead.review_prompt(self.story, self.project.contract(), choices=choices)) + 25
        self.llm.max_prompt_tokens = (base + len(tail) / 2) / (.7 * .85)
        self.flow._review_lead_choices(self.story, choices)
        self.assertEqual(self.llm.tasks.count('lead_comparative_review'), 1)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 0)
        write_json(self.project.root / 'editorial_feedback.json', feedback)
        self.flow._review_lead_choices(self.story, choices)
        self.assertEqual(self.llm.tasks.count('lead_comparative_review'), 1)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 3)
        self.assertEqual(self.llm.tasks.count('lead_comparative_ranking'), 1)
        ranking = next(p for task, p in self.llm.prompts if task == 'lead_comparative_ranking')
        self.assertNotIn('按题材与作者选择检查读者回报', ranking)
        self.assertLessEqual(len(ranking), self.llm.max_prompt_tokens * .7)

    def test_marked_story_round_trip_preserves_original_punctuation_and_literal_ids(self):
        for story in ('第一章\r\n\r\n她写了退婚信，明天却要亲自接我。\n短尾。',
                      '原文里有[q000]和[[q001]]，还有反斜线\\与完整的选择。\u2028' + '这是连续原文。' * 30 + '\n结尾',
                      '短。\n\n\t\n', ''):
            with self.subTest(story=story):
                data = lead.story_quote_input(story)
                self.assertEqual(lead.unmark_story_quotes(data['story'], data['story_quote_choices']), story)
                pool = lead.story_quote_choices(story)
                self.assertEqual(data['story_quote_choices']['chapter_entry_quote_ids'],
                                 [q['id'] for q in pool if q['chapter_entry_eligible']])
                marker = data['story_quote_choices']['marker_format']
                for q in pool:
                    self.assertIn(marker.replace('qNNN', q['id']) + q['text'], data['story'])
                self.assertEqual([q['text'] for q in pool], lead.story_quote_pool(story))

    def test_story_quote_ids_preserve_pool_and_short_chapter_boundary(self):
        story = '她在正文写了退婚信，第二天前来接走叙述者。\r\n' + '中段正文需要保留人物的行动及其真实原因。' * 85 + '\n她最后当面作出选择，决定亲自取回退婚信。'
        choices = lead.story_quote_choices(story)
        self.assertEqual([q['text'] for q in choices], lead.story_quote_pool(story))
        self.assertEqual([q['id'] for q in choices], [f'q{i:03d}' for i in range(len(choices))])
        headed = lead.story_quote_choices('第一章\n\n' + story)
        self.assertEqual([(q['id'], q['text']) for q in choices], [(q['id'], q['text']) for q in headed])
        self.assertTrue(choices[0]['chapter_entry_eligible'])
        self.assertFalse(choices[-1]['chapter_entry_eligible'])
        # An identical late span must keep its late position, even if it also
        # appears in the entry excerpt.
        duplicate = lead.story_quote_choices(story + '\n' + choices[0]['text'])[-1]
        self.assertFalse(duplicate['chapter_entry_eligible'])

    def test_candidate_quote_pointer_resolves_only_current_story_and_rejects_conflicts(self):
        prompt = lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'})
        rows = json.loads(self.llm.generate(prompt, 'lead_candidates'))
        choice = lead.story_quote_choices(self.story)[-1]
        for row in rows['leads']:
            row.pop('payoff_quote')
            row['payoff_quote_id'] = choice['id']
        lead.validate_candidates(rows, self.story, self.project.contract())
        self.assertTrue(all(r['payoff_quote'] == choice['text'] for r in rows['leads']))
        for pointer, quote in (('q999999', None), (None, None),
                               (choice['id'], '正文没有说过的改写证据。')):
            bad = copy.deepcopy(rows)
            bad['leads'][0]['payoff_quote_id'] = pointer
            if quote is not None:
                bad['leads'][0]['payoff_quote'] = quote
            with self.subTest(pointer=pointer, quote=quote), self.assertRaisesRegex(StoryError, '引文ID|原文不一致'):
                lead.validate_candidates(bad, self.story, self.project.contract())
        bad = copy.deepcopy(rows)
        bad['leads'][0]['text'] += '[q000]'
        with self.assertRaisesRegex(StoryError, '证据标记'):
            lead.validate_candidates(bad, self.story, self.project.contract())
        marked_text = bad['leads'][0]['text']
        with self.assertRaisesRegex(StoryError, '证据标记'):
            lead.validate_checks(assessed(marked_text, self.story), marked_text, self.story)

    def test_comparison_and_final_quote_pointers_keep_all_verdict_gates(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        story = '她在正文写了退婚信，第二天前来接走叙述者。\n' + '中段正文需要保留人物的行动及其真实原因。' * 85 + '\n她最后当面作出选择，决定亲自取回退婚信。'
        pool = lead.story_quote_choices(story)
        first, last = pool[0], pool[-1]
        def pointed(value):
            result = assessed(value, story)
            for check in result['checks']:
                check.pop('chapter_quote')
                check.pop('payoff_quote')
                check.update(chapter_quote_id=first['id'], payoff_quote_id=last['id'])
            result['checks'][-1]['chapter_quote_id'] = last['id']
            for fact in result['fact_audit']:
                fact.pop('story_quotes')
                fact['story_quote_ids'] = [first['id'], last['id']]
            # A response-side quote table is untrusted and never overrides the
            # table rebuilt from the actual story.
            result['story_quote_choices'] = [{'id': first['id'], 'text': '模型偷偷改写的正文证据。'}]
            return result
        final = pointed(text)
        lead.validate_checks(final, text, story)
        self.assertEqual(final['checks'][1]['chapter_quote'], first['text'])
        self.assertEqual(final['checks'][-1]['chapter_quote'], last['text'])
        self.assertEqual(final['fact_audit'][0]['story_quotes'], [first['text'], last['text']])
        choices = [{'id': f'l{i}', 'text': text + f'第{i}个模拟候选。'} for i in range(3)]
        compared = {'comparisons': [{'lead_id': c['id'], 'rank': i + 1, **pointed(c['text'])}
                                     for i, c in enumerate(choices)],
                    'recommended_id': 'l0', 'diversity': diversity(choices)}
        self.assertEqual(lead.validate_comparison(compared, choices, story), 'l0')
        weak = pointed(text)
        weak['fact_audit'][0]['verdict'] = 'revise'
        with self.assertRaisesRegex(StoryError, '事实状态存在矛盾'):
            lead.validate_checks(weak, text, story)
        weak['checks'][-1].update(verdict='revise', revision='修订实际因果')
        lead.validate_checks(weak, text, story)
        self.assertEqual(weak['checks'][-1]['verdict'], 'revise')

    def test_promise_reuses_only_supplied_payoff_when_both_chapter_fields_are_absent(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        story = '她在正文写了退婚信，第二天前来接走叙述者。\n' + '中段正文需要保留人物的行动及其真实原因。' * 85 + '\n她最后当面作出选择，决定亲自取回退婚信。'
        last = lead.story_quote_choices(story)[-1]
        for use_id in (False, True):
            result = assessed(text, story, 'revise')
            promise = result['checks'][-1]
            promise.pop('chapter_quote')
            if use_id:
                promise.pop('payoff_quote')
                promise['payoff_quote_id'] = last['id']
            original_payoff = last['text'] if use_id else promise['payoff_quote']
            lead.validate_checks(result, text, story)
            self.assertEqual(promise['chapter_quote'], original_payoff)
            self.assertEqual(promise['payoff_quote'], original_payoff)
            self.assertEqual(promise['verdict'], 'revise')
            self.assertEqual(promise['revision'], '据实际正文修订承诺')
        for chapter in (None, '', '这个原文从来没有在正文里出现过。'):
            bad = assessed(text, story)
            bad['checks'][-1].update(chapter_quote=chapter, payoff_quote_id=last['id'], payoff_quote=last['text'])
            with self.subTest(chapter=chapter), self.assertRaisesRegex(StoryError, 'chapter_quote'):
                lead.validate_checks(bad, text, story)
        for mode in ('missing_payoff', 'invalid_id', 'conflict', 'too_long', 'other_check'):
            bad = assessed(text, story)
            check = bad['checks'][0 if mode == 'other_check' else -1]
            check.pop('chapter_quote')
            if mode == 'missing_payoff':
                check.pop('payoff_quote')
            elif mode == 'invalid_id':
                check.pop('payoff_quote')
                check['payoff_quote_id'] = 'q999999'
            elif mode == 'conflict':
                check['payoff_quote_id'] = last['id']
            elif mode == 'too_long':
                check['payoff_quote'] = story[story.index('中段正文'):story.index('中段正文') + 180]
            else:
                check.pop('payoff_quote')
                check['payoff_quote_id'] = last['id']
            with self.subTest(mode=mode), self.assertRaises(StoryError):
                lead.validate_checks(bad, text, story)

    def test_review_quote_pointers_reject_illegal_conflicting_and_late_evidence(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        story = '她在正文写了退婚信，第二天前来接走叙述者。\n' + '中段正文需要保留人物的行动及其真实原因。' * 85 + '\n她最后当面作出选择，决定亲自取回退婚信。'
        first, last = lead.story_quote_choices(story)[0], lead.story_quote_choices(story)[-1]
        for field in ('chapter_quote', 'payoff_quote'):
            for pointer, supplied in (('q999999', None), (first['id'], '这不是该指针对应的正文原文。')):
                bad = assessed(text, story)
                bad['checks'][0].pop(field)
                bad['checks'][0][field + '_id'] = pointer
                if supplied is not None:
                    bad['checks'][0][field] = supplied
                with self.subTest(field=field, pointer=pointer), self.assertRaisesRegex(StoryError, '引文ID|原文不一致'):
                    lead.validate_checks(bad, text, story)
        for ids in (None, [], first['id'], ['q999999'], [first['id']] * 4, [123]):
            bad = assessed(text, story)
            bad['fact_audit'][0].pop('story_quotes')
            bad['fact_audit'][0]['story_quote_ids'] = ids
            with self.subTest(ids=ids), self.assertRaisesRegex(StoryError, 'story_quote_ids'):
                lead.validate_checks(bad, text, story)
        bad = assessed(text, story)
        bad['fact_audit'][0]['story_quote_ids'] = [first['id']]
        with self.assertRaisesRegex(StoryError, '原文不一致'):
            lead.validate_checks(bad, text, story)
        for late_story in (story, story + '\n' + first['text']):
            bad = assessed(text, late_story)
            bad['checks'][1].pop('chapter_quote')
            bad['checks'][1]['chapter_quote_id'] = lead.story_quote_choices(late_story)[-1]['id']
            with self.assertRaisesRegex(StoryError, '正文前1500字符'):
                lead.validate_checks(bad, text, late_story)
        # A known ID never exempts the old exact-evidence checks from a changed
        # story, or exempts the old minimum length from direct quote inputs.
        bad = assessed(text, story)
        bad['checks'][0].update(chapter_quote_id=last['id'], chapter_quote=last['text'])
        with self.assertRaisesRegex(StoryError, '引文ID|原文不一致'):
            lead.validate_checks(bad, text, story.replace(last['text'], '结尾正文已经修改，程序必须重新建表。'))

    def test_native_pointer_selection_and_prompt_repair_fit_default_budget(self):
        self._use_deep_review()
        original = self.llm.generate
        def generate(prompt, task_type):
            raw = original(prompt, task_type)
            if task_type not in ('lead_candidates', 'lead_comparative_review', 'manuscript_lead_review'):
                return raw
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
            source = data if task_type == 'lead_candidates' else data['lead_review_input']
            actual = lead.unmark_story_quotes(source['story'], source['story_quote_choices'])
            pool = lead.story_quote_choices(actual)
            response = json.loads(raw)
            if task_type == 'lead_candidates':
                for row in response['leads']:
                    row.pop('payoff_quote')
                    row['payoff_quote_id'] = pool[-1]['id']
            else:
                for row in response.get('comparisons', [response]):
                    for check in row['checks']:
                        check.pop('chapter_quote')
                        check.pop('payoff_quote')
                        check.update(chapter_quote_id=pool[0]['id'], payoff_quote_id=pool[-1]['id'])
                    for fact in row['fact_audit']:
                        fact.pop('story_quotes')
                        fact['story_quote_ids'] = [pool[-1]['id']]
            return json.dumps(response, ensure_ascii=False)
        self.llm.generate = generate
        self.flow.develop()
        report = self.flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'passed')
        _, body = self.project.active_draft()
        lead.validate_selection(body, self.project.contract(), report['lead_review']['selection'])
        pointer_prompts = [p for task, p in self.llm.prompts
                           if task in ('lead_candidates', 'lead_comparative_review', 'manuscript_lead_review')]
        self.assertEqual(len(pointer_prompts), 3)
        for prompt in pointer_prompts:
            self.assertLessEqual(len(prompt), 35000)
            self.assertNotIn('"story_quote_pool":', prompt)
            self.assertIn('"story_quote_choices":', prompt)
        prompt = next(p for task, p in self.llm.prompts if task == 'lead_comparative_review')
        selection = report['lead_review']['selection']
        repaired = lead.repair_review_prompt(prompt, selection['comparison'], '引用ID错误')
        payload = json.JSONDecoder().raw_decode(repaired[repaired.index('{"lead_review_input":'):])[0]
        original_payload = json.JSONDecoder().raw_decode(prompt[prompt.index('{"lead_review_input":'):])[0]
        self.assertEqual(payload['lead_review_input'], original_payload['lead_review_input'])
        self.assertEqual(payload['criteria'], lead.CHECKS)
        self.assertEqual(payload['lead_research']['worked_examples'], original_payload['lead_research']['worked_examples'][:2])
        self.assertIn('不为了通过格式而改判pass', repaired)
        self.assertLessEqual(len(repaired), 35000)

    def test_review_input_checks_assertions_without_counting_layout_lines(self):
        text = '我死了。\n\n她笑了。\n她那封退婚信却写着明天来接我。\n我照约前去，她却先问了我的名字。'
        choices = [{'id': f'l{i}', 'text': text + f'第{i}个模拟候选。'} for i in range(3)]
        criteria = lead.criteria_hash()
        generation = lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'})
        prompt = lead.review_prompt(self.story, self.project.contract(), choices=choices)
        data = json.JSONDecoder().raw_decode(prompt, prompt.index('{'))[0]
        self.assertEqual(len(data['lead_research']['worked_examples']), 4)
        self.assertGreaterEqual(len({e['id'] for e in data['lead_research']['worked_examples']}), 2)
        for actual, expected in zip(data['lead_review_input']['leads'], choices):
            self.assertEqual(actual, expected)
            self.assertNotIn('fact_audit_count', actual)
        final = lead.review_prompt(self.story, self.project.contract(), text=text)
        final_data = json.JSONDecoder().raw_decode(final, final.index('{'))[0]['lead_review_input']
        self.assertEqual(final_data['lead'], text)
        self.assertNotIn('fact_audit_count', final_data)
        self.assertEqual(generation, lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'}))
        self.assertEqual(criteria, lead.criteria_hash())
        # One selected assertion can span layout breaks. Reflowing it does not
        # manufacture more proof obligations or change a supplied judgment.
        for value in (text, text.replace('\n', '')):
            result = assessed(value, self.story)
            result['fact_audit'] = [{'lead_quote': '我死了。', 'story_quotes': [self.story[-24:]],
                                    'verdict': 'revise', 'reason': '正文并未发生死亡。'}]
            result['checks'][-1].update(verdict='revise', revision='按正文修正该断言')
            lead.validate_checks(result, value, self.story)
            self.assertEqual(len(result['fact_audit']), 1)
            self.assertEqual(result['checks'][-1]['verdict'], 'revise')

    def test_all_rejected_repair_pointer_is_strict_and_retry_keeps_revise_judgments(self):
        prompt = lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'})
        choices = json.loads(self.llm.generate(prompt, 'lead_candidates'))['leads']
        response = {'comparisons': [{'lead_id': c['id'], 'rank': i + 1,
                                     **assessed(c['text'], self.story, 'revise')}
                                    for i, c in enumerate(choices)],
                    'recommended_id': None, 'diversity': diversity(choices, 'revise'),
                    'repair': {'scope': 'lead_only', 'reason': '按实际正文重写首句',
                               'action': '露出实际冲突内容',
                               'story_quote_id': lead.story_quote_choices(self.story)[-1]['id']}}
        self.assertIsNone(lead.validate_comparison(response, choices, self.story))
        self.assertEqual(response['repair']['story_quote'], lead.story_quote_choices(self.story)[-1]['text'])
        for pointer, quote in (('q999999', None), (response['repair']['story_quote_id'], '这个引文被模型改写了。')):
            bad = copy.deepcopy(response)
            bad['repair']['story_quote_id'] = pointer
            if quote is not None:
                bad['repair']['story_quote'] = quote
            with self.subTest(pointer=pointer), self.assertRaisesRegex(StoryError, '引文ID|原文不一致'):
                lead.validate_comparison(bad, choices, self.story)
        short = copy.deepcopy(response)
        short['repair'].pop('story_quote_id')
        short['repair']['story_quote'] = self.story[:3]
        with self.assertRaisesRegex(StoryError, '全拒绝'):
            lead.validate_comparison(short, choices, self.story)
        original = copy.deepcopy(response)
        revised = lead.repair_candidates_prompt(prompt, choices, response)
        payload, end = json.JSONDecoder().raw_decode(revised, revised.index('{'))
        original_payload = json.JSONDecoder().raw_decode(prompt, prompt.index('{'))[0]
        self.assertEqual(payload['contract'], original_payload['contract'])
        self.assertEqual(payload['story'], original_payload['story'])
        self.assertEqual(payload['story_quote_choices'], original_payload['story_quote_choices'])
        feedback = json.JSONDecoder().raw_decode(revised, revised.index('{', end))[0]
        self.assertEqual(feedback['prior_leads'], [{'id': c['id'], 'text': c['text']} for c in choices])
        for saved, row in zip(feedback['revise_feedback'], response['comparisons']):
            self.assertEqual(saved['checks'], [{k: c[k] for k in ('id', 'verdict', 'reason', 'revision')}
                                               for c in row['checks']])
            self.assertTrue(all(f['verdict'] == 'revise' for f in saved['fact_audit']))
        self.assertEqual(response, original)
        self.assertLessEqual(len(revised) + 27, 35000)
        self.assertNotEqual(lead.candidate_prompt_hash(prompt), lead.candidate_prompt_hash(revised))

    def test_format_repair_quote_positions_are_lossless_and_invalid_text_stays_literal(self):
        choices = json.loads(self.llm.generate(
            lead.candidates_prompt(self.story, self.project.contract(), {'text': '找信'}), 'lead_candidates'))['leads']
        response = {'comparisons': [{'lead_id': c['id'], 'rank': i + 1,
                                     **assessed(c['text'], self.story)} for i, c in enumerate(choices)],
                    'recommended_id': choices[0]['id'], 'diversity': diversity(choices)}
        first = lead.story_quote_choices(self.story)[0]
        response['comparisons'][0]['checks'][0]['chapter_quote'] = '这条引文并没有出现在实际正文中。'
        response['comparisons'][0]['checks'][1].update(chapter_quote_id='q999999', chapter_quote=first['text'])
        response['comparisons'][0]['fact_audit'][0].update(story_quote_ids=['q999999'], story_quotes=[first['text']])
        original = copy.deepcopy(response)
        prompt = lead.review_prompt(self.story, self.project.contract(), choices=choices)
        repaired = lead.repair_review_prompt(prompt, response, '引文字段无效')
        payload, end = json.JSONDecoder().raw_decode(repaired, repaired.index('{'))
        compact = json.JSONDecoder().raw_decode(repaired, repaired.index('{', end))[0]
        self.assertEqual(compact['comparisons'][0]['checks'][0]['chapter_quote'], response['comparisons'][0]['checks'][0]['chapter_quote'])
        self.assertEqual(compact['comparisons'][0]['checks'][1]['chapter_quote'], first['text'])
        self.assertEqual(compact['comparisons'][0]['fact_audit'][0]['story_quotes'], [first['text']])
        pool = {q['id']: q['text'] for q in lead.story_quote_choices(self.story)}
        sources = {('story', None): self.story,
                   **{('lead', c['id']): c['text'] for c in choices},
                   **{('example', e['id']): e['excerpt'] for e in payload['lead_research']['worked_examples']}}
        def restore(value):
            if isinstance(value, dict):
                return {k: restore(v) for k, v in value.items()}
            if isinstance(value, list):
                if value and value[0] == 'quote':
                    if len(value) == 3:
                        return pool[value[2]]
                    source_id = value[2] if len(value) == 5 else None
                    return sources[(value[1], source_id)][value[-2]:value[-1]]
                return [restore(v) for v in value]
            return value
        self.assertEqual(restore(compact), original)
        self.assertEqual(response, original)
        self.assertLess(len(json.dumps(compact, ensure_ascii=False)), len(json.dumps(response, ensure_ascii=False)))
        self.assertLessEqual(len(repaired) + 27, 35000)
        # Lossless quote compression does not erase an arbitrarily long review;
        # the existing default transport budget still blocks it before a call.
        huge = copy.deepcopy(response)
        huge['comparisons'][0]['checks'][0]['reason'] = '保留完整判断。' * 5000
        over_budget = lead.repair_review_prompt(prompt, huge, '引文字段无效')
        before = len(self.llm.tasks)
        with self.assertRaisesRegex(StoryError, '安全上下文预算'):
            self.flow.call_json('lead_comparative_review', over_budget)
        self.assertEqual(len(self.llm.tasks), before)

    def test_short_and_quoted_prefix_windows_are_exact(self):
        self.assertEqual(lead.opening_windows('我死了。\n她笑了。\n他没哭。后来才哭。'),
                         {'first_sentence': '我死了。', 'first_three_sentences': '我死了。\n她笑了。\n他没哭。'})
        self.assertEqual(lead.opening_windows('“你敢吗？”\n我敢。\n她反悔了。')['first_sentence'], '“你敢吗？”')
        self.assertEqual(lead.opening_windows('穿越了。')['first_three_sentences'], '穿越了。')
        self.assertEqual(lead.opening_windows('第一句.第二句.第三句.第四句.'),
                         {'first_sentence': '第一句.', 'first_three_sentences': '第一句.第二句.第三句.'})
        self.assertEqual(lead.opening_windows('售价18.8元。她不卖了……我愣住。')['first_sentence'], '售价18.8元。')

    def test_avoid_keywords_do_not_retrieve_excluded_routes(self):
        data = {'route_profiles': [{'id': 'comic', 'keywords': ['差评']},
                                  {'id': 'dark', 'keywords': ['追悔', '死亡', '背叛']}],
                'selection_policy': {}, 'worked_examples': []}
        routes, _ = lead.selected_references(data, {'title': '差评',
            'constraints': {'avoid': ['追悔', '死亡', '背叛']}})
        self.assertEqual([r['id'] for r in routes], ['comic'])
        routes, _ = lead.selected_references(data, {'premise': '不要写死亡背叛追悔，写差评互怼',
            'preferences': {'avoid': ['追悔', '死亡', '背叛']}})
        self.assertEqual([r['id'] for r in routes], ['comic'])
        self.assertIn('我不离婚', lead.reference_surface({'premise': '我不离婚'}, '她说不要离婚。'))

    def test_prefix_cannot_borrow_later_quote_or_pass_over_revise(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        story = '她在正文写了退婚信，第二天前来接走叙述者。'
        good = assessed(text, story)
        lead.validate_checks(good, text, story)
        bad = copy.deepcopy(good)
        bad['prefix_review']['first_sentence']['quote'] = text[-12:]
        with self.assertRaisesRegex(StoryError, '准确前缀'):
            lead.validate_checks(bad, text, story)
        bad = copy.deepcopy(good)
        bad['prefix_review']['first_sentence']['verdict'] = 'revise'
        with self.assertRaisesRegex(StoryError, '吸引力存在短板'):
            lead.validate_checks(bad, text, story)
        bad['checks'][0]['verdict'] = 'revise'
        bad['checks'][0]['revision'] = '重写首句'
        lead.validate_checks(bad, text, story)

    def test_pass_null_revision_is_only_representation_not_added_judgment(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        story = '她在正文写了退婚信，第二天前来接走叙述者。'
        result = assessed(text, story)
        result['checks'][0]['revision'] = None
        lead.validate_checks(result, text, story)
        self.assertEqual(result['checks'][0]['revision'], '')
        result['checks'][0].update(verdict='revise', revision=None)
        with self.assertRaisesRegex(StoryError, '修订动作'):
            lead.validate_checks(result, text, story)

    def test_malformed_review_can_enter_schema_repair_without_crashing(self):
        text = '我死了。她笑了。她那封退婚信却写着明天来接我。'
        prompt = lead.review_prompt(self.story, self.project.contract(), text=text)
        for result in ({'comparisons': None}, {'comparisons': ['oops']},
                       {'comparisons': [None]}, {'benchmark': None}):
            repaired = lead.repair_review_prompt(prompt, result, '字段结构错误')
            self.assertIn('待纠错的上次响应', repaired)
            self.assertIn(self.story[:20], repaired)
            self.assertIn('不为了通过格式而改判pass', repaired)

    def test_upstream_repair_preserves_body_and_stops_lead_only_retry(self):
        original = self.llm.generate
        self.llm.reject_comparison = True
        def generate(prompt, task_type):
            raw = original(prompt, task_type)
            if task_type == 'lead_comparative_review':
                data = json.loads(raw)
                data['repair']['scope'] = 'story_scene'
                return json.dumps(data, ensure_ascii=False)
            return raw
        self.llm.generate = generate
        with self.assertRaisesRegex(StoryError, '正文需要上游回修'):
            self.flow._compare_leads(self.story, {'text': '找信'})
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 1)
        record = read_json(next((self.project.root / 'lead_candidates').glob('*/repair-required.json')))
        self.assertEqual(record['status'], 'requires_upstream_repair')
        saved = next((self.project.root / 'lead_candidates').glob('*/prelead-story.txt')).read_text()
        self.assertEqual(saved.strip(), self.story.strip())

    def test_counterexamples_cannot_be_used_as_passing_benchmarks(self):
        text = '导语证据需要足够具体的原文，不因为来源热门就直接放行。'
        story = '正文证据需要对应导语的实际行动，测试并不代表真实读者效果。'
        response = assessed(text, story)
        weak = next(e for e in lead.library()['worked_examples']
                    if not e.get('benchmark_eligible', True))
        response['benchmark'][0].update(example_id=weak['id'], reference_quote=weak['excerpt'])
        with self.assertRaisesRegex(StoryError, '对照的原例'):
            lead.validate_checks(response, text, story)

    def test_case_selection_prefers_matching_conflict_within_route(self):
        data = {'route_profiles': [{'id': 'family', 'keywords': ['父母', '赡养', '退休']}],
                'selection_policy': {'max_examples': 2},
                'worked_examples': [
                    {'id': 'generic', 'route': 'family', 'title': '父母偏心', 'excerpt': '', 'mechanism': ''},
                    {'id': 'specific', 'route': 'family', 'title': '退休后赡养父母', 'excerpt': '', 'mechanism': ''},
                    {'id': 'weak', 'route': 'family', 'title': '退休后赡养父母', 'benchmark_eligible': False}]}
        _, examples = lead.selected_references(data, {'premise': '退休后赡养父母'})
        self.assertEqual([e['id'] for e in examples], ['specific', 'generic'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / 'story')
        self.brief = StoryBrief.from_dict({'format': 'short_story', 'opening_mode': 'lead_then_chapter',
            'length': {'target': 3500, 'min': 2500, 'max': 4500},
            'author_input': {'premise': '林禾找回信，付出记忆代价'}})
        self.project.create(self.brief)
        self.story = '林禾发现信不见了。她拿着收据推开记忆店的门。\n' + ('她询问店主，核对收据，决定是否拿记忆换回信。\n' * 125) + '林禾取回那封信，失去与母亲共度的一段记忆。'
        self.llm = LeadLLM(self.story)
        self.flow = StoryWorkflow(self.project, self.llm, max_calls=100)

    def _use_deep_review(self):
        """Explicit opt-in only for tests of the retained comparison pipeline."""
        self.brief.review_mode = 'deep_review'
        self.project.brief_path.write_text(yaml.safe_dump(self.brief.to_dict(), allow_unicode=True), encoding='utf-8')

    def test_fanqie_default_and_author_override(self):
        raw = {'format': 'short_story', 'channels': ['fanqie_short'], 'author_input': {'premise': '例子'}}
        contract = StoryBrief.from_dict(raw).effective_contract()
        self.assertEqual(contract['opening_mode'], 'lead_then_chapter')
        self.assertFalse(lead.enabled(StoryBrief.from_dict({**raw, 'opening_mode': 'direct_scene'}).effective_contract()))
        self.assertNotIn('opening_mode', StoryBrief.from_dict({**raw, 'channels': ['generic_text']}).effective_contract())
        with self.assertRaises(StoryError):
            StoryBrief.from_dict({**raw, 'opening_mode': 'unknown'})

    def test_native_generate_review_and_txt_export_preserve_lead(self):
        self.flow.develop()
        report = self.flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'passed')
        _, body = self.project.active_draft()
        text, story = lead.split(body)
        self.assertIn('林禾发现信不见了', story)
        self.assertEqual(report['review_mode'], 'chief_editor')
        self.assertEqual(report['content_hash'], digest(body))
        package = StoryExporter(self.project, self.flow.call_json).export('generic_text')
        self.assertEqual((package / 'manuscript.txt').read_text(), body)
        self.assertEqual((package / 'lead.txt').read_text(), text + '\n')
        self.assertTrue(body.startswith(text))
        self.assertEqual(self.llm.tasks.count('lead_draft'), 1)
        draft_prompt = next(prompt for task, prompt in self.llm.prompts if task == 'lead_draft')
        draft_input = json.JSONDecoder().raw_decode(draft_prompt[draft_prompt.index('{"contract":'):])[0]
        self.assertEqual(draft_input['lead_research']['checks'], lead.CHECKS)
        self.assertEqual(len(draft_input['lead_research']['sentence_examples']), 2)
        self.assertIn('首句与前三句分别审查', draft_prompt)
        self.assertIn('不能逐字重播导语', draft_prompt)
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 0)
        self.assertEqual(self.llm.tasks.count('lead_comparative_review'), 0)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 0)
        self.assertEqual(self.llm.tasks.count('chief_editor_review'), 1)

    def test_scene_path_attaches_one_lead_after_actual_story(self):
        # Checkpointed scene path uses the same integration; no hand-built final body.
        self.flow.whole_draft_limit = 0
        self.flow.develop()
        report = self.flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'passed')
        _, body = self.project.active_draft()
        text, story = lead.split(body)
        self.assertIn('第1场', story)
        self.assertNotIn('第1场', text)
        self.assertEqual(body.count('\n第一章\n'), 1)
        self.assertEqual(self.llm.tasks.count('lead_draft'), 1)
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 0)
        self.assertEqual(self.llm.tasks.count('scene_progression_review'), 0)
        self.assertTrue(all(index < self.llm.tasks.index('lead_draft')
                            for index, task in enumerate(self.llm.tasks) if task == 'write_story_scene'))
        chief_prompt = next(prompt for task, prompt in self.llm.prompts if task == 'chief_editor_review')
        self.assertIn('第1场', chief_prompt)
        self.assertIn(text, chief_prompt)

    def test_all_rejected_leads_are_revised_then_fail_closed(self):
        self.llm.reject_comparison = True
        with self.assertRaisesRegex(StoryError, '三个导语均'):
            self.flow._compare_leads(self.story, {'text': '找信'})
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 2)
        self.assertEqual(self.llm.tasks.count('lead_comparative_review'), 2)
        self.assertFalse(list((self.project.root / 'lead_candidates').glob('*/result.json')))

    def test_cache_bound_to_body_and_tampering_is_rejected(self):
        body = self.flow._compare_leads(self.story, {'text': '找信'})
        before = self.flow.calls
        self.assertEqual(body, self.flow._compare_leads(self.story, {'text': '找信'}))
        self.assertEqual(before, self.flow.calls)
        self.flow._compare_leads(self.story.replace('母亲', '父亲'), {'text': '找信'})
        self.assertGreater(self.flow.calls, before)
        path = next((self.project.root / 'lead_candidates').glob('*/result.json'))
        cache = read_json(path)
        cache['candidates']['leads'][0]['text'] += '被改过。'
        write_json(path, cache)
        story = self.story if cache['story_hash'] == digest(self.story + '\n') else self.story.replace('母亲', '父亲')
        with self.assertRaisesRegex(StoryError, '缓存输入绑定损坏'):
            self.flow._compare_leads(story, {'text': '找信'})

    def test_high_full_story_scores_do_not_override_failed_lead_review(self):
        self._use_deep_review()
        self.flow.develop()
        self.llm.reject_final = True
        report = self.flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'revise_required')
        self.assertIn('lead:handoff', report['blockers'])
        with self.assertRaises(StoryError):
            StoryExporter(self.project, self.flow.call_json).export('generic_text')

    def test_deleted_lead_cannot_pass_or_export(self):
        self.flow.develop()
        self.flow.generate(auto_revise=False)
        record, body = self.project.active_draft()
        _, story = lead.split(body)
        self.project.save_draft(story, artifact_id=record['artifact_id'], parent_revision=record['revision'])
        report = self.flow.reviewer.review()
        self.assertIn('chief_editor:lead_structure', report['blockers'])
        with self.assertRaises(StoryError):
            StoryExporter(self.project, self.flow.call_json).export('generic_text')

    def test_fabricated_or_wrong_section_quotes_rejected(self):
        body = self.flow._compare_leads(self.story, {'text': '找信'})
        text, story = lead.split(body)
        good = assessed(text, story)
        lead.validate_checks(good, text, story)
        for key in ('lead_quote', 'chapter_quote', 'payoff_quote'):
            bad = copy.deepcopy(good)
            bad['checks'][-1][key] = '正文并没有出现这个承诺。'
            with self.assertRaises(StoryError):
                lead.validate_checks(bad, text, story)
        bound = {**good, 'protocol': lead.PROTOCOL, 'content_hash': digest(body), 'contract_hash': digest(self.project.contract()),
                 'selection': read_json(self.project.root / 'lead_selections' / f'{digest(body)}.json')}
        self.assertEqual(lead.gate(body, self.project.contract(), bound), [])
        with self.assertRaisesRegex(StoryError, '版本不符'):
            lead.gate(body + '新增后果', self.project.contract(), bound)

    def test_generic_or_predictable_appeal_cannot_hide_behind_pass_checks(self):
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        text, story = lead.split(body)
        for key in ('voice', 'specificity', 'question_strength'):
            bad = assessed(text, story)
            bad['appeal'][key]['verdict'] = 'revise'
            with self.assertRaisesRegex(StoryError, '吸引力存在短板'):
                lead.validate_checks(bad, text, story)
        bad = assessed(text, story)
        bad['fact_audit'][0]['verdict'] = 'revise'
        with self.assertRaisesRegex(StoryError, '事实状态存在矛盾'):
            lead.validate_checks(bad, text, story)

    def test_same_mechanism_set_is_not_selected_even_with_all_pass_checks(self):
        body = self.flow._compare_leads(self.story, {'text': '找信'})
        record = read_json(self.project.root / 'lead_selections' / f'{digest(body)}.json')
        comparison = copy.deepcopy(record['comparison'])
        comparison['diversity']['verdict'] = 'revise'
        comparison['recommended_id'] = None
        self.assertIsNone(lead.validate_comparison(comparison, record['candidates']['leads'], lead.clean_body(lead.split(body)[1])))
        comparison['recommended_id'] = 'l0'
        with self.assertRaises(StoryError):
            lead.validate_comparison(comparison, record['candidates']['leads'], lead.clean_body(lead.split(body)[1]))

    def test_default_manual_edit_is_reviewed_without_automatic_lead_reselection(self):
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        edited = body.replace('母亲', '父亲')
        self.project.save_draft(edited)
        before = list(self.llm.tasks)
        self.assertEqual(self.flow._ensure_lead_selection(edited), edited)
        self.assertEqual(self.llm.tasks, before)
        report = self.flow.reviewer.review()
        self.assertEqual(report['content_hash'], digest(edited))
        self.assertEqual(report['review_mode'], 'chief_editor')
        self.assertEqual(self.llm.tasks.count('lead_draft'), 1)
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 0)

    def test_default_author_approved_lead_survives_story_change_without_new_candidates(self):
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        receipt = lead.approve_lead(body, self.project.contract())
        write_json(self.project.root / 'approved_lead.json', receipt)
        text, story = lead.split(body)
        edited = lead.assemble(text, story.replace('母亲', '父亲'))
        before = list(self.llm.tasks)
        self.assertEqual(self.flow._ensure_lead_selection(edited), edited)
        self.assertEqual(self.llm.tasks, before)
        self.assertTrue(lead.validate_approved_lead(edited, self.project.contract(), receipt)['needs_fact_review'])
        with self.assertRaisesRegex(StoryError, '确认原文不一致'):
            self.flow._ensure_lead_selection(lead.assemble(text + '另一结尾。', story))

    def test_explicit_deep_manual_edit_requires_fresh_comparison(self):
        self._use_deep_review()
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        old = read_json(self.project.root / 'lead_selections' / f'{digest(body)}.json')
        for edited in (body.replace('第0份', '修改过的'), body.replace('母亲', '父亲')):
            with self.assertRaisesRegex(StoryError, '重新比较'):
                lead.validate_selection(edited, self.project.contract(), old)
            self.project.save_draft(edited)
            before = self.llm.tasks.count('lead_comparative_review')
            with self.assertRaisesRegex(StoryError, '重新比较'):
                self.flow.reviewer.review()
            selected = self.flow._ensure_lead_selection(edited)
            self.assertEqual(lead.split(selected)[1], lead.split(edited)[1])
            self.assertEqual(self.llm.tasks.count('lead_comparative_review'), before + 1)
            lead.validate_selection(selected, self.project.contract(),
                read_json(self.project.root / 'lead_selections' / f'{digest(selected)}.json'))

    def test_interrupted_comparison_reuses_exact_generated_candidates(self):
        original = self.llm.generate
        fail_once = [True]
        def interrupted(prompt, task_type):
            if task_type == 'lead_comparative_review' and fail_once[0]:
                fail_once[0] = False
                raise RuntimeError('Transport interrupted; preserve completed candidates')
            return original(prompt, task_type)
        self.llm.generate = interrupted
        with self.assertRaises(RuntimeError):
            self.flow._compare_leads(self.story, {'text': '找信'})
        body = self.flow._compare_leads(self.story, {'text': '找信'})
        self.assertTrue(lead.split(body)[0])
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 1)

    def test_pending_candidates_ignore_dict_order_but_invalidate_changed_text(self):
        from unittest.mock import patch
        original_generate = self.llm.generate
        original_prompt = lead.candidates_prompt

        def interrupted(prompt, task_type):
            if task_type == 'lead_comparative_review':
                raise RuntimeError('Comparison interrupted after candidates were saved')
            return original_generate(prompt, task_type)

        def reverse_dicts(value):
            if isinstance(value, dict):
                return {k: reverse_dicts(value[k]) for k in reversed(value)}
            if isinstance(value, list):
                return [reverse_dicts(v) for v in value]
            return value

        def reordered_prompt(*args, **kwargs):
            prompt = original_prompt(*args, **kwargs)
            start = prompt.index('{"contract":')
            payload, end = json.JSONDecoder().raw_decode(prompt, start)
            payload['contract'] = reverse_dicts(payload['contract'])
            return prompt[:start] + json.dumps(payload, ensure_ascii=False,
                                               separators=(',', ':')) + prompt[end:]

        self.llm.generate = interrupted
        with self.assertRaises(RuntimeError):
            self.flow._compare_leads(self.story, {'text': '找信'})
        pending_path = next((self.project.root / 'lead_candidates').glob('*/pending-candidates-1.json'))
        saved = read_json(pending_path)
        first = original_prompt(lead.clean_body(self.story), self.project.contract(), {'text': '找信'})
        second = reordered_prompt(lead.clean_body(self.story), self.project.contract(), {'text': '找信'})
        self.assertNotEqual(digest(first), digest(second))
        self.assertEqual(lead.candidate_prompt_hash(first), lead.candidate_prompt_hash(second))
        with patch.object(lead, 'candidates_prompt', reordered_prompt):
            fresh_flow = StoryWorkflow(self.project, self.llm, max_calls=100)
            with self.assertRaises(RuntimeError):
                fresh_flow._compare_leads(self.story, {'text': '找信'})
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 1)
        self.assertEqual(read_json(pending_path), saved)

        with patch.object(lead, 'candidates_prompt',
                          lambda *a, **kw: reordered_prompt(*a, **kw) + '\n改变首句写法要求。'):
            fresh_flow = StoryWorkflow(self.project, self.llm, max_calls=100)
            with self.assertRaises(RuntimeError):
                fresh_flow._compare_leads(self.story, {'text': '找信'})
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 2)
        self.assertNotEqual(read_json(pending_path)['prompt_hash'], saved['prompt_hash'])
        base = '指令\n{"text":"原文", "items":[1,2]}\n尾文'
        for changed in ('新指令\n{"text":"原文", "items":[1,2]}\n尾文',
                        '指令\n{"text":"改文", "items":[1,2]}\n尾文',
                        '指令\n{"text":"原文", "items":[2,1]}\n尾文'):
            self.assertNotEqual(lead.candidate_prompt_hash(base), lead.candidate_prompt_hash(changed))

    def test_changed_criteria_require_fresh_selection_even_for_identical_text(self):
        self._use_deep_review()
        from unittest.mock import patch
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        before = self.llm.tasks.count('lead_comparative_review')
        with patch.dict(lead.CHECKS, {'lead_pull': lead.CHECKS['lead_pull'] + '新审查版本'}):
            selected = self.flow._ensure_lead_selection(body)
            self.assertEqual(lead.split(selected)[1], lead.split(body)[1])
            self.assertEqual(self.llm.tasks.count('lead_comparative_review'), before + 1)

    def test_comparison_is_blind_to_generator_self_assessment(self):
        self.flow._compare_leads(self.story, {'text': '找信'})
        prompt = next(p for task, p in self.llm.prompts if task == 'lead_comparative_review')
        data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"lead_review_input":'):])[0]
        self.assertTrue(all(set(c) == {'id', 'text'}
                            for c in data['lead_review_input']['leads']))
        self.assertIn('worked_examples', data['lead_research'])
        self.assertNotIn('以下是有来源的编辑经验', prompt)

    def test_evidence_may_omit_layout_newlines_but_not_words_or_punctuation(self):
        original = '她将空白卡推到面前：\n“轮到你写真实台词了。”'
        self.assertTrue(lead._quote(original.replace('\n', ''), original))
        self.assertFalse(lead._quote('她将空白卡推到面前：“轮到你写告白台词了。”', original))
        self.assertFalse(lead._quote('她将空白卡推到面前，“轮到你写真实台词了。”', original))

    def test_later_payoff_evidence_is_allowed_but_entry_evidence_stays_local(self):
        text = '这是一段足够长的模拟导语。' * 8
        story = self.story + '这里是末尾具体兑现的独有证据。'
        response = assessed(text, story)
        response['checks'][-1]['chapter_quote'] = '这里是末尾具体兑现的独有证据。'
        lead.validate_checks(response, text, story)
        response['checks'][1]['chapter_quote'] = '这里是末尾具体兑现的独有证据。'
        with self.assertRaises(StoryError):
            lead.validate_checks(response, text, story)

    def test_new_editorial_feedback_invalidates_candidate_cache(self):
        self._use_deep_review()
        body = self.flow._attach_lead(self.story, {'text': '找信'})
        write_json(self.project.root / 'editorial_feedback.json', {'decision': 'reject', 'reason': '具体退稿意见'})
        self.project.save_draft(body)
        with self.assertRaisesRegex(StoryError, '退稿意见'):
            self.flow.reviewer.review()
        self.flow._ensure_lead_selection(body)
        self.assertEqual(self.llm.tasks.count('lead_candidates'), 2)

    def test_completed_review_repair_receives_prior_response_without_changing_judgment(self):
        original = self.llm.generate
        damaged = [False]
        def malformed_once(prompt, task):
            result = original(prompt, task)
            if task == 'lead_comparative_review' and not damaged[0]:
                damaged[0] = True
                data = json.loads(result)
                data['comparisons'][0]['fact_audit'][0]['story_quotes'] = ['这条引文实际未出现在正文。']
                return json.dumps(data, ensure_ascii=False)
            return result
        self.llm.generate = malformed_once
        self.flow._compare_leads(self.story, {'text': '找信'})
        reviews = [p for task, p in self.llm.prompts if task == 'lead_comparative_review']
        self.assertEqual(len(reviews), 2)
        self.assertIn('待纠错的上次响应', reviews[-1])
        self.assertIn('这条引文实际未出现在正文。', reviews[-1])
        self.assertIn('不为了通过格式而改判pass', reviews[-1])

    def test_research_reaches_opening_plan_write_review_and_package(self):
        for task in ('opening_candidates', 'story_plan', 'write_short_story', 'write_story_scene',
                     'lead_candidates', 'lead_comparative_review', 'manuscript_lead_review', 'channel_metadata'):
            ctx = model_research_context(research_context(self.project.contract(), task=task))
            self.assertTrue(ctx['manuscript_lead_research']['enabled'])
            self.assertIn('lead-enrichment-20261008',
                          {s['id'] for s in ctx['manuscript_lead_research']['sources']})
            self.assertTrue(ctx['manuscript_lead_research']['patterns'])

    def test_large_case_bank_uses_relevant_bounded_context(self):
        data = lead.library()
        self.assertGreaterEqual(len(data['worked_examples']), 72)
        for premise, wanted in (
                ('妻子提出离婚，丈夫止损退出，再不回头', 'stop_loss'),
                ('合租室友偷偷排练表白，我现场接招，轻喜搞笑', 'comic_exchange'),
                ('赡养大伯多年却被偏心父母排除，退休后的亲情', 'family_reframing'),
                ('我装穷，她隐藏身份，两人联姻时马甲暴露', 'double_identity')):
            contract = {**self.project.contract(), 'author_input': {'premise': premise}}
            ctx = lead.context(contract, 'lead')
            self.assertIn(wanted, {p['id'] for p in ctx['route_profiles']})
            self.assertLessEqual(len(ctx['worked_examples']), 8)
            self.assertGreaterEqual(sum(e.get('route') == wanted for e in ctx['worked_examples']), 2)
            self.assertTrue(all(e.get('benchmark_eligible', True) for e in ctx['worked_examples']))
        prompt = lead.candidates_prompt(self.story, self.project.contract(), {})
        self.assertIn('上游缺的具体场面', prompt)
        self.assertIn('能否平稳落地', prompt)


if __name__ == '__main__':
    unittest.main()
