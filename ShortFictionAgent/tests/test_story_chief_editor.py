"""Production orchestration and evidence tests; fake verdicts are not semantic evals."""
import copy
import hashlib
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief
from short_fiction_agent.story_studio import lead, research_guidance
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, canonical_json, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, current_review, review_decision
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from tests.narrative_fixtures import narrative_response


READY = {'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': []}
SOURCE = {'version': 'test-fixture', 'fingerprint': 'fixture',
          'common': {'rule': '测试来源卡：不为省略普通结算自动编造未付款。'}}


def payload(prompt):
    return json.loads(prompt.rsplit('\n', 1)[1])


class ChiefEditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / 'project')
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100, 'max': 30000},
            'author_input': {'premise': '陆昭24岁，穿越到1990年香港。允许贪钱和追求美色。',
                             'locked_facts': ['陆昭24岁']}}))
        self.calls = []
        self.addCleanup(patch.stopall)
        patch('short_fiction_agent.story_studio.quality.model_research_context', return_value=SOURCE).start()

    def reviewer(self, *, call=None, capacity=30000):
        def respond(task, prompt):
            self.calls.append((task, prompt))
            data = payload(prompt)
            response = copy.deepcopy(READY)
            if 'reading_turn' in data:
                first = data['paragraphs'][0]['id']
                response['facts'] = [{'statement': '测试桩：本段人物实际作出选择。', 'evidence_ids': [first]}]
            return response
        def with_narrative(task, prompt):
            response = (call or respond)(task, prompt)
            return response if task.endswith('_claim_reread') else narrative_response(prompt, response)
        return StoryReviewer(self.project, with_narrative, max_prompt_chars=capacity)

    def save(self, body):
        return self.project.save_draft(body)

    def test_short_manuscript_uses_same_editor_first_then_formal_read_without_old_specialist_tasks(self):
        body = '陆昭24岁，穿越到1990年香港。\n我贪钱，也想追她。\n她笑着接过花，答应明天见。'
        self.save(body)
        _, body = self.project.active_draft()
        report = self.reviewer().review()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual([task for task, _ in self.calls], ['chief_editor_first_read', 'chief_editor_review'])
        self.assertEqual(report['review_coverage']['spans'], [[0, len(body)]])
        self.assertEqual(current_review(self.project)['review_id'], report['review_id'])
        self.assertIn('贪钱', self.calls[0][1])
        self.assertIn('只有标签或界面堆叠', self.calls[1][1])

    def test_confirmed_lead_can_be_reviewed_without_three_candidate_receipt(self):
        brief = self.project.brief().to_dict()
        brief.update(format='short_story', opening_mode='lead_then_chapter',
                     length={'min': 1, 'target': 100, 'max': 30000})
        other = StoryProject(Path(self.tmp.name) / 'lead-project')
        other.create(StoryBrief.from_dict(brief))
        other.save_draft('我穿越到1990年香港，带着能查未来的未来记录。\n第一章\n陆昭24岁，亲自向她送花。\n她答应与我交往。')
        reviewer = StoryReviewer(other, lambda task, prompt: narrative_response(prompt, READY))
        self.assertEqual(reviewer.review()['decision'], 'passed')
        self.assertFalse((other.root / 'lead_selections').exists())

    def test_grounded_money_and_date_blockers_survive_adapter_and_style_does_not_block(self):
        body = '我的本金八百元。\n他承诺返还本金。\n结尾只到账收益四百元，却说本金已经回来。\n她说九月三日还有十天，九月十日却仍写还有十天。'
        self.save(body)
        response = {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [
            {'severity': 'blocker', 'evidence_ids': ['p0', 'p1', 'p2'], 'scope': 'global',
             'reason': '测试桩：正文把收益与本金混为一笔，没有返本事实。', 'fix_intent': '明确本金返还及其实际到账。'},
            {'severity': 'blocker', 'evidence_ids': ['p3'], 'scope': 'local',
             'reason': '测试桩：相隔七天，剩余日期未改变。', 'fix_intent': '统一后文相对日期。'},
            {'severity': 'suggestion', 'evidence_ids': ['p2'], 'scope': 'local',
             'reason': '测试桩：可以缩短余额解释。', 'fix_intent': '可选删去重复措辞。'}]}
        report = self.reviewer(call=lambda task, prompt: response).review()
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(len([i for i in report['issues'] if i['severity'] == 'blocker']), 2)
        self.assertEqual(report['issues'][0]['repair_scope'], 'replan')
        self.assertEqual(report['issues'][0]['scope'], 'global')
        for issue in report['issues']:
            self.assertEqual(body[slice(*issue['span'])], issue['quote'])
            for evidence in issue['evidence']:
                self.assertEqual(body[slice(*evidence['span'])], evidence['text'])
        response['recommendation'] = 'ready'
        response['issues'] = [response['issues'][2]]
        # A different project avoids replaying the completed, source-bound response.
        self.project.root = Path(self.tmp.name) / 'suggestion'
        self.project.create(StoryBrief.from_dict({'format': 'micro_story',
            'length': {'min': 1, 'target': 100, 'max': 30000}, 'author_input': {'premise': '保留作者欲望'}}))
        self.save(body)
        self.assertEqual(self.reviewer(call=lambda task, prompt: response).review()['decision'], 'passed')

    def test_invalid_evidence_does_not_dispatch_another_semantic_review(self):
        self.save('这笔钱还没有到账。')
        response = {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [
            {'severity': 'blocker', 'evidence_ids': ['p999'], 'scope': 'local',
             'reason': '测试无效引用', 'fix_intent': '测试修改'}]}
        calls = []
        reviewer = self.reviewer(call=lambda task, prompt: calls.append(task) or response)
        for _ in range(2):
            with self.assertRaisesRegex(StoryError, '实际阅读范围'):
                reviewer.review()
        self.assertEqual(calls, ['chief_editor_first_read', 'chief_editor_review'])
        self.assertTrue(list((self.project.root / 'review_resume').rglob('*.json')))

    def test_capacity_partitions_all_prose_and_one_synthesis_with_same_editor(self):
        body = '\n'.join(f'第{i}段：' + '她把花递回来，我换了种说法。' * 12 for i in range(40))
        self.save(body)
        _, body = self.project.active_draft()
        # Hold the complete current review rules plus a fixed prose allowance.
        # A historical 14k total became smaller than rules + mandatory anchors;
        # this test concerns complete partitioning, not a fixed model capacity.
        baseline, _ = chief.synthesis_prompt('起点。\n终点。', self.project.contract(),
            [{'issues': [], 'facts': []}], source_cards=chief.compact_source_cards(SOURCE))
        capacity = len(baseline) + 4000
        reviewer = self.reviewer(capacity=capacity)
        report = reviewer.review()
        chunks = report['chief_editor']['chunks']
        self.assertGreater(len(chunks), 1)
        self.assertEqual(len(self.calls), len(chunks) + 1 + len(report['chief_editor']['first_read_receipts']))
        self.assertEqual([task for task, _ in self.calls].count('chief_editor_synthesis'), 1)
        self.assertEqual(len({payload(prompt)['reviewer_id'] for _, prompt in self.calls}), 1)
        seen = [row['id'] for task, prompt in self.calls if task == 'chief_editor_review'
                for row in payload(prompt)['paragraphs']]
        self.assertEqual(seen, [row['id'] for row in chief.paragraphs(body)])
        cursor = 0
        for start, end in report['review_coverage']['spans']:
            self.assertEqual(start, cursor)
            cursor = end
        self.assertEqual(cursor, len(body))
        self.assertTrue(all(len(prompt) <= capacity for _, prompt in self.calls))
        synthesis = payload(self.calls[-1][1])
        self.assertEqual(len(synthesis['reading_anchors']), len(chunks))
        self.assertTrue(all(anchors for anchors in synthesis['reading_anchors']))

    def test_unknown_resume_reuses_completed_chunks_but_changed_body_does_not(self):
        body = '\n'.join(f'第{i}段：' + '她递来花，我认真答应了。' * 15 for i in range(40))
        record = self.save(body)
        baseline, _ = chief.synthesis_prompt('起点。\n终点。', self.project.contract(),
            [{'issues': [], 'facts': []}], source_cards=chief.compact_source_cards(SOURCE))
        capacity = len(baseline) + 4000
        completed = []
        interrupt = True

        def respond(task, prompt):
            nonlocal interrupt
            self.calls.append((task, prompt))
            data = payload(prompt)
            if task == 'chief_editor_review' and len(completed) == 1 and interrupt:
                interrupt = False
                raise RuntimeError('result unknown; no automatic resend')
            response = copy.deepcopy(READY)
            if 'reading_turn' in data:
                response['facts'] = [{'statement': '人物回应选择。',
                                      'evidence_ids': [data['paragraphs'][0]['id']]}]
                completed.append(data['reading_turn']['index'])
            return response

        with self.assertRaisesRegex(RuntimeError, 'unknown'):
            self.reviewer(call=respond, capacity=capacity).review()
        first_prompt = self.calls[0][1]
        report = self.reviewer(call=respond, capacity=capacity).review()
        self.assertGreater(len(report['chief_editor']['chunks']), 1)
        self.assertTrue(all(len(prompt) <= capacity for _, prompt in self.calls))
        self.assertEqual(sum(prompt == first_prompt for _, prompt in self.calls), 1)
        self.assertEqual(report['decision'], 'passed')
        count = len(self.calls)
        self.reviewer(call=respond, capacity=capacity).review()
        self.assertEqual(len(self.calls), count)  # Complete exact-version receipts also remain reusable.
        self.project.save_draft(body + '\n后来她又补充了一句话。', artifact_id=record['artifact_id'], parent_revision=1)
        self.reviewer(call=respond, capacity=capacity).review()
        self.assertGreater(len(self.calls), count)

    def test_tampered_coverage_response_and_current_version_cannot_pass(self):
        body = '陆昭24岁。\n她知道我已经付款。'
        record = self.save(body)
        report = self.reviewer().review()
        contract = self.project.contract()
        for mutation in ('coverage', 'evidence', 'receipt', 'source'):
            forged = copy.deepcopy(report)
            if mutation == 'coverage':
                forged['review_coverage']['spans'] = [[0, 4]]
            elif mutation == 'evidence':
                forged['issues'] = [{'id': 'invented'}]
            elif mutation == 'receipt':
                forged['chief_editor']['chunks'][0]['input_hash'] = 'different'
                forged['chief_editor']['record_hash'] = digest(
                    {k: v for k, v in forged['chief_editor'].items() if k != 'record_hash'})
            else:
                forged['chief_editor']['source_sha256'] = 'changed'
                forged['chief_editor']['record_hash'] = digest(
                    {k: v for k, v in forged['chief_editor'].items() if k != 'record_hash'})
            with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                review_decision(body, contract, forged)
        self.project.save_draft(body + '\n她还没拿到钱。', artifact_id=record['artifact_id'], parent_revision=1)
        with self.assertRaises(StoryError):
            current_review(self.project)

    def test_affected_review_verifies_mapping_and_honestly_falls_back_to_full_reading(self):
        before = '陆昭24岁。\n本金没有返还。'
        record = self.save(before)
        _, before = self.project.active_draft()
        reviewer = self.reviewer()
        original = reviewer.review()
        after = '陆昭24岁。\n本金已经返还。\n'
        changes = [{'quote': '本金没有返还。', 'replacement': '本金已经返还。', 'start': before.index('本金')}]
        self.project.save_draft(after, artifact_id=record['artifact_id'], parent_revision=1)
        report = reviewer.review_affected(original, changes, previous_body=before, new_body=after)
        self.assertEqual(report['review_coverage']['spans'], [[0, len(after)]])
        events = [json.loads(line) for line in (self.project.root / 'runs/events.jsonl').read_text().splitlines()]
        fallback = [e for e in events if e['event'] == 'chief_incremental_fallback'][-1]
        self.assertTrue(fallback['edit_mapping_verified'])
        self.assertEqual(len(self.calls), 4)

    def test_stable_json_cache_and_changed_feedback_invalidate_only_real_inputs(self):
        body = '她把花递给我，我答应了。'
        self.save(body)
        self.reviewer().review()
        reverse = dict(reversed(list(SOURCE.items())))
        with patch('short_fiction_agent.story_studio.quality.model_research_context', return_value=reverse):
            self.reviewer().review()
        self.assertEqual(len(self.calls), 2)
        write_json(self.project.root / 'editorial_feedback.json', {'decision': 'reject', 'reason': '需要核对导语'} )
        self.reviewer().review()
        self.assertEqual(len(self.calls), 4)

    def test_real_research_banks_reach_production_prompt_with_fair_complete_rules(self):
        # Exercise the actual libraries and Hong Kong problem-package routing,
        # not a synthetic parallel research chain or a live model judgment.
        project = StoryProject(Path(self.tmp.name) / 'real-research')
        project.create(StoryBrief.from_dict({
            'format': 'short_story', 'opening_mode': 'lead_then_chapter',
            'channels': ['fanqie_short'],
            'length': {'min': 1, 'target': 300, 'max': 30000},
            'genre': {'primary': 'romance', 'custom_description': '1990年香港，穿越与姐弟恋'},
            'audience': {'description': '男频情感读者'},
            'research_preferences': {'audience_track': 'male',
                'reader_tags': ['男生情感', '穿越', '年代', '娱乐圈', '姐弟恋', '爽文']},
            'reader_promise': '男主贪钱也追求美色，靠未来记录未来情报赚钱，女明星实际回应他的追求。',
            'author_input': {'premise': '陆昭24岁，从现代穿越到1990年香港，带着能离线查询未来的未来记录。',
                'locked_facts': ['陆昭24岁，从现代穿越到1990年香港', '未来记录随手机穿越离线运行']},
        }))
        contract = project.contract()
        bank_paths = sorted(Path(research_guidance.__file__).with_name('data').glob('*research.json'))
        hashes_before = {str(path): digest(path.read_text(encoding='utf-8')) for path in bank_paths}
        selected = research_guidance.model_research_context(
            research_guidance.research_context(contract, task='chief_editor_review'))
        self.assertTrue(selected['target_reader_research']['mechanisms'])
        original = digest(selected)
        body = ('陆昭24岁，从现代穿越到1990年香港。未来记录随手机穿越离线运行。\n'
                '她要我猜明天的结果，我先把查到的消息给她看。\n第一章\n'
                '我查过未来记录的原记录后买入，她亲自陪我去兑现，钱实际到账。\n'
                '她主动牵住我的手，答应做我的恋人。')
        project.save_draft(body)
        with patch('short_fiction_agent.story_studio.quality.model_research_context', return_value=selected):
            report = StoryReviewer(project, self.reviewer().call_json).review()
        data = next(payload(prompt) for task, prompt in self.calls
                    if task == 'chief_editor_review')
        cards = data['source_cards']
        expected_groups = {'manuscript_lead_research', 'prose_expression_research',
            'dramatic_realization_research', 'desire_payoff_research', 'whole_story_research',
            'topic_guidance', 'common', 'limits', 'continuous_pull', 'profiles',
            'screenshot_opening_research', 'target_reader_research'}
        actual_groups = {row['source_path'].split('.')[0].split('[')[0] for row in cards['cards']}
        self.assertEqual(actual_groups, expected_groups)
        self.assertLessEqual(len(canonical_json(cards)), 6000)
        paths = [row['source_path'] for row in cards['cards']]
        self.assertIn('manuscript_lead_research.current_policy', paths)
        self.assertIn('prose_expression_research.transfer_rule', paths)
        self.assertTrue(any(path.startswith('desire_payoff_research.') and path.endswith('.stage_rule')
                            for path in paths))
        self.assertTrue(any(path.startswith('dramatic_realization_research.rules[') for path in paths))
        self.assertTrue(any(path.startswith('whole_story_research.principles[') for path in paths))
        self.assertTrue(any(path.startswith('target_reader_research.mechanisms[') for path in paths))
        for row in cards['cards']:
            value = selected
            for key, index in re.findall(r'([^.[\]]+)|\[(\d+)\]', row['source_path']):
                value = value[int(index)] if index else value[key]
            self.assertEqual(row['value'], value)  # The complete original string.
        self.assertEqual(data['author_contract']['entry_facts'], lead.entry_facts(contract))
        self.assertEqual(report['chief_editor']['source_cards'], cards)
        self.assertEqual(digest(selected), original)
        self.assertEqual({str(path): digest(path.read_text(encoding='utf-8')) for path in bank_paths}, hashes_before)
        self.assertEqual(report['decision'], 'passed')

    def test_large_first_bank_cannot_starve_other_groups_or_clip_a_rule(self):
        source = {'manuscript_lead_research': {
                'current_policy': '导语交代必要前提，后文核对兑现。',
                'principles': ['完整但超过预算的原则。' * 200]},
            'prose_expression_research': {'transfer_rule': '人物实际动作与回应不能用解释替代。'},
            'dramatic_realization_research': {'rules': ['关键选择必须实际改变处境。']},
            'desire_payoff_research': {'mechanisms': [{'stage_rule': '欲望以行动和结果兑现。'}]},
            'whole_story_research': {'principles': ['结尾回应本篇核心承诺。']},
            'common': ['遵守作者锁定事实。']}
        cards = chief.compact_source_cards(source, budget=1300)
        self.assertEqual({row['source_path'].split('.')[0].split('[')[0] for row in cards['cards']}, set(source))
        self.assertLessEqual(len(canonical_json(cards)), 1300)
        self.assertNotIn('manuscript_lead_research.principles[0]', [row['source_path'] for row in cards['cards']])
        with self.assertRaisesRegex(StoryError, '各研究领域的完整规则'):
            chief.compact_source_cards(source, budget=300)

    def test_grounding_prompt_separates_author_premises_from_independent_lead_evidence(self):
        # Prompt/transport regression only. A model must judge whether a lead
        # expresses a premise; no keyword gate assigns the semantic verdict.
        contract = {'title': '来自另一时空的修表人',
                    'author_input': {'premise': '主角从未来来到旧城。',
                                     'entry_facts': ['主角从未来来到旧城。']},
                    'reader_promise': '本次材料仅为独立导语。'}
        intro = '雨夜的旧城，我举起会预警的怀表，拦住了正要上桥的她。'
        original = copy.deepcopy(contract)
        prompt = chief.prompt(intro, contract)
        data = payload(prompt)
        self.assertEqual(data['paragraphs'], [{'id': row['id'], 'text': row['text']}
                                              for row in chief.paragraphs(intro)])
        self.assertEqual(data['author_contract']['entry_facts'], ['主角从未来来到旧城。'])
        self.assertEqual(contract, original)
        for rule in ('只能依据paragraphs中的实际叙述',
                     '不能证明原文已呈现这些事实',
                     '先脱离标题、标签、合同和后续章节',
                     '不能自动推出主角来自现代或经历了跨时空迁移',
                     '导语整体须用自然叙述交代',
                     '可以等义表达，无须出现某个固定术语',
                     '不要求首句塞进所有设定'):
            self.assertIn(rule, prompt)
        full = intro + '\n第一章\n我从未来来到旧城，怀表也跟着我到了这里。'
        plan = chief.build_plan(full, contract)
        full_data = payload(plan['chunks'][0]['prompt'])
        self.assertEqual(full_data['paragraphs'][0]['text'], intro)
        self.assertEqual(full_data['paragraphs'][-1]['text'], full.splitlines()[-1])
        self.assertIn('不能拿合同中的穿越设定或正文后段补证', plan['chunks'][0]['prompt'])
        self.assertIn('每个元素只能是一个完整段落ID', prompt)

    def test_reader_transport_omits_only_spans_and_keeps_exact_complete_source(self):
        body = '\n  \n' + '\n\n'.join(f'  第{i}段：她问“还来吗？”我回答：还来。🙂\t' * 5
                                     for i in range(35)) + '\n\t\n'
        bank = chief.paragraphs(body)
        # Source transport needs a real full paragraph plus current instructions.
        capacity = len(chief._prompt(bank[:1], {}, chunk={'index': 9999, 'count': 9999})) + 500
        plan = chief.build_plan(body, {}, max_prompt_chars=capacity)
        self.assertGreater(len(plan['chunks']), 1)
        received = [row for chunk in plan['chunks'] for row in payload(chunk['prompt'])['paragraphs']]
        self.assertEqual(received, [{'id': row['id'], 'text': row['text']} for row in bank])
        self.assertEqual(plan['source_sha256'], hashlib.sha256(body.encode()).hexdigest())
        self.assertEqual(plan['paragraph_plan_hash'], digest(bank))
        cursor = 0
        for chunk in plan['chunks']:
            self.assertEqual(chunk['span'][0], cursor)
            cursor = chunk['span'][1]
            self.assertLessEqual(len(chunk['prompt']), capacity)
            self.assertEqual(chunk['paragraph_ids'], [row['id'] for row in payload(chunk['prompt'])['paragraphs']])
        self.assertEqual(cursor, len(body))
        issue = {'severity': 'blocker', 'evidence_ids': ['p0', 'p34'], 'scope': 'global',
                 'reason': '测试桩：保留两端完整原文及实际偏移。', 'fix_intent': '核对原文。'}
        checked = chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [issue]}, body)
        self.assertEqual(checked['issues'][0]['evidence'], [bank[0], bank[34]])
        for row in checked['issues'][0]['evidence']:
            self.assertEqual(body[slice(*row['span'])], row['text'])

    def test_generic_conditions_and_reader_expectation_rules_reach_reading_and_synthesis(self):
        body = '\n'.join('她先问清条件，收到答复之后才签字。' * 20 for _ in range(20))
        # Keep a deliberately small prose allowance after complete instructions;
        # the source must still partition, and no paragraph is shortened.
        capacity = len(chief.prompt('一段正文。', {})) + 1200
        plan = chief.build_plan(body, {}, max_prompt_chars=capacity)
        self.assertGreater(len(plan['chunks']), 1)
        self.assertTrue(all(len(chunk['prompt']) <= capacity for chunk in plan['chunks']))
        notes = [{'claims': [], 'observations': [], 'recommendation': 'uncertain', 'issues': [], 'facts': [{
            'statement': '先有条件与答复，再实际签字。', 'evidence_ids': ['p0']}]}]
        summary, _ = chief.synthesis_prompt(body, {}, notes, max_prompt_chars=30000)
        shared_rules = ('遵守作者指定的叙述人称、视角和表达要求',
                        '对照原断言的限定条件、前置动作与实际发生的结果',
                        '相对时间保留所依附的日期或场景时间',
                        '分别判断首句、紧随的前三句与整段带来的信息',
                        '人物接招可以是对白或行动',
                        '尚需看到的具体行动、对方回应或原因',
                        '不强制前三句反转、对白数量或新灾难')
        for prompt in [chunk['prompt'] for chunk in plan['chunks']] + [summary]:
            for rule in shared_rules:
                self.assertIn(rule, prompt)
        for chunk in plan['chunks']:
            self.assertIn('保留决定断言能否成立的限定与前置条件、对应实际结果', chunk['prompt'])
            self.assertIn('必要时引用时间标题和同一对象的前后原段', chunk['prompt'])
            self.assertIn('已发现的问题直接保留为issues', chunk['prompt'])
        # The production criteria contain no specimen's identity, race number,
        # price or date. Semantic judgments remain the editor's responsibility.
        for specimen_answer in ('陆昭', '旧版导语', 'old_lead', 'approved_lead', '613', '1990', '九号', 'expected_focus'):
            self.assertNotIn(specimen_answer, chief.INSTRUCTION)

    def test_comma_pair_id_normalization_keeps_six_actual_anchors_and_raw_response(self):
        body = '\n'.join(f'第{i}段实际发生的事情。' for i in range(6))
        raw = {'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': [{
            'statement': '六段均属于本次输入。', 'evidence_ids': ['p0, p1', 'p2', 'p3', 'p4', 'p5']}]}
        original = copy.deepcopy(raw)
        response_hash = digest(raw)
        normalized = chief.validate_response(raw, body, require_facts=True)
        self.assertEqual(normalized['facts'][0]['evidence_ids'], [f'p{i}' for i in range(6)])
        self.assertEqual(normalized['facts'][0]['evidence'], chief.paragraphs(body))
        self.assertEqual(normalized['format_normalizations'], [{
            'kind': 'split_comma_joined_paragraph_ids',
            'source_path': 'facts[0].evidence_ids[0]',
            'original': 'p0, p1', 'normalized': ['p0', 'p1']}])
        self.assertEqual(raw, original)
        self.assertEqual(digest(raw), response_hash)

    def test_unknown_duplicate_guessed_and_non_protocol_id_formats_still_fail(self):
        body = '\n'.join(f'第{i}段。' for i in range(7))
        invalid = [['p0, p999'], ['p0, p0'], ['p0, p1', 'p1'], ['p0,p1'],
                   ['p0; p1'], ['p0, p1, p2'], [' p0, p1']]
        for ids in invalid:
            with self.subTest(ids=ids), self.assertRaisesRegex(StoryError, '实际阅读范围'):
                chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': [
                    {'statement': '不能猜测或重选证据。', 'evidence_ids': ids}]}, body, require_facts=True)
        with self.assertRaisesRegex(StoryError, '实际阅读范围'):
            chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': [
                {'statement': '正文中存在但本次未送入的ID不能使用。', 'evidence_ids': ['p0, p1']}]},
                body, allowed_ids=['p0'], require_facts=True)

    def test_fact_anchors_can_cover_every_available_paragraph_without_issue_limit(self):
        body = '\n'.join(f'第{i}段保留一项必要的原文事实。' for i in range(18))
        allowed = [f'p{i}' for i in range(1, 18)]
        # Neither seven nor twelve is a new fact-ID cap: the reading boundary
        # sets the maximum, and every actual anchor survives unchanged.
        for count in (7, len(allowed)):
            ids = allowed[:count]
            raw = {'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': [{
                'statement': '条件、动作和结果需要共同对照。', 'evidence_ids': ids}]}
            original = copy.deepcopy(raw)
            checked = chief.validate_response(raw, body, allowed_ids=allowed, require_facts=True)
            self.assertEqual(checked['facts'][0]['evidence_ids'], ids)
            self.assertEqual(checked['facts'][0]['evidence'], chief.paragraphs(body)[1:count + 1])
            self.assertEqual(raw, original)
        issue = {'severity': 'blocker', 'evidence_ids': allowed[:7], 'scope': 'local',
                 'reason': '问题引用仍受已声明的六段上限约束。', 'fix_intent': '保留必要的问题证据。'}
        with self.assertRaisesRegex(StoryError, '实际阅读范围'):
            chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [issue]}, body, allowed_ids=allowed)
        for invalid in (allowed[:7] + ['p999'], allowed[:7] + ['p1'], ['p0'] + allowed[:7]):
            with self.subTest(invalid=invalid), self.assertRaisesRegex(StoryError, '实际阅读范围'):
                chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': [{
                    'statement': '未知、重复及本段之外的引用继续拒绝。', 'evidence_ids': invalid}]},
                    body, allowed_ids=allowed, require_facts=True)

    def test_normalization_is_recorded_once_without_model_retry_or_verdict_change(self):
        self.save('账上只有利润。\n本金没有返还。')
        raw = {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
            'severity': 'blocker', 'evidence_ids': ['p0, p1'], 'scope': 'local',
            'reason': '测试桩：原文没有本金返还。', 'fix_intent': '交代实际本金去向。'}]}
        original = copy.deepcopy(raw)
        def respond(task, prompt):
            self.calls.append((task, prompt))
            return copy.deepcopy(raw)
        reviewer = self.reviewer(call=respond)
        report = reviewer.review()
        repeated = reviewer.review()
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(repeated['decision'], 'revise_required')
        receipt = report['chief_editor']['chunks'][0]
        actual_raw = narrative_response(self.calls[1][1], original)
        self.assertEqual(raw, original)
        self.assertEqual(receipt['response'], actual_raw)
        self.assertEqual(receipt['response_hash'], digest(actual_raw))
        self.assertEqual(receipt['format_normalizations'][0]['source_path'], 'issues[0].evidence_ids[0]')
        self.assertEqual(report['chief_editor']['response']['issues'][0]['evidence_ids'], ['p0', 'p1'])
        _, body = self.project.active_draft()
        self.assertEqual(report['chief_editor']['response']['issues'][0]['evidence'], chief.paragraphs(body))
        events = [json.loads(line) for line in (self.project.root / 'runs/events.jsonl').read_text().splitlines()]
        notices = [event for event in events if event['event'] == 'chief_format_normalized']
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0]['response_hash'], digest(actual_raw))
        self.assertFalse(notices[0]['automatic_semantic_retry'])
        forged = copy.deepcopy(report)
        forged['chief_editor']['chunks'][0]['format_normalizations'][0]['source_path'] = 'issues[9].evidence_ids[0]'
        forged['chief_editor']['record_hash'] = digest({k: v for k, v in forged['chief_editor'].items() if k != 'record_hash'})
        with self.assertRaisesRegex(StoryError, '格式规范化记录'):
            review_decision(body, self.project.contract(), forged)

    def test_synthesis_omits_only_transport_spans_and_keeps_all_notes_and_source_text(self):
        body = '\n'.join(f'第{i}段：' + '她询问情况，对方回答后，她换了主意并当场行动。' * 2
                         for i in range(135))
        rows = chief.paragraphs(body)
        chunks = []
        for turn in range(4):
            facts = []
            for fact_index in range(9):
                indices = list(range(turn * 36 + fact_index * 4, min(turn * 36 + fact_index * 4 + 4, 135)))
                if indices:
                    facts.append({'statement': '这是原文中的完整事实，不删去其中的人物行动、实际回应和因果边界。' * 2,
                                  'evidence_ids': [f'p{i}' for i in indices]})
            response = {'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [], 'facts': facts}
            if turn == 1:
                response.update(recommendation='revise', issues=[{
                    'severity': 'blocker', 'evidence_ids': ['p36', 'p37'], 'scope': 'local',
                    'reason': '测试桩：两段实际回应需要核对。', 'fix_intent': '保留完整因果再复核。'}])
            chunks.append(chief.validate_response(response, body, require_facts=True))
        original = copy.deepcopy(chunks)
        contract = {'author_input': {'premise': '作者要求。' * 1200}}
        cards = {'cards': [{'source_path': 'common[0]', 'value': '完整研究规则。' * 800}]}
        # Reserve a fixed source budget above the complete current policies;
        # new policies must not be tested against a stale instruction allowance.
        baseline, _ = chief.synthesis_prompt('起点。\n终点。', {},
                                            [{'issues': [], 'facts': []}])
        capacity = len(baseline) + 22000
        prompt, allowed = chief.synthesis_prompt(body, contract, chunks, source_cards=cards,
                                                max_prompt_chars=capacity)
        data = payload(prompt)
        self.assertLessEqual(len(prompt), capacity)
        self.assertEqual(data['synthesis_protocol'], chief.SYNTHESIS_PROTOCOL)
        self.assertEqual(data['paragraphs'], [[row['id'], row['text']] for row in rows])
        self.assertEqual(allowed, [row['id'] for row in rows])
        self.assertEqual(data['source_cards'], {'cards': [[row['source_path'], row['value']]
                                                        for row in cards['cards']]})
        self.assertEqual(data['reading_anchors'], [[item['evidence_ids']
            for item in result['issues'] + result['facts']] for result in chunks])
        self.assertEqual(chunks, original)
        # The former redundant span fields alone pushed the same evidence over
        # the input limit. Program validation still binds exact source spans.
        old_size = len(prompt) - len(canonical_json(data['paragraphs'])) + len(canonical_json(rows))
        self.assertGreater(old_size, len(prompt))
        self.assertGreater(old_size, capacity)
        checked = chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'revise',
            'issues': chunks[1]['issues']}, body, allowed_ids=allowed)
        self.assertEqual(checked['issues'][0]['evidence'], rows[36:38])

    def test_synthesis_fills_whole_claim_gaps_before_optional_edges_and_preserves_notes(self):
        texts = [f'第{i}段：' + '完整原文中的动作与回应。' * 25 for i in range(30)]
        texts[7], texts[8] = '中间先改变了行动。', '对方对此作出了回应。'
        texts[12], texts[13] = '较长的关键过程。' * 80, '另一段较长的关键过程。' * 80
        body = '\n'.join(texts)
        bank = chief.paragraphs(body)
        raw = {'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [{
            'severity': 'blocker', 'evidence_ids': ['p11', 'p14'], 'scope': 'local',
            'reason': '测试桩：该行动前后需要核对。', 'fix_intent': '保留完整过程。'}], 'facts': [{
            'statement': '先前断言与后来结果需要对照中间的动作和回应。', 'evidence_ids': ['p6', 'p9']}]}
        notes = [chief.validate_response(raw, body, require_facts=True)]
        original = copy.deepcopy(notes)
        unbounded, _ = chief.synthesis_prompt(body, {}, notes)
        data = payload(unbounded)
        instruction = unbounded.rsplit('\n', 1)[0]
        expected_ids = {'p0', 'p6', 'p7', 'p8', 'p9', 'p11', 'p14', 'p27', 'p28', 'p29'}
        expected = [[row['id'], row['text']] for row in bank if row['id'] in expected_ids]
        budget = len(instruction + '\n' + canonical_json({**data, 'paragraphs': expected}))
        bounded, allowed = chief.synthesis_prompt(body, {}, notes, max_prompt_chars=budget)
        self.assertEqual(payload(bounded)['paragraphs'], expected)
        self.assertEqual(set(allowed), expected_ids)
        self.assertEqual(len(bounded), budget)
        self.assertEqual(payload(bounded)['reading_anchors'], [[
            item['evidence_ids'] for item in notes[0]['issues'] + notes[0]['facts']]])
        self.assertNotIn('reading_notes', payload(bounded))
        self.assertEqual(notes, original)
        # A gap that misses the capacity by one character contributes neither
        # half its paragraphs nor shortened text. Mandatory anchors stay.
        smaller, smaller_ids = chief.synthesis_prompt(body, {}, notes, max_prompt_chars=budget - 1)
        self.assertFalse({'p7', 'p8', 'p12', 'p13'} & set(smaller_ids))
        self.assertTrue({'p0', 'p6', 'p9', 'p11', 'p14', 'p27', 'p28', 'p29'} <= set(smaller_ids))
        self.assertLessEqual(len(smaller), budget - 1)

    def test_synthesis_pair_encoding_preserves_cards_metadata_and_source_anchors(self):
        body = '她先问清条件。\n对方答复后，她才签字。'
        cards = {'version': '完整版本', 'fingerprint': '原始指纹', 'scope': '原始范围',
                 'selection_policy': '原始策略', 'selection_scope': '原始研究范围',
                 'audit': {'source_ids': ['来源甲', '来源乙']},
                 'cards': [{'source_path': 'whole.rules[0]', 'value': '完整规则：“回应前提”。\n不能只总结成功。'},
                           {'source_path': 'common[0]', 'value': '路径、引号与符号均保留：\\原文🙂。'}]}
        notes = [chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'uncertain', 'issues': [], 'facts': [
            {'statement': '先有“条件”及答复，\n再签字；不把成功摘要当成证据。',
             'evidence_ids': ['p0', 'p1']}]}, body, require_facts=True)]
        original_cards, original_notes = copy.deepcopy(cards), copy.deepcopy(notes)
        prompt, allowed = chief.synthesis_prompt(body, {}, notes, source_cards=cards)
        data = payload(prompt)
        restored_cards = {**data['source_cards'], 'cards': [
            {'source_path': path, 'value': value} for path, value in data['source_cards']['cards']]}
        self.assertEqual(restored_cards, original_cards)
        self.assertEqual(data['reading_anchors'], [[item['evidence_ids'] for item in original_notes[0]['facts']]])
        self.assertNotIn(original_notes[0]['facts'][0]['statement'], prompt)
        self.assertEqual(data['paragraphs'], [[row['id'], row['text']] for row in chief.paragraphs(body)])
        self.assertEqual(allowed, ['p0', 'p1'])
        self.assertEqual(cards, original_cards)
        self.assertEqual(notes, original_notes)
        self.assertIn('reading_anchors只定位', prompt)
        # Nonstandard per-card metadata is preserved instead of silently lost
        # while trying to force the compact pair representation.
        metadata_cards = {'cards': [{'source_path': 'common[0]', 'value': '完整规则。',
                                    'audit': {'source_id': '仍需保留'}}]}
        metadata_prompt, _ = chief.synthesis_prompt(body, {}, notes, source_cards=metadata_cards)
        self.assertEqual(payload(metadata_prompt)['source_cards'], metadata_cards)

    def test_explicit_deep_review_keeps_legacy_entry_point(self):
        self.save('她递来花，我答应了。')
        reviewer = self.reviewer()
        with patch.object(reviewer, '_review_legacy', return_value={'decision': 'legacy'}) as old:
            self.assertEqual(reviewer.review(mode='deep_review')['decision'], 'legacy')
        old.assert_called_once()
        self.assertEqual(self.calls, [])

    def test_out_of_chunk_evidence_and_forged_ready_are_rejected(self):
        body = '我现金八百元。\n我的银行另有十万元。'
        issue = {'severity': 'blocker', 'evidence_ids': ['p1'], 'reason': '测试分账户',
                 'fix_intent': '核对实际账户', 'scope': 'local'}
        with self.assertRaisesRegex(StoryError, '实际阅读范围'):
            chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'revise', 'issues': [issue]}, body, allowed_ids=['p0'])
        with self.assertRaisesRegex(StoryError, '却建议ready'):
            chief.validate_response({'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': [issue]}, body)
        with self.assertRaisesRegex(StoryError, '单段正文'):
            chief.build_plan('不拆开的长段。' * 2000, {}, max_prompt_chars=4000)


if __name__ == '__main__':
    unittest.main()
