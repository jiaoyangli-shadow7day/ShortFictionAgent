"""Source-binding regressions; fixtures do not establish model semantic accuracy."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio import reader_experience as reader
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from test_story_reader_experience import CALIBRATION, grounding_response, reader_response


class ReaderGroundingTests(unittest.TestCase):
    def setUp(self):
        self.body = '她把杯子推过来。\n“你先喝。”\n我把围巾搭在她椅背，她笑了。'
        self.contract = {'review_mode': 'deep_review', 'format': 'short_story', 'ending_mode': 'closed',
                         'length': {'min': 1, 'target': 100, 'max': 20000},
                         'author_input': {'premise': '未来他们结婚'}, 'reader_promise': '未来大奖',
                         'audience': {'description': '不要泄露的作者受众说明'}}
        self.record = reader.validate(reader_response(self.body), self.body, self.contract)

    def _audit(self, response=None, record=None, body=None):
        body = self.body if body is None else body
        return reader.validate_grounding(response or grounding_response(body), body,
                                         self.contract, record or self.record)

    def test_grounding_prompt_has_anonymous_source_once_without_interest_verdict_or_plan(self):
        prompt = reader.grounding_prompt(self.body, self.contract, self.record)
        data = json.loads(prompt.split('\n', 1)[1])
        for forbidden in ('未来他们结婚', '未来大奖', '不要泄露的作者受众说明'):
            self.assertNotIn(forbidden, prompt)
        self.assertEqual(data['reader_windows'], reader.windows(self.body))
        self.assertEqual(data['fields'], list(reader.GROUNDING_FIELDS))
        claims = data['reader_basis'][0]['claims']
        self.assertEqual(set(claims), set(reader.GROUNDING_FIELDS))
        projected = json.dumps(data['reader_basis'], ensure_ascii=False)
        for forbidden in ('"verdict"', '"strength"', '"interest_effect"', '"fix_intent"', '"evidence"'):
            self.assertNotIn(forbidden, projected)
        # Compression itself may quote a source paragraph; source/evidence copies
        # are not repeated through the record's automatically generated anchors.
        self.assertEqual(prompt.count('我把围巾搭在她椅背，她笑了。'), 1)
        for required in ('不重评兴趣', '未注明的推断', '不能自行替原审读补上', '认知状态',
                         '不意味着正文应返修', '安静相处、温情、悲剧和坏结局',
                         '不得概括成较弱说法', '提出一起去检查不等于已经动身',
                         '答应明天付款不等于已付清', '亲属现身不等于已确认串通骗财'):
            self.assertIn(required, prompt)

    def test_atomic_projection_preserves_each_original_change_assertion(self):
        response = reader_response(self.body)
        change = response['windows'][0]['change']
        change.update({'before': '她还没有回应', 'action': '她提议明天支付',
                       'response': '对方已经动身', 'after': '全部款项已经付清'})
        record = reader.validate(response, self.body, self.contract)
        data = json.loads(reader.grounding_prompt(self.body, self.contract, record).split('\n', 1)[1])
        claims = data['reader_basis'][0]['claims']
        self.assertNotIn('change', claims)
        for field in ('before', 'action', 'response', 'after'):
            self.assertEqual(claims[f'change.{field}'], change[field])
        old_group = grounding_response(self.body)
        old_group['windows'][0]['checks'] = [check for check in old_group['windows'][0]['checks']
                                             if not check['field'].startswith('change.')]
        old_group['windows'][0]['checks'].append({'field': 'change', 'status': 'supported',
            'reason': '只确认了付款共识', 'evidence_paragraph_indices': [0]})
        with self.assertRaisesRegex(StoryError, '八项'):
            self._audit(old_group, record=record)

    def test_supported_and_explicit_inference_pass_without_erasing_a_reader_veto(self):
        self.record['windows'][0]['change']['after'] = '推断：她的笑容意味着收到照顾'
        self.record = reader.validate(self.record, self.body, self.contract)
        response = grounding_response(self.body, overrides={('first_screen', 'change.after'): {
            'status': 'inferred', 'reason': '原审读已写推断；末段笑容支持收到照顾的理解',
            'evidence_paragraph_indices': [2]}})
        audit = self._audit(response)
        self.assertEqual(reader.grounding_gate(self.body, self.contract, self.record, audit), [])
        self.assertEqual(audit['reader_review_hash'], digest(self.record))
        for row in audit['windows']:
            for check in row['checks']:
                for item in check['evidence']:
                    self.assertEqual(self.body[slice(*item['span'])], item['text'])
        negative = reader.validate(reader_response(self.body, strength='weak'), self.body, self.contract)
        audit = self._audit(record=negative)
        self.assertEqual(reader.grounding_gate(self.body, self.contract, negative, audit), [])
        self.assertEqual(reader.gate(self.body, self.contract, negative),
                         ['reader_experience:first_screen:pull:weak'])

    def test_window_and_field_coverage_cannot_be_missing_duplicate_or_unknown(self):
        for mutate in ('no_window', 'duplicate_window', 'no_field', 'duplicate_field', 'unknown_field'):
            with self.subTest(mutate=mutate):
                response = grounding_response(self.body)
                if mutate == 'no_window':
                    response['windows'] = []
                elif mutate == 'duplicate_window':
                    response['windows'] *= 2
                elif mutate == 'no_field':
                    response['windows'][0]['checks'].pop()
                elif mutate == 'duplicate_field':
                    response['windows'][0]['checks'].append(response['windows'][0]['checks'][0])
                else:
                    response['windows'][0]['checks'][0]['field'] = 'verdict'
                with self.assertRaises(StoryError):
                    self._audit(response)

    def test_status_reason_and_evidence_are_required_and_source_bound(self):
        for field, value in (('status', 'passed'), ('reason', ' '),
                             ('evidence_paragraph_indices', []), ('evidence_paragraph_indices', [True]),
                             ('evidence_paragraph_indices', [0, 0]), ('evidence_paragraph_indices', [999])):
            with self.subTest(field=field, value=value):
                response = grounding_response(self.body)
                response['windows'][0]['checks'][0][field] = value
                with self.assertRaises(StoryError):
                    self._audit(response)
        response = grounding_response(self.body)
        response['windows'][0]['checks'][0].pop('evidence_paragraph_indices')
        with self.assertRaises(StoryError):
            self._audit(response)
        body = '\n'.join('她看着他端来的茶。' * 18 for _ in range(24))
        record = reader.validate(reader_response(body), body, self.contract)
        response = grounding_response(body)
        response['windows'][0]['checks'][0]['evidence_paragraph_indices'] = [
            reader.windows(body)[1]['paragraphs'][0]['paragraph_index']]
        with self.assertRaisesRegex(StoryError, '越窗'):
            self._audit(response, record=record, body=body)

    def test_grounding_cannot_be_reused_after_any_reader_body_contract_or_protocol_change(self):
        audit = self._audit()
        changed = copy.deepcopy(self.record)
        changed['windows'][0]['reason'] = '同一正文的另一版审读结论'
        changed = reader.validate(changed, self.body, self.contract)
        with self.assertRaisesRegex(StoryError, '原审读记录'):
            reader.grounding_gate(self.body, self.contract, changed, audit)
        for key, value in (('protocol', 'older-basis'), ('reader_protocol', 'reader-experience-2026-10-08-v2'),
                           ('source_sha256', '0' * 64), ('reader_review_hash', '0' * 64)):
            forged = copy.deepcopy(audit)
            forged[key] = value
            with self.assertRaises(StoryError):
                reader.grounding_gate(self.body, self.contract, self.record, forged)
        forged = copy.deepcopy(audit)
        forged['windows'][0]['checks'][0]['evidence'][0]['text'] = '她答应结婚。'
        with self.assertRaisesRegex(StoryError, '位置'):
            reader.grounding_gate(self.body, self.contract, self.record, forged)
        for body, contract in ((self.body + '后来。', self.contract),
                               (self.body, {**self.contract, 'ending_mode': 'tragic'})):
            with self.assertRaises(StoryError):
                reader.grounding_gate(body, contract, self.record, audit)

    def test_actual_counterexample_claims_have_exact_source_anchors_without_prose_issues(self):
        # Editorial regression claims include a project-authored quiet example;
        # these are fixtures, not a simulated model-success rate.
        cases = {case['id']: case for case in CALIBRATION['cases']}
        examples = [
            ('roommate_technical_flirt', 'change.response', 12,
             '姜莱收起借口并眼带笑意地拉起陆巡胳膊，要求进主卧实地查看衣柜空间；陆巡欣然配合。',
             '原段只给姜莱邀他先看衣柜；没有陆巡的实际配合回应'),
            ('roommate_technical_flirt', 'change.after', 12,
             '双方正式挑明并确认了搬入主卧共同起居的决定，关系由分房合租升级为正式同寝同居。',
             '原段支持邀住及共同起居意愿，不支持已经实现正式同寝同居'),
            ('wedding_description_changes_understanding', 'pull', 12,
             '真父亲被藏在后厨，假父亲目睹了雇佣骗局',
             '此段确认老汉是他爹；藏人和雇佣骗局不是窗口已确认事实'),
            ('quiet_existing_affection', 'compression', 7,
             '她把新钥匙收了回去。',
             '原段写她试图拿回而我装进口袋；不能把未成功动作写成已完成')]
        for case_id, field, index, claim, reason in examples:
            with self.subTest(case_id=case_id):
                body = cases[case_id]['body']
                response = reader_response(body)
                row = response['windows'][0]
                if field.startswith('change.'):
                    row['change'][field.split('.')[1]] = claim
                elif field == 'pull':
                    row['pull']['reason_to_continue'] = claim
                else:
                    row['most_skippable']['paragraph_index'] = index
                    row['most_skippable']['compression']['replacement'] = claim
                record = reader.validate(response, body, self.contract)
                basis = reader.validate_grounding(grounding_response(body, overrides={('first_screen', field): {
                    'status': 'unsupported', 'reason': reason, 'evidence_paragraph_indices': [index]}}),
                    body, self.contract, record)
                self.assertEqual(reader.grounding_gate(body, self.contract, record, basis),
                                 [f'reader_basis:first_screen:{field}:unsupported'])
                self.assertEqual(reader.issues(record), [])
                check = next(item for item in basis['windows'][0]['checks'] if item['field'] == field)
                anchor = check['evidence'][0]
                self.assertEqual(body[slice(*anchor['span'])], anchor['text'])
                repair = reader.basis_repair_prompt(body, self.contract, record, basis)
                self.assertIn(claim, repair)
                self.assertIn('不强制continue', repair)
                self.assertIn('仍有依据的负面判断须保留', repair)

    def test_unconfirmed_basis_stops_as_review_error_with_original_prose_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'review_mode': 'deep_review', 'format': 'short_story', 'opening_mode': 'direct_scene',
                'length': {'min': 1, 'target': 1000, 'max': 2000},
                'author_input': {'premise': '双方照顾彼此', 'locked_facts': []}}))
            project.save_draft(self.body)
            original_draft, original_body = project.active_draft()
            tasks = []
            def call_json(task, prompt):
                tasks.append(task)
                if task in {'reader_experience_review', 'reader_basis_repair'}:
                    response = reader_response(original_body)
                    if task == 'reader_basis_repair':
                        response['windows'][0]['reason'] = '修复后的另一版审读，但仍有未核实的回应'
                    return response
                if task == 'reader_basis_grounding':
                    return grounding_response(original_body, overrides={('first_screen', 'change.response'): {
                        'status': 'unsupported', 'reason': '审读仍将未发生的回应当事实',
                        'evidence_paragraph_indices': [0]}})
                self.fail('未确认审读依据时不能进入全文评分或正文编辑')
            with self.assertRaisesRegex(StoryError, '正文已保留'):
                StoryReviewer(project, call_json).review()
            self.assertEqual(tasks, ['reader_experience_review', 'reader_basis_grounding',
                                     'reader_basis_repair', 'reader_basis_grounding'])
            current_draft, current_body = project.active_draft()
            self.assertEqual(current_body, original_body)
            self.assertEqual(current_draft['revision'], original_draft['revision'])
            self.assertEqual(list((project.root / 'reviews').glob('*.json')), [])
            records = [read_json(path) for path in (project.root / 'reader_basis').glob('*.json')]
            self.assertTrue(records)
            self.assertTrue(all(item['basis_blockers'] for item in records))


if __name__ == '__main__':
    unittest.main()
