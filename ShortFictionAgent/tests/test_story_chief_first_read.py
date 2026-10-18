"""Cold-input, complete-reading and frozen-verdict regressions.

Scripted judgments test orchestration only, never manuscript quality.
"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_first_read as first
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, review_decision
from short_fiction_agent.story_studio.storage import StoryProject, write_json
from tests.narrative_fixtures import narrative_response


def payload(prompt):
    return json.loads(prompt.rsplit('\n', 1)[1])


def stage_for(body, calls, transform=None):
    def stage(task, prompt, allowed, *, first_read_plan):
        calls.append((task, prompt))
        response = narrative_response(prompt)
        if transform:
            transform(response, payload(prompt))
        normalized = first.validate_response(response, body, first_read_plan=first_read_plan)
        receipt = {'input_hash': digest(prompt), 'response_hash': digest(response), 'response': response}
        return normalized, receipt
    return stage


class ChiefFirstReadTests(unittest.TestCase):
    def test_cold_prompt_keeps_public_title_and_exact_source_without_author_or_future(self):
        body = '  她把票递给我。🙂\n\n我接过票，改掉回程车次。\t\n'
        contract = {'title': '追到她后才知道的秘密', 'audience': {'description': '情感读者', 'secret': '不得泄露甲'},
            'author_input': {'premise': '不得泄露乙', 'locked_facts': ['不得泄露丙']},
            'reader_promise': '不得泄露丁', 'ending': '不得泄露戊',
            'planned_reader_promise': {'ending': '不得泄露己'}, 'revision_context': '不得泄露庚'}
        plan = first.next_plan(body, contract)
        data = payload(plan['prompt'])
        self.assertEqual(data['public_context'], {'enabled': False, 'title': contract['title'],
            'audience': '情感读者', 'audience_expectations': [], 'genre': {}, 'tone': '',
            'point_of_view': '', 'entry_facts': []})
        self.assertEqual(data['paragraphs'], [{'id': row['id'], 'text': row['text']}
            for row in first.paragraphs(body)])
        self.assertEqual(data['previous_readings'], [])
        self.assertTrue(data['reading_scope']['is_final'])
        for secret in '甲乙丙丁戊己庚':
            self.assertNotIn('不得泄露' + secret, plan['prompt'])
        changed = copy.deepcopy(contract)
        changed['ending'] = '另一个结局'
        self.assertEqual(first.next_plan(body, changed), plan)
        changed['title'] = '公开标题改变了'
        self.assertNotEqual(first.next_plan(body, changed)['plan_hash'], plan['plan_hash'])

    def test_long_read_delivers_every_full_paragraph_in_order_with_cumulative_history(self):
        body = '\n'.join(f'第{i}段：' + '我记下各个买家的报价，她等我回去答复。' * 14 for i in range(36))
        calls = []
        # The reader-priority instruction adds source-reading context. Keep a
        # multi-turn test budget that can hold that instruction plus history.
        record = first.run_first_read(body, {'title': '她的邀约'}, stage=stage_for(body, calls),
            reading_prompt_chars=6000, max_prompt_chars=17000)
        self.assertGreater(len(calls), 2)
        data = [payload(prompt) for _, prompt in calls]
        rows = [row for part in data for row in part['paragraphs']]
        self.assertEqual(rows, [{'id': row['id'], 'text': row['text']} for row in first.paragraphs(body)])
        for index, part in enumerate(data):
            self.assertEqual(len(part['previous_readings']), index)
            self.assertEqual(part['reading_scope']['is_final'], index == len(data) - 1)
            seen = {row['id'] for earlier in data[:index] for row in earlier['paragraphs']}
            self.assertTrue(all(row['id'] in seen for row in part['source_history']))
            self.assertLessEqual(len(calls[index][1]), 17000)
        reading = record['first_read']
        self.assertTrue(reading['coverage_complete'])
        self.assertEqual(reading['paragraph_ids'], [row['id'] for row in first.paragraphs(body)])
        for row in reading['evidence']:
            self.assertEqual(body[slice(*row['span'])], row['text'])

    def test_cumulative_thin_veto_survives_later_ready_despite_effective_local_turns(self):
        body = '\n'.join(f'第{i}段：' + '他说明一次报价和付款条件，她答应继续等。' * 16 for i in range(22))
        calls = []
        def judge(response, data):
            if data['reading_scope']['is_final']:
                response['cumulative'].update(status='thin',
                    reason='协议负控制：各次报价局部有用，连读仍长时间挤压标题里的邀约。',
                    fix_intent='压缩同功能手续，把邀约的实际回应提前。')
        # Leave room for the shared original-reader criteria plus actual source;
        # still force sequential turns and preserve the final adverse finding.
        record = first.run_first_read(body, {}, stage=stage_for(body, calls, judge),
            reading_prompt_chars=6000, max_prompt_chars=16000)['first_read']
        self.assertTrue(all(part['burden']['status'] == 'effective' for part in record['observations']))
        formal = {'recommendation': 'ready', 'issues': [], 'fact_gate': {'complete': True}}
        result = first.final_gate(formal, body, {}, record)
        self.assertEqual(formal['recommendation'], 'ready')
        self.assertEqual(result['recommendation'], 'revise')
        self.assertEqual(result['issues'][0]['origin'], 'chief_first_read')
        self.assertEqual(result['issues'][0]['scope'], 'global')
        self.assertEqual(result['issues'][0]['severity'], 'blocker')
        self.assertTrue(result['first_read_gate']['blockers'])

    def test_uncertain_cannot_be_promoted_to_ready_by_later_checks(self):
        body = '她打开信。\n他没有回答。'
        def judge(response, data):
            response['burden']['status'] = 'uncertain'
        record = first.run_first_read(body, {}, stage=stage_for(body, [], judge))['first_read']
        self.assertEqual(first.final_gate({'recommendation': 'ready'}, body, {}, record)['recommendation'], 'uncertain')
        self.assertEqual(first.final_gate({'recommendation': 'revise'}, body, {}, record)['recommendation'], 'revise')

    def test_detail_thin_and_uncertain_are_saved_without_final_veto(self):
        body = '双方签了字，发行权归我，我拿到第一笔款。\n我卡里还有五十万元，没写通知由谁发。'
        def judge(response, data):
            for field, status in (('burden', 'thin'), ('cumulative', 'uncertain')):
                response[field].update(status=status, editorial_priority='copyedit_detail',
                    reading_effect='交易、到账和赢法已经看懂，尾数及签字人的法律身份不影响阅读。',
                    reason='金额尾数与版权代理手续没有详述。', fix_intent='仅作初始校对提醒，不扩写手续。')
        record = first.run_first_read(body, {}, stage=stage_for(body, [], judge))['first_read']
        result = first.final_gate({'recommendation': 'ready', 'issues': []}, body, {}, record)
        self.assertEqual(result['recommendation'], 'ready')
        self.assertEqual(result['issues'], [])
        self.assertEqual(result['first_read_gate']['blockers'], [])
        self.assertEqual(result['first_read_gate']['uncertain'], [])
        reminders = result['initial_copyedit_reminders']
        self.assertEqual([row['status'] for row in reminders], ['thin', 'uncertain'])
        self.assertTrue(all(row['editorial_priority'] == 'copyedit_detail' for row in reminders))
        self.assertEqual(record['observations'][0]['burden']['status'], 'thin')
        for row in reminders:
            for evidence in row['evidence']:
                self.assertEqual(body[slice(*evidence['span'])], evidence['text'])

    def test_contract_procedure_drag_is_still_a_core_reading_blocker(self):
        body = '我又核对一次付款条款。\n她在楼下等我的答复，我仍在逐张核对版权文件。'
        def judge(response, data):
            response['burden'].update(status='thin', editorial_priority='reading_core',
                reading_effect='反复办理版权与金额手续，标题里的感情回应一直没有发生。',
                reason='人物交锋停摆，连续手续挤压核心关系回报。', fix_intent='压缩重复手续，让她实际接招。')
        record = first.run_first_read(body, {}, stage=stage_for(body, [], judge))['first_read']
        result = first.final_gate({'recommendation': 'ready', 'issues': []}, body, {}, record)
        self.assertEqual(result['recommendation'], 'revise')
        self.assertTrue(result['first_read_gate']['blockers'])
        self.assertEqual(result['issues'][0]['editorial_priority'], 'reading_core')
        self.assertEqual(result['issues'][0]['severity'], 'blocker')

    def test_typed_priority_requires_specific_reading_effect(self):
        body = '她把账单推给我。'
        plan = first.next_plan(body, {})
        response = narrative_response(plan['prompt'])
        response['burden']['editorial_priority'] = 'copyedit_detail'
        with self.assertRaisesRegex(StoryError, '具体读者影响'):
            first.validate_response(response, body, first_read_plan=plan)

    def test_future_ids_changed_source_and_changed_plan_are_rejected(self):
        body = '\n'.join('第%d段：' % i + '完整原段内容。' * 30 for i in range(12))
        plan = first.next_plan(body, {}, max_prompt_chars=6000, reading_prompt_chars=3000)
        response = narrative_response(plan['prompt'])
        response['cumulative']['evidence_ids'] = ['p11']
        self.assertNotIn('p11', plan['paragraph_ids'])
        with self.assertRaisesRegex(StoryError, '越过实际阅读范围'):
            first.validate_response(response, body, first_read_plan=plan)
        with self.assertRaisesRegex(StoryError, '绑定损坏'):
            first.validate_response(narrative_response(plan['prompt']), body + '\n后添原文。', first_read_plan=plan)
        tampered = copy.deepcopy(plan)
        tampered['prompt'] += '改动'
        with self.assertRaisesRegex(StoryError, '绑定损坏'):
            first.validate_response(narrative_response(plan['prompt']), body, first_read_plan=tampered)
        with self.assertRaisesRegex(StoryError, '不能截断或跳读'):
            first.next_plan('不可分割的长段。' * 1500, {}, max_prompt_chars=3000)

    def test_default_live_first_read_is_cold_frozen_cached_and_replayed(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder) / 'story')
            project.create(StoryBrief.from_dict({'title': '她会赴约吗', 'format': 'micro_story',
                'length': {'min': 1, 'target': 100, 'max': 1000},
                'author_input': {'premise': '作者解释秘密甲'},
                'reader_promise': '方案保证秘密乙'}))
            project.save_draft('我先逐条核对报价。\n她在约好的地方一直等我。\n我再次重算付款日。')
            write_json(project.root / 'editorial_feedback.json', {'decision': 'reject', 'reason': '旧意见秘密丙'})
            calls = []
            def call(task, prompt):
                calls.append((task, prompt))
                response = narrative_response(prompt)
                if task == 'chief_editor_first_read':
                    response['cumulative'].update(status='thin',
                        reason='协议负控制：反复手续挤压赴约回应。', fix_intent='让人物实际回应邀约。')
                return response
            with patch('short_fiction_agent.story_studio.quality.model_research_context', return_value={}):
                reviewer = StoryReviewer(project, call)
                report = reviewer.review()
                self.assertEqual(calls[0][0], 'chief_editor_first_read')
                for secret in ('作者解释秘密甲', '方案保证秘密乙', '旧意见秘密丙'):
                    self.assertNotIn(secret, calls[0][1])
                    self.assertIn(secret, calls[1][1])
                self.assertEqual(report['decision'], 'revise_required')
                self.assertEqual(report['chief_editor']['chunks'][0]['response']['recommendation'], 'ready')
                self.assertTrue(report['chief_editor']['response']['first_read_gate']['blockers'])
                count = len(calls)
                reviewer.review()
                self.assertEqual(len(calls), count)
                _, body = project.active_draft()
                self.assertEqual(review_decision(body, project.contract(), report)['decision'], 'revise_required')
                for mutation in ('observation', 'receipt', 'missing'):
                    forged = copy.deepcopy(report)
                    chief = forged['chief_editor']
                    if mutation == 'observation':
                        chief['first_read']['observations'][0]['cumulative']['status'] = 'effective'
                    elif mutation == 'receipt':
                        chief['first_read_receipts'][0]['response']['cumulative']['status'] = 'effective'
                    else:
                        chief.pop('first_read_receipts')
                    chief['record_hash'] = digest({k: v for k, v in chief.items() if k != 'record_hash'})
                    with self.subTest(mutation=mutation), self.assertRaises(StoryError):
                        review_decision(body, project.contract(), forged)

    def test_invalid_completed_first_read_is_cached_without_semantic_retry(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder) / 'story')
            project.create(StoryBrief.from_dict({'format': 'micro_story',
                'length': {'min': 1, 'target': 100, 'max': 1000},
                'author_input': {'premise': '她决定去赴约'}}))
            project.save_draft('她打开信，决定去赴约。')
            calls = []
            def call(task, prompt):
                calls.append(task)
                return {'recommendation': 'ready', 'issues': []}
            with patch('short_fiction_agent.story_studio.quality.model_research_context', return_value={}):
                reviewer = StoryReviewer(project, call)
                for _ in range(2):
                    with self.assertRaisesRegex(StoryError, '初读缺少'):
                        reviewer.review()
            self.assertEqual(calls, ['chief_editor_first_read'])


if __name__ == '__main__':
    unittest.main()
