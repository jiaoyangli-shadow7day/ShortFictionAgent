"""Real routing/grounding risks; scripted responses do not prove reader appeal."""
import copy
import json
import unittest

from short_fiction_agent.story_studio import chapter_reader as reader
from short_fiction_agent.story_studio.contracts import StoryError


def response_for(plan):
    source = next(row for row in plan['source_bank'] if row['scope'] == 'current')
    current_id = source['id']
    def note(text):
        return {'text': text, 'evidence_ids': [current_id]}
    def observation(ids):
        return {'status': 'effective', 'reason': '她主动回应了邀约，读者得到具体的双向选择。',
            'fix_intent': '保留这次主动回应，无须追加冲突。', 'repair_scope': 'none', 'evidence_ids': ids}
    return {'verdict': 'continue',
        'reader_expectation': note('她是否愿意一起走？'),
        'delivered_experience': note('她主动把手交给他，两人开始一起回家。'),
        'next_pull': note('已经收束，可以结束这一章。'),
        'windows': [dict(window_id=row['window_id'], **observation([row['paragraph_ids'][0]]))
                    for row in plan['windows']],
        'cumulative': observation([current_id]),
        'reading_value_check': observation([current_id]),
        'compression_trial': {'action': 'keep', 'compact_version': source['text'],
            'preserved_experience': '保留当前人物实际接招。', 'lost_experience': '本传输正控制原样保留，不声称语义最优。',
            'reason': '保留安静互动，不为压短强加戏。', 'evidence_ids': [current_id],
            'retained_beats': [{'action_quote': source['text'], 'response_quote': source['text'],
                                'evidence_ids': [current_id]}],
            'source_result_quote': source['text'], 'source_result_evidence_ids': [current_id]},
        'actual_handoff': {'facts': [note('两人一起回家。')],
            'answered_questions': [note('她接受了邀约。')], 'pending_questions': [],
            'relationship_changes': [note('她第一次主动牵住他。')]}}


class ChapterReaderTests(unittest.TestCase):
    def test_single_call_contains_exact_source_and_windows_without_future_or_author_plan(self):
        body = '\n\n'.join('她看着我，把手递过来。' * 35 for _ in range(5))
        contract = {'title': '一起回家', 'audience': {'description': '男频恋爱读者', 'secret': '秘密甲'},
            'author_input': {'premise': '秘密乙'}, 'reader_promise': '秘密丙',
            'planned_reader_promise': {'ending': '秘密丁'}, 'ending': '秘密戊',
            'genre': {'primary': '男生情感', 'secondary': ['重生'], 'ending': '秘密己'}, 'tone': '口语、轻松'}
        plan = reader.make_plan(body, contract, previous_text='她昨天没有回答。', chapter_id='ch2')
        payload = json.loads(plan['prompt'].rsplit('\n', 1)[1])
        self.assertEqual(payload['public_context'], {'enabled': False, 'title': '一起回家',
            'audience': '男频恋爱读者', 'audience_expectations': [],
            'genre': {'primary': '男生情感', 'secondary': ['重生']}, 'tone': '口语、轻松',
            'point_of_view': '', 'entry_facts': []})
        self.assertGreater(len(payload['observation_windows']), 1)
        self.assertEqual([row['text'] for row in payload['chapter_paragraphs']], body.split('\n\n'))
        self.assertEqual(payload['source_history'], [{'id': 'before:p0', 'text': '她昨天没有回答。'}])
        for secret in '甲乙丙丁戊己':
            self.assertNotIn('秘密' + secret, plan['prompt'])
        self.assertIn('不是每窗必须有钩子', plan['prompt'])
        self.assertIn('安静恋爱', plan['prompt'])
        self.assertIn('训练、核验、铺垫在任务上合理，不等于这段体验值得读', plan['prompt'])

    def test_window_coverage_and_source_positions_are_enforced(self):
        body = '\n\n'.join('我又讲了一遍自己的做法。' * 45 for _ in range(4))
        plan = reader.make_plan(body, {})
        response = response_for(plan)
        record = reader.validate_response(response, plan)
        self.assertEqual(record['verdict'], 'continue')
        for window in record['windows']:
            for evidence in window['evidence']:
                self.assertEqual(body[slice(*evidence['span'])], evidence['text'])
        missing = copy.deepcopy(response)
        missing['windows'].pop()
        with self.assertRaisesRegex(StoryError, '完整覆盖窗口'):
            reader.validate_response(missing, plan)
        wrong_window = copy.deepcopy(response)
        wrong_window['windows'][0]['evidence_ids'] = [plan['windows'][-1]['paragraph_ids'][0]]
        with self.assertRaisesRegex(StoryError, '当前窗口之外'):
            reader.validate_response(wrong_window, plan)
        future = copy.deepcopy(response)
        future['next_pull']['evidence_ids'] = ['future:p0']
        with self.assertRaisesRegex(StoryError, '未提供'):
            reader.validate_response(future, plan)

    def test_cumulative_structure_problem_cannot_be_promoted_to_local_repair(self):
        plan = reader.make_plan('她又说了一遍不要替她决定。\n我再次答应以后先问她。', {},
                                previous_text='她让我先问她的意见，我答应了。')
        response = response_for(plan)
        response['cumulative'].update(status='thin', repair_scope='structure',
            reason='此前已经解决的替她决定问题，本章再次按相同顺序重演。',
            fix_intent='合并重复教育场，改成双方实际选择带来新的相处后果。',
            evidence_ids=['before:p0', 'current:p0'])
        # A generous model verdict must not erase its grounded structural finding.
        record = reader.validate_response(response, plan)
        self.assertEqual(record['model_verdict'], 'continue')
        self.assertEqual(record['verdict'], 'replan')
        repair = reader.revision_instructions(record)
        self.assertEqual(repair['route'], 'replan')
        self.assertIn('不能只润色', repair['instruction'])
        self.assertEqual(repair['issues'][0]['repair_scope'], 'structure')
        response['cumulative']['repair_scope'] = 'expression'
        self.assertEqual(reader.validate_response(response, plan)['verdict'], 'revise')

    def test_quiet_ending_and_optional_financial_reminder_do_not_block(self):
        plan = reader.make_plan('她牵住我的手。\n我们回家，把剩下的零钱放进抽屉。', {}, is_final=True)
        response = response_for(plan)
        response['cumulative'].update(status='thin', repair_scope='expression',
            reason='零钱尾数没有逐项算出。', fix_intent='仅作初始校对提醒。',
            editorial_priority='copyedit_detail', reading_effect='回家和牵手已经清楚，尾数不影响体验。')
        record = reader.validate_response(response, plan)
        self.assertEqual(record['verdict'], 'continue')
        self.assertEqual(record['cumulative']['status'], 'thin')
        self.assertEqual(record['actual_handoff']['pending_questions'], [])
        self.assertEqual(reader.revision_instructions(record)['issues'], [])
        self.assertEqual(reader.actual_handoff(record)['relationship_changes'],
                         [{'text': '她第一次主动牵住他。'}])

    def test_actual_handoff_cannot_turn_a_previous_fact_into_current_change(self):
        plan = reader.make_plan('我把伞放好。', {}, previous_text='她答应了约会。')
        response = response_for(plan)
        response['actual_handoff']['relationship_changes'][0]['evidence_ids'] = ['before:p0']
        with self.assertRaisesRegex(StoryError, '当前窗口之外'):
            reader.validate_response(response, plan)

    def test_pending_expectation_can_survive_a_chapter_without_being_reinvented(self):
        plan = reader.make_plan('他回家等她的答复。', {}, previous_text='她说周五会告诉他答案。')
        response = response_for(plan)
        response['actual_handoff']['pending_questions'] = [
            {'text': '她周五会给什么答复？', 'evidence_ids': ['before:p0']}]
        record = reader.validate_response(response, plan)
        self.assertEqual(record['actual_handoff']['pending_questions'][0]['evidence'][0]['scope'], 'before')

    def test_negative_route_without_evidence_cannot_be_silently_downgraded(self):
        plan = reader.make_plan('她牵住我的手。', {})
        response = response_for(plan)
        response['verdict'] = 'replan'
        with self.assertRaisesRegex(StoryError, '缺少可定位'):
            reader.validate_response(response, plan)

    def test_prior_state_is_carried_without_old_prompts_or_unsupported_fields(self):
        first_plan = reader.make_plan('她牵住我的手。', {}, chapter_id='ch1')
        first = reader.validate_response(response_for(first_plan), first_plan)
        first['prompt'] = '秘密未来结局'
        first['future_plan'] = '秘密未来结局'
        next_plan = reader.make_plan('我们走进楼道。', {}, previous_text='她牵住我的手。',
                                    previous_reviews=[first], chapter_id='ch2')
        payload = json.loads(next_plan['prompt'].rsplit('\n', 1)[1])
        self.assertEqual(payload['previous_readings'][0]['actual_handoff']['relationship_changes'],
                         [{'text': '她第一次主动牵住他。'}])
        self.assertNotIn('秘密未来结局', next_plan['prompt'])
        first['reviewer_id'] = 'different-editor'
        with self.assertRaisesRegex(StoryError, '同一主审'):
            reader.make_plan('新章', {}, previous_reviews=[first])

    def test_old_handoff_is_bound_to_unchanged_full_prefix_and_chapter_order(self):
        lead, chapter = '她今晚会来。', '她牵住我的手。'
        plan = reader.make_plan(chapter, {}, previous_text=lead, chapter_id='ch1')
        first = reader.validate_response(response_for(plan), plan)
        # Assembly may add whitespace between chapters, but cannot change content.
        reader.make_plan('我们一起回家。', {}, previous_text=lead + '\n\n' + chapter + '\n',
                         previous_reviews=[first], chapter_id='ch2')
        changed_handoff = copy.deepcopy(first)
        changed_handoff['actual_handoff']['facts'][0]['text'] = '她拒绝了他。'
        with self.assertRaisesRegex(StoryError, '交接已变化'):
            reader.make_plan('新章', {}, previous_text=lead + '\n' + chapter,
                             previous_reviews=[changed_handoff])
        with self.assertRaisesRegex(StoryError, '完整前缀不匹配'):
            reader.make_plan('新章', {}, previous_text='她今晚不会来。\n' + chapter,
                             previous_reviews=[first])
        with self.assertRaisesRegex(StoryError, '未审读的新内容'):
            reader.make_plan('新章', {}, previous_text=lead + '\n' + chapter + '\n他跑开了。',
                             previous_reviews=[first])

    def test_changed_source_or_criteria_cannot_reuse_old_review_plan(self):
        plan = reader.make_plan('她牵住我的手。', {})
        response = response_for(plan)
        mutated = copy.deepcopy(plan)
        mutated['source_bank'][0]['text'] = '她拒绝我。'
        with self.assertRaisesRegex(StoryError, '来源已变化'):
            reader.validate_response(response, mutated)
        response['reader_expectation'].pop('evidence_ids')
        with self.assertRaises(StoryError):
            reader.validate_response(response, plan)

    def compact_response(self):
        body = ('我递过伞。我又向她解释了一遍为什么下雨要打伞。\n\n'
                '“走吧。”她接住。她说她当然知道下雨应该打伞。\n\n'
                '我们一起出门。我又看了一次天气预报，确认外面在下雨。')
        plan = reader.make_plan(body, {})
        response = response_for(plan)
        response['reading_value_check'].update(status='thin', repair_scope='expression',
            reason='递伞得到回应已经兑现相互靠近，反复确认雨天常识延后了出门。',
            fix_intent='保留递伞、接住和一起出门，合并常识解释与天气核验。',
            evidence_ids=['current:p0', 'current:p1', 'current:p2'])
        response['compression_trial'].update(action='compact',
            compact_version='我递过伞。\n“走吧。”她接住。\n我们一起出门。',
            preserved_experience='递伞被接受，两人实际一起走。', lost_experience='删去雨天常识复述，无独有关系体验损失。',
            reason='同一次交锋和结果仍在，重复确认并未使偏爱更具体。',
            evidence_ids=['current:p0', 'current:p1', 'current:p2'],
            retained_beats=[{'action_quote': '我递过伞。', 'response_quote': '“走吧。”她接住。',
                             'evidence_ids': ['current:p0', 'current:p1']}],
            source_result_quote='我们一起出门。', source_result_evidence_ids=['current:p2'])
        return plan, response

    def test_reading_value_can_require_repair_even_when_every_window_has_progress(self):
        plan, response = self.compact_response()
        record = reader.validate_response(response, plan)
        self.assertTrue(all(row['status'] == 'effective' for row in record['windows']))
        self.assertEqual(record['verdict'], 'revise')
        trial = record['compression_trial']
        self.assertEqual(plan['chapter_text'][slice(*trial['source_span'])], trial['source_quote'])
        self.assertEqual(reader.revision_instructions(record)['compression_trial'], trial)
        response['reading_value_check']['repair_scope'] = 'structure'
        response['compression_trial']['action'] = 'rebuild'
        self.assertEqual(reader.validate_response(response, plan)['verdict'], 'replan')

    def test_compact_cannot_be_an_empty_summary_or_invent_action_response_result(self):
        plan, response = self.compact_response()
        for key, field in (('retained_beats', 'action_quote'), ('retained_beats', 'response_quote'),
                           (None, 'source_result_quote')):
            forged = copy.deepcopy(response)
            row = forged['compression_trial']['retained_beats'][0] if key else forged['compression_trial']
            row[field] = '她忽然宣布明天结婚。'
            with self.subTest(field=field), self.assertRaisesRegex(StoryError, '不是所引当前正文的原句'):
                reader.validate_response(forged, plan)
        response['compression_trial']['compact_version'] = '两人一起回家。'
        with self.assertRaisesRegex(StoryError, '不能只写剧情摘要'):
            reader.validate_response(response, plan)

    def test_compression_repair_must_match_reading_value_and_actually_compact(self):
        plan, response = self.compact_response()
        mismatch = copy.deepcopy(response)
        mismatch['reading_value_check'].update(status='effective', repair_scope='none')
        with self.assertRaisesRegex(StoryError, '实际修订路线不一致'):
            reader.validate_response(mismatch, plan)
        response['compression_trial']['compact_version'] = plan['chapter_text']
        with self.assertRaisesRegex(StoryError, '没有实际缩短'):
            reader.validate_response(response, plan)

    def test_new_reading_value_and_trial_are_source_bound_and_hash_frozen(self):
        plan, response = self.compact_response()
        response['reading_value_check']['evidence_ids'] = ['before:p0']
        with self.assertRaises(StoryError):
            reader.validate_response(response, plan)
        response['reading_value_check']['evidence_ids'] = ['current:p0']
        record = reader.validate_response(response, plan)
        for key in ('reading_value_check', 'compression_trial'):
            tampered = copy.deepcopy(record)
            tampered[key]['reason'] = '替换为新的结论。'
            with self.subTest(field=key), self.assertRaisesRegex(StoryError, '交接已变化'):
                reader.make_plan('新章', {}, previous_text=plan['chapter_text'], previous_reviews=[tampered])

    def test_long_valid_evidence_and_empty_effective_fix_are_normalized_without_reroll(self):
        body = '\n\n'.join(f'第{i}次递伞，她都接住了。' for i in range(14))
        plan = reader.make_plan(body, {}, previous_text='昨天她没有接伞。')
        response = response_for(plan)
        ids = [row['id'] for row in plan['source_bank'] if row['scope'] == 'current']
        response['reading_value_check']['evidence_ids'] = ids[:8]
        response['compression_trial']['evidence_ids'] = ids
        response['cumulative']['fix_intent'] = ''
        response['reading_value_check']['fix_intent'] = '  '
        for window in response['windows']:
            window['fix_intent'] = ''
        record = reader.validate_response(response, plan)
        self.assertEqual(record['verdict'], 'continue')
        self.assertEqual(len(record['reading_value_check']['evidence_ids']), 8)
        self.assertEqual(len(record['compression_trial']['evidence_ids']), 14)
        self.assertEqual(record['cumulative']['fix_intent'], '保留')
        self.assertEqual(record['reading_value_check']['fix_intent'], '保留')
        self.assertTrue(all(row['fix_intent'] == '保留' for row in record['windows']))
        response['reading_value_check'].update(status='thin', repair_scope='expression', fix_intent='')
        with self.assertRaisesRegex(StoryError, '缺少具体fix_intent'):
            reader.validate_response(response, plan)

    def test_trial_span_unions_valid_child_evidence_but_rejects_history_or_unknown_ids(self):
        plan, response = self.compact_response()
        # A parent citation shortened during transport cannot invalidate actual
        # source-bound child quotes; their union defines the compared interval.
        response['compression_trial']['evidence_ids'] = ['current:p0']
        record = reader.validate_response(response, plan)
        self.assertEqual(record['compression_trial']['evidence_ids'],
                         ['current:p0', 'current:p1', 'current:p2'])
        self.assertEqual(record['compression_trial']['source_quote'], plan['chapter_text'])
        for invalid_id in ('before:p0', 'current:p999'):
            invalid = copy.deepcopy(response)
            invalid['compression_trial']['retained_beats'][0]['evidence_ids'] = [invalid_id]
            with self.subTest(evidence=invalid_id), self.assertRaises(StoryError):
                reader.validate_response(invalid, plan)


if __name__ == '__main__':
    unittest.main()
