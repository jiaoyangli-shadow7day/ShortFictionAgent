import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from short_fiction_agent.story_studio.selection_review import candidate_contract_violations
from short_fiction_agent.story_studio import reselection_review
from short_fiction_agent.story_studio.inspiration import seed_fingerprint


class ReselectionTests(unittest.TestCase):
    def test_reselection_retries_with_independent_failure_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'reader_promise': '一个结局'}))
            body = '同一方法重复成功的旧稿。'
            write_json(project.root / 'selection.json', {'candidate_id': 'latest_stale'})
            write_json(project.root / 'reselection_context.json', {
                'source_content_hash': digest(body), 'failed_candidate_id': 'original',
                'editorial_findings': ['旧方案同法重复']})
            flow = StoryWorkflow(project, None)
            calls = []
            def develop(*, refresh):
                calls.append(refresh)
                if len(calls) == 1:
                    write_json(project.root / 'reselection_reviews' / 'first.json', {
                        'assessment': {'decision': 'revise', 'issues': [
                            {'reason': '仍是同一举报入口'}]}})
                    raise StoryError('重选候选沿用被否决的核心机制，已在开篇前停止')
                return {'refresh': refresh}
            flow.develop = develop
            report = {'decision': 'revise_required', 'content_hash': digest(body),
                      'review_id': 'review-1', 'issues': [{'origin': 'whole_story',
                      'repair_scope': 'replan', 'criterion': 'agency_and_resistance',
                      'quote': body, 'reason': '同法重复', 'fix_intent': '重新选机制'}]}
            with patch.object(project, 'active_draft', return_value=({}, body)), \
                    patch('short_fiction_agent.story_studio.workflow.current_review', return_value=report):
                self.assertEqual(flow.reselect_after_rejected_draft(
                    editorial_findings=['新规划的垫资动机不成立']), {'refresh': True})
            saved = read_json(project.root / 'reselection_context.json')
            self.assertEqual(saved['failed_candidate_id'], 'original')
            self.assertEqual(saved['editorial_findings'],
                             ['旧方案同法重复', '新规划的垫资动机不成立'])
            self.assertEqual(saved['failed_reselection_attempts'][0]['independent_issues'][0]['reason'],
                             '仍是同一举报入口')
            self.assertEqual(calls, [True, True])

    def test_all_candidate_rejections_feed_next_attempt(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'reader_promise': '一个结局'}))
            body = '未通过全文审查的旧稿。'
            flow = StoryWorkflow(project, None)
            calls = []
            def develop(*, refresh):
                calls.append(refresh)
                if len(calls) == 1:
                    write_json(project.root / 'candidate_comparison.json', {'comparisons': [{
                        'candidate_id': 'bad_candidate', 'rank': 1,
                        'feasibility': {'blocking_claims': [{'quote': '自造的新规则',
                            'reason': '没有作者来源'}]}}], 'recommended_index': None})
                    raise StoryError('全部候选存在核心缺口；已保存独立判断，请修改简报并刷新候选')
                self.assertIn('没有作者来源', str(flow._development_context()))
                return {'refresh': refresh}
            flow.develop = develop
            report = {'decision': 'revise_required', 'content_hash': digest(body),
                      'review_id': 'review-1', 'issues': [{'origin': 'whole_story',
                      'repair_scope': 'replan', 'criterion': 'consequence_chain',
                      'quote': body, 'reason': '因果不足', 'fix_intent': '重选'}]}
            with patch.object(project, 'active_draft', return_value=({}, body)), \
                    patch('short_fiction_agent.story_studio.workflow.current_review', return_value=report):
                self.assertEqual(flow.reselect_after_rejected_draft(), {'refresh': True})
            saved = read_json(project.root / 'reselection_context.json')
            self.assertEqual(saved['failed_reselection_attempts'][0]['candidate_id'], 'bad_candidate')
            self.assertEqual(saved['failed_reselection_attempts'][0]['independent_issues'][0]['reason'],
                             '没有作者来源')

    def test_stale_selected_candidate_cannot_bypass_new_reselection_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'reader_promise': '一个结局'}))
            flow = StoryWorkflow(project, None)
            write_json(project.root / 'reselection_context.json', {'source_content_hash': 'old'})
            write_json(project.root / 'candidates.json', {'candidates': [{'id': 'old'}]})
            write_json(project.root / 'openings.json', {'openings': []})
            write_json(project.root / 'selection.json', {
                'feedback_hash': digest(flow._development_context()),
                'contract_hash': digest(project.brief().effective_contract()),
                'source_hash': seed_fingerprint(project.root), 'candidate_index': 0})
            with self.assertRaisesRegex(StoryError, '重选独立审查'):
                flow._selection()

    def test_reselection_review_requires_candidate_quote(self):
        candidate = {'unique_element': '再次向同一个系统举报入口反复发送审查指令。'}
        issue = {'reason': '沿用旧的举报方法', 'missing_link': '需要不同策略',
                 'candidate_quote': candidate['unique_element']}
        self.assertEqual(reselection_review.validate({'decision': 'revise', 'issues': [issue]},
                         candidate)['decision'], 'revise')
        with self.assertRaisesRegex(StoryError, '原文证据'):
            reselection_review.validate({'decision': 'revise', 'issues': [{**issue,
                'candidate_quote': '候选改成了全新对抗方式。'}]}, candidate)

    def test_external_cash_cannot_be_refrozen_by_system_agent(self):
        contract = {'author_input': {'premise': '奖金到账后是真的钱，不黑银行。'}}
        bad = {'cost': '前期到手的3万元现金被系统代理利用规则空子暂时扣押。'}
        good = {'cost': '主角用已到账的3万元自愿竞价，竞错后实际损失1万元。'}
        self.assertTrue(candidate_contract_violations(contract, bad))
        self.assertFalse(candidate_contract_violations(contract, good))

    def test_failed_full_draft_reopens_candidate_selection_with_bound_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'reader_promise': '主角反击后获得真实选择权'}))
            write_json(project.root / 'selection.json', {'candidate_id': 'old'})
            flow = StoryWorkflow(project, None)
            flow.develop = lambda *, refresh: {'refresh': refresh}
            body = '第一次反击和第二次反击完全相同。'
            report = {'decision': 'revise_required', 'content_hash': digest(body),
                      'review_id': 'review-1', 'issues': [{'origin': 'whole_story',
                      'repair_scope': 'replan', 'criterion': 'agency_and_resistance',
                      'quote': body, 'reason': '第二次没有新的代价',
                      'fix_intent': '更换核心对抗机制'}]}
            with patch.object(project, 'active_draft', return_value=({'artifact_id': 'draft-1'}, body)), \
                    patch('short_fiction_agent.story_studio.workflow.current_review', return_value=report):
                result = flow.reselect_after_rejected_draft(
                    editorial_findings=['旧候选只有同一种举报手法'])
            self.assertEqual(result, {'refresh': True})
            saved = read_json(project.root / 'reselection_context.json')
            self.assertEqual(saved['failed_candidate_id'], 'old')
            self.assertEqual(saved['source_content_hash'], digest(body))
            self.assertIn('旧候选只有同一种举报手法', str(flow._development_context()))

    def test_reselection_rejects_unbound_review(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'reader_promise': '一个结局'}))
            flow = StoryWorkflow(project, None)
            with patch.object(project, 'active_draft', return_value=({}, '当前稿')), \
                    patch('short_fiction_agent.story_studio.workflow.current_review',
                          return_value={'decision': 'revise_required',
                                        'content_hash': digest('另一稿'), 'issues': []}):
                with self.assertRaisesRegex(StoryError, '当前原生稿'):
                    flow.reselect_after_rejected_draft()
