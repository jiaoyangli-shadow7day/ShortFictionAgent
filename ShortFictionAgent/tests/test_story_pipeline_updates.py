"""Real native-state regressions; scripted verdicts test transport, not reader appeal."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio import lead, reader_experience
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest, visible_count
from short_fiction_agent.story_studio.distribution import valid_generation_lineage
from short_fiction_agent.story_studio.quality import StoryReviewer, current_review
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import LeadUpstreamRepair, StoryWorkflow
from test_story_manuscript_lead import LeadLLM as LeadScriptedLLM
from test_story_studio import ScriptedLLM, _prompt_object, reader_basis_fixture
from test_story_reader_experience import reader_response


ENTRY_QUOTE = '林禾发现信不见了。她拿着收据推开记忆店的门。'
REPAIRED_ENTRY = '林禾发现信不见了。她把收据递到店主面前，请他当场说清取信的条件。'


def story_of_length(count):
    """Exact-length prose for budget transport controls, not a quality sample."""
    ending = '林禾取回那封信，失去与母亲共度的一段记忆。'
    remaining = count - visible_count(ENTRY_QUOTE + ending)
    assert remaining > 0
    line = '她询问店主，核对收据，决定是否拿记忆换回信。'
    filling = (line * (remaining // len(line) + 1))[:remaining]
    paragraphs = [filling[index:index + 120] for index in range(0, len(filling), 120)]
    body = '\n'.join([ENTRY_QUOTE, *paragraphs, ending])
    assert visible_count(body) == count
    return body


class CaptureLLM(ScriptedLLM):
    def __init__(self, body):
        super().__init__(body)
        self.prompts = []

    def generate(self, prompt, task_type):
        self.prompts.append((task_type, prompt))
        return super().generate(prompt, task_type)


class UpstreamLeadLLM(LeadScriptedLLM):
    """One grounded lead denial, then a real local edit and normal final review."""
    def __init__(self, body, scope='story_scene'):
        super().__init__(body)
        self.scope = scope
        self.denied = False
        self.repair = None
        self.upstream_edit_inputs = []

    def generate(self, prompt, task_type):
        if task_type == 'lead_comparative_review' and not self.denied:
            self.denied = True
            self.reject_comparison = True
            response = json.loads(super().generate(prompt, task_type))
            self.reject_comparison = False
            self.repair = {'scope': self.scope, 'reason': '传输测试：开篇未落实收据引出的当场追问。',
                           'action': '让林禾把收据递到店主面前，当场追问取信条件。',
                           'story_quote': ENTRY_QUOTE}
            response['repair'] = copy.deepcopy(self.repair)
            return json.dumps(response, ensure_ascii=False)
        if task_type == 'story_edit':
            data = _prompt_object(prompt, ('contract', 'body', 'review_issues'))
            issues = [issue for issue in data['review_issues'] if issue.get('origin') == 'lead_upstream']
            if issues:
                self.prompts.append((task_type, prompt))
                self.tasks.append(task_type)
                self.upstream_edit_inputs.append(copy.deepcopy(data))
                return json.dumps({'back_to_plan': False, 'edits': [{
                    'quote': issues[0]['quote'], 'replacement': REPAIRED_ENTRY,
                    'kind': 'rewrite', 'benefit': '传输测试将指定追问写成当场动作。',
                    'risk': '保留收据来源、交易规则和既有结尾。'}]}, ensure_ascii=False)
        return super().generate(prompt, task_type)


class PipelineUpdatesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def project(self, name, *, short=True, rewards=None):
        raw = {'format': 'short_story' if short else 'micro_story',
               'review_mode': 'deep_review',  # Legacy lead/basis/resume pipeline controls.
               'length': {'target': 3500, 'min': 2500, 'max': 4500} if short else
                         {'target': 900, 'min': 600, 'max': 1100},
               'author_input': {'premise': '林禾找回信，付出记忆代价', 'locked_facts': []}}
        if short:
            raw['opening_mode'] = 'lead_then_chapter'
        if rewards is not None:
            raw['reader_rewards'] = rewards
        project = StoryProject(Path(self.tmp.name) / name)
        project.create(StoryBrief.from_dict(raw))
        return project

    def test_reader_call_text_excludes_feedback_and_future_reward_control(self):
        future_reward = '未来结尾专用控制标记：最后主角获得七笔赎罪款'
        author_defense = '作者自证专用控制标记：下一章会公开谜底'
        project = self.project('blind', rewards=[{'id': 'future', 'desire': '获得补偿',
            'payoff': future_reward, 'required': False}])
        write_json(project.root / 'editorial_feedback.json', {'decision': 'reject', 'reason': author_defense})
        body = '林禾把收据放在柜台上。店主问她愿意拿哪段记忆交换。'
        llm = CaptureLLM(body)
        flow = StoryWorkflow(project, llm)
        contract = project.contract()
        raw = flow.call_text('reader_experience_review', reader_experience.prompt(body, contract))
        reader_prompt = next(prompt for task, prompt in llm.prompts if task == 'reader_experience_review')
        self.assertNotIn(author_defense, reader_prompt)
        self.assertNotIn(future_reward, reader_prompt)
        self.assertNotIn('reader_rewards', reader_prompt)
        self.assertNotIn('editorial_feedback', reader_prompt)
        self.assertIn(body, reader_prompt)
        record = reader_experience.validate(json.loads(raw), body, contract)
        self.assertEqual(record['content_hash'], digest(body))
        # Positive control proves the excluded data exists and is ordinarily injected.
        flow.call_text('story_edit', '传输测试的普通编辑任务。')
        ordinary_prompt = next(prompt for task, prompt in llm.prompts if task == 'story_edit')
        self.assertIn(author_defense, ordinary_prompt)
        self.assertIn(future_reward, ordinary_prompt)

    def test_first_edit_transport_failure_keeps_native_resume_without_prose_rewrite(self):
        class InterruptedEditLLM(ScriptedLLM):
            interrupted = False
            def generate(self, prompt, task_type):
                if task_type == 'story_edit' and not self.interrupted:
                    self.interrupted = True
                    self.tasks.append(task_type)
                    raise RuntimeError('initial edit transport outcome unknown')
                return super().generate(prompt, task_type)
        project = self.project('interrupted', short=False)
        llm = InterruptedEditLLM(story_of_length(900))
        flow = StoryWorkflow(project, llm)
        flow.develop()
        with self.assertRaisesRegex(RuntimeError, 'initial edit transport'):
            flow.generate()
        record, body = project.active_draft()
        checkpoint = read_json(project.root / 'generation_resume.json')
        self.assertEqual(checkpoint['content_hash'], digest(body))
        self.assertEqual(checkpoint['revision'], record['revision'])
        source = read_json(project.root / 'generation_receipts' / f"{checkpoint['source_receipt_hash']}.json")
        self.assertEqual(source['review_status'], 'pending')
        self.assertTrue(valid_generation_lineage(project, source))
        self.assertFalse((project.root / 'generation_receipt.json').exists())
        self.assertEqual(llm.tasks.count('write_micro_story'), 1)
        report = flow.resume_generation()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(llm.tasks.count('write_micro_story'), 1)
        self.assertEqual(project.active_draft()[1], body)
        receipt = read_json(project.root / 'generation_receipt.json')
        self.assertEqual(receipt['entry_point'], 'StoryWorkflow.resume_generation')
        self.assertEqual(receipt['source_receipt_hash'], checkpoint['source_receipt_hash'])
        self.assertTrue(valid_generation_lineage(project, receipt))
        self.assertEqual(current_review(project)['content_hash'], digest(body))

    def test_unsupported_reader_basis_repairs_observation_and_preserves_prose_veto(self):
        project = self.project('basis-repair')
        body = '她只提出明天再谈。我把自己的想法记在手机里，她没有听到。'
        saved = project.save_draft(body)
        body = project.active_draft()[1]
        tasks = []
        def call(task, prompt):
            tasks.append(task)
            if task in {'reader_experience_review', 'reader_basis_repair'}:
                answer = reader_response(body, verdict='revise', kind='none', strength='weak')
                answer['windows'][0]['change']['after'] = (
                    '测试桩故意捏造：对方已听到并回应' if task == 'reader_experience_review'
                    else '对方没有听到独白，尚无回应')
                return answer
            if task == 'reader_basis_grounding':
                data = _prompt_object(prompt, ('reader_windows', 'reader_basis'))
                answer = reader_basis_fixture(data['reader_windows'], data['reader_basis'])
                if tasks.count(task) == 1:
                    for row in answer['windows']:
                        for check in row['checks']:
                            if check['field'] in {'change', 'change.after'}:
                                check['status'] = 'unsupported'
                                check['reason'] = '对方尚未听到，没有证据证明已经回应。'
                return answer
            raise AssertionError('前置读者否决不应启动全文任务：' + task)
        report = StoryReviewer(project, call).review()
        self.assertEqual(report['review_stage'], 'reader_experience_preflight')
        self.assertEqual(report['decision'], 'revise_required')
        self.assertEqual(tasks.count('reader_basis_repair'), 1)
        self.assertEqual(tasks.count('reader_basis_grounding'), 2)
        self.assertIn('pull:weak', ' '.join(report['blockers']))
        self.assertFalse(any(issue.get('origin') == 'reader_basis' for issue in report['issues']))
        active, current_body = project.active_draft()
        self.assertEqual(current_body, body)
        self.assertEqual(active['revision'], saved['revision'])
        self.assertEqual(report['reader_experience']['windows'][0]['change']['after'],
                         '对方没有听到独白，尚无回应')

    def test_unconfirmed_basis_is_review_error_and_never_a_prose_revision(self):
        project = self.project('basis-unconfirmed')
        body = '她只提出明天再谈。我把自己的想法记在手机里，她没有听到。'
        saved = project.save_draft(body)
        body = project.active_draft()[1]
        tasks = []
        def call(task, prompt):
            tasks.append(task)
            if task in {'reader_experience_review', 'reader_basis_repair'}:
                answer = reader_response(body)
                if task == 'reader_basis_repair':
                    answer['windows'][0]['reason'] += '；修过的审读仍须对其新版本独立核验。'
                return answer
            if task == 'reader_basis_grounding':
                data = _prompt_object(prompt, ('reader_windows', 'reader_basis'))
                return reader_basis_fixture(data['reader_windows'], data['reader_basis'],
                                            status='unsupported')
            raise AssertionError('依据未确认不得执行正文编辑或全文审读：' + task)
        with self.assertRaisesRegex(StoryError, '依据核验仍未通过'):
            StoryReviewer(project, call).review()
        active, current_body = project.active_draft()
        self.assertEqual(active['state'], 'review_error')
        self.assertEqual(active['revision'], saved['revision'])
        self.assertEqual(current_body, body)
        self.assertEqual(tasks.count('reader_basis_repair'), 1)
        self.assertEqual(tasks.count('reader_basis_grounding'), 2)
        evidence = list((project.root / 'reader_basis').glob('*.json'))
        self.assertTrue(evidence)
        self.assertTrue(all(read_json(path)['basis_blockers'] for path in evidence))
        self.assertFalse(list((project.root / 'reviews').glob('*.json')))

    def test_delayed_lead_preserves_story_and_enforces_author_maximum_after_attachment(self):
        class BudgetLeadLLM(LeadScriptedLLM):
            def __init__(self, body):
                super().__init__(body)
                self.requested_lengths = []
            def generate(self, prompt, task_type):
                if task_type == 'repair_story_length':
                    self.prompts.append((task_type, prompt))
                    self.tasks.append(task_type)
                    data = _prompt_object(prompt, ('contract', 'body'))
                    self.requested_lengths.append(copy.deepcopy(data['contract']['length']))
                    return story_of_length(data['contract']['length']['target'])
                return super().generate(prompt, task_type)
        project = self.project('budget')
        contract = project.contract()
        llm = BudgetLeadLLM(story_of_length(contract['length']['max']))
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        report = flow.generate(auto_revise=False)
        self.assertEqual(report['decision'], 'revise_required')
        self.assertIn('length_contract', report['blockers'])
        budget = lead.story_length(contract)
        self.assertEqual(budget, contract['length'])
        self.assertEqual(llm.requested_lengths, [])
        lead_prompt = next(prompt for task, prompt in llm.prompts if task == 'lead_candidates')
        data = _prompt_object(lead_prompt, ('contract', 'story', 'story_quote_choices'))
        prelead = lead.unmark_story_quotes(data['story'], data['story_quote_choices'])
        self.assertEqual(visible_count(prelead), budget['max'])
        _, final_body = project.active_draft()
        self.assertGreater(visible_count(final_body), contract['length']['max'])
        self.assertEqual(visible_count(lead.split(final_body)[1]), budget['max'])
        self.assertEqual(llm.tasks.count('lead_candidates'), 1)
        self.assertEqual(llm.tasks.count('repair_story_length'), 0)

    def test_upstream_scene_repair_delivers_quote_and_action_to_edit_then_full_review(self):
        project = self.project('upstream')
        llm = UpstreamLeadLLM(story_of_length(3200))
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(len(llm.upstream_edit_inputs), 1)
        issue = llm.upstream_edit_inputs[0]['review_issues'][0]
        self.assertEqual(issue['quote'], llm.repair['story_quote'])
        self.assertEqual(issue['fix_intent'], llm.repair['action'])
        self.assertEqual(issue['repair_scope'], 'story_scene')
        self.assertIn(issue['quote'], llm.upstream_edit_inputs[0]['body'])
        _, final_body = project.active_draft()
        self.assertIn(REPAIRED_ENTRY, lead.split(final_body)[1])
        self.assertNotIn(ENTRY_QUOTE, lead.split(final_body)[1])
        self.assertEqual(llm.tasks.count('lead_candidates'), 2)
        self.assertLess(llm.tasks.index('story_edit', llm.tasks.index('lead_comparative_review')),
                        llm.tasks.index('reader_experience_review'))
        self.assertIn('continuity_review', llm.tasks)
        self.assertIn('whole_story_review', llm.tasks)
        self.assertEqual(current_review(project)['content_hash'], digest(final_body))
        repair = read_json(next((project.root / 'lead_candidates').glob('*/repair-required.json')))
        self.assertEqual(repair['repair']['story_quote'], ENTRY_QUOTE)
        self.assertEqual(repair['status'], 'repair_applied_pending_review')
        self.assertTrue(valid_generation_lineage(project, read_json(project.root / 'generation_receipt.json')))

    def test_needs_author_upstream_denial_preserves_native_body_without_auto_edit(self):
        project = self.project('needs-author')
        body = story_of_length(3200)
        llm = UpstreamLeadLLM(body, scope='needs_author')
        flow = StoryWorkflow(project, llm, max_calls=100)
        flow.develop()
        with self.assertRaises(LeadUpstreamRepair) as caught:
            flow.generate(auto_revise=True)
        self.assertEqual(caught.exception.repair['scope'], 'needs_author')
        record, saved = project.active_draft()
        self.assertEqual(record['state'], 'needs_author')
        self.assertEqual(saved.strip(), body.strip())
        self.assertEqual(llm.upstream_edit_inputs, [])
        self.assertEqual(llm.tasks.count('story_edit'), 1)
        self.assertEqual(llm.tasks.count('lead_candidates'), 1)
        self.assertNotIn('reader_experience_review', llm.tasks)
        self.assertNotIn('continuity_review', llm.tasks)
        self.assertFalse((project.root / 'generation_receipt.json').exists())
        self.assertEqual(read_json(caught.exception.evidence_path)['status'], 'requires_upstream_repair')


if __name__ == '__main__':
    unittest.main()
