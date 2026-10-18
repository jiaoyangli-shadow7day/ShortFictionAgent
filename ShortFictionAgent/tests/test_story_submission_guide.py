import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import lead, submission_guide as guide
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json


def brief(*, editor=False, explicit=False, target=1000):
    publication = {}
    if editor:
        publication['signing_method'] = 'editor_submission'
    if explicit:
        publication['submission_guide_id'] = 'fanqie_editor_dami'
    return StoryBrief.from_dict({
        'project_id': 'guide-test', 'title': '她将辞职信递给了我',
        'genre': {'primary': 'romance'}, 'format': 'short_story',
        'channels': ['fanqie_short'], 'length': {'target': target},
        'author_input': {'premise': '她将辞职信递给了我，却要我替她决定留不留下。'},
        'publication_contract': publication,
    })


class SubmissionGuideScopeTests(unittest.TestCase):
    def test_default_fanqie_uses_semantics_without_editor_format_or_administration(self):
        contract = brief().effective_contract()
        for stage in ('plan', 'lead', 'draft', 'review', 'revision'):
            with self.subTest(stage=stage):
                context = guide.submission_guide_context(contract, stage)
                self.assertTrue(context['enabled'])
                self.assertTrue(context['semantic_requirements'])
                self.assertFalse(context['format_requirements'])
                self.assertFalse(context['administrative'])
                guidance = guide.submission_guide_guidance(contract, stage)
                self.assertIn('必须实际用于本阶段', guidance)
                if stage not in {'plan', 'revision'}:
                    self.assertNotIn('既往短故事收益', guidance)
                else:
                    # Attributed summaries contain administrative facts. Reading them
                    # does not apply them to an APP story as body requirements.
                    self.assertIn('仅作为资料阅读', guidance)
                    self.assertIn('不能扩张成所有正文的硬规则', guidance)
        self.assertEqual(guide.submission_guide_context(contract)['source']['url'],
                         'https://docs.qq.com/doc/DWHJoc1hRQ1puam1J')
        self.assertIn('已完整核对', guide.submission_guide_context(contract)['source']['read_scope'])
        self.assertIn('大纲分享', guide.submission_guide_context(contract)['source']['read_scope'])

    def test_plan_and_revision_receive_all_verified_summaries_and_concrete_application(self):
        contract = brief(target=30000).effective_contract()
        sources = guide.resolve_submission_guide(contract)['source_documents']
        self.assertEqual(len(sources), 3)
        repo = Path(__file__).resolve().parents[2]
        for source in sources:
            self.assertEqual(source['content_kind'], 'project_authored_summary')
            raw = (repo / source['snapshot_path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), source['sha256'])
            self.assertEqual(raw.decode('utf-8'), source['text'])
        for stage in ('plan', 'revision'):
            prompt = guide.submission_guide_guidance(contract, stage)
            for source in sources:
                self.assertIn(source['text'], prompt)
                self.assertIn(source['sha256'], prompt)
            for requirement in ('prose_value', 'reading_points', 'beat_index', '具体怎样接招',
                                '约一万字超短', '长中短不套12章上限'):
                self.assertIn(requirement, prompt)
        # A reader sees the actual prose and relevant scoped criteria; the
        # source pages are not repeated at the expense of that prose.
        for stage in ('draft', 'review', 'lead'):
            prompt = guide.submission_guide_guidance(contract, stage)
            self.assertNotIn('来源全文：', prompt)
            self.assertIn('entry_promise', prompt)

    def test_modified_source_cannot_be_loaded_under_its_verified_hash(self):
        changed = copy.deepcopy(guide._guides())
        changed['profiles']['fanqie_editor_dami']['source_documents'][0]['text'] += '未核对新增要求'
        with patch.object(Path, 'read_text', return_value=json.dumps(changed, ensure_ascii=False)):
            with self.assertRaisesRegex(StoryError, '摘要.*指纹不一致'):
                guide._guides.__wrapped__()

    def test_summary_change_invalidates_existing_plan_and_guide_binding(self):
        contract = brief(target=30000).effective_contract()
        old = guide.submission_guide_context(contract, 'plan')
        changed = copy.deepcopy(guide._guides())
        changed['profiles']['fanqie_editor_dami']['source_documents'][0]['text'] += '\n已核对修订'
        with patch.object(guide, '_guides', return_value=changed):
            new = guide.submission_guide_context(contract, 'plan')
        self.assertNotEqual(old['guide_hash'], new['guide_hash'])
        self.assertEqual(old['semantic_requirement_ids'], new['semantic_requirement_ids'])

    def test_editor_profile_is_explicit_and_preserves_author_length_and_point_of_view(self):
        contract = brief(editor=True, target=45000).effective_contract()
        original_length = copy.deepcopy(contract['length'])
        guide.submission_guide_context(contract, 'plan')
        self.assertEqual(contract['length'], original_length)
        self.assertIsNone(contract['length']['max'])
        self.assertIn('不是统一上限', guide.submission_guide_guidance(contract, 'plan'))
        self.assertIn('不机械硬化', guide.submission_guide_guidance(contract, 'plan'))
        self.assertTrue(guide.submission_guide_context(contract, 'review')['format_requirements'])
        self.assertTrue(guide.submission_guide_context(contract, 'publication')['administrative'])
        self.assertTrue(guide.submission_guide_context(brief(explicit=True).effective_contract())['format_requirements'])
        with self.assertRaisesRegex(StoryError, '档案ID'):
            guide.resolve_submission_guide({'publication_contract': {'submission_guide_id': 'unknown'}})
        generic = StoryBrief.from_dict({'project_id': 'generic-guide', 'author_input': {'premise': '找信'}}).effective_contract()
        self.assertFalse(guide.submission_guide_context(generic)['enabled'])

    def test_editor_qualification_is_pending_without_historical_evidence_not_a_body_rule(self):
        from short_fiction_agent.story_studio.publication import publication_check
        contract = brief(editor=True).effective_contract()
        result = publication_check('字' * 1000, contract)
        self.assertIn('editor_guarantee_qualification', result['pending'])
        self.assertNotIn('editor_guarantee_qualification', result['blockers'])
        self.assertTrue(result['submission_guide']['administrative'])
        self.assertNotIn('近三个月', guide.submission_guide_guidance(contract, 'draft'))

    def test_editor_layout_prepared_before_review_keeps_every_visible_character(self):
        body = '她把辞职信推给我。\n\n第一章\n\n“你替我选。”\n\n我却先签上了自己的名字。\n'
        normalized = guide.prepare_submission_text(body, brief(editor=True).effective_contract())
        self.assertEqual(''.join(body.split()), ''.join(normalized.split()))
        self.assertFalse(guide.submission_guide_format_errors(normalized, brief(editor=True).effective_contract()))
        self.assertEqual(guide.prepare_submission_text(body, brief().effective_contract()), body)
        self.assertTrue(guide.submission_guide_format_errors(body, brief(editor=True).effective_contract()))
        assembled = lead.assemble('导语第一段。\n\n导语第二段。', '完整正文。', brief(editor=True).effective_contract())
        self.assertEqual(lead.split(assembled), ('导语第一段。\n导语第二段。', '完整正文。'))
        self.assertFalse(guide.submission_guide_format_errors(assembled, brief(editor=True).effective_contract()))

    def test_approved_lead_layout_migration_never_forges_new_author_confirmation(self):
        original_contract = brief().effective_contract()
        original = lead.assemble('她把辞职信推给我。\n\n可我先签上了自己的名字。', '她等着我的解释。')
        receipt = lead.approve_lead(original, original_contract)
        untouched = copy.deepcopy(receipt)
        editor_contract = brief(editor=True).effective_contract()
        normalized = guide.prepare_submission_text(original, editor_contract)
        self.assertEqual(''.join(original.split()), ''.join(normalized.split()))
        with self.assertRaisesRegex(StoryError, '保留原确认记录.*作者确认排版版本'):
            lead.validate_approved_lead(normalized, editor_contract, receipt)
        self.assertEqual(receipt, untouched)
        self.assertEqual(lead.validate_approved_lead(original, editor_contract, receipt)['lead_text'],
                         lead.split(original)[0])
        # Preserve the original author choice; do not manufacture a new receipt
        # merely because the requested editor has a different layout convention.
        self.assertTrue(guide.submission_guide_format_errors(original, editor_contract))

    def test_all_lead_paths_receive_selected_guide_and_version_invalidates_cache(self):
        contract = brief().effective_contract()
        story = '她把辞职信推给我。“你替我选。”我却先签上了自己的名字。'
        prompts = [lead.single_candidate_prompt(story, contract),
                   lead.material_selection_prompt(lead.story_paragraphs(story), contract, index=1, count=1),
                   lead.candidates_prompt(story, contract, {}),
                   lead.review_prompt(story, contract, text='她把辞职信推给我。'),
                   lead.ranking_prompt(story, contract, [{'id': 'a', 'text': '导语。'}],
                                       {'a': {'checks': [{'id': 'lead_pull', 'verdict': 'revise', 'reason': '没有具体期待'}]}})]
        for prompt in prompts:
            self.assertIn('fanqie_editor_dami', prompt)
            self.assertIn('entry_promise', prompt)
            self.assertIn('60—200', prompt)
            self.assertIn('正文预算不能挤压导语', prompt)
        old_prompt = lead.candidate_prompt_hash(prompts[2])
        old_criteria = lead.criteria_hash()
        changed = copy.deepcopy(guide._guides())
        changed['profiles']['fanqie_editor_dami']['version'] = 'future-guide'
        with patch.object(guide, '_guides', return_value=changed):
            self.assertNotEqual(old_prompt, lead.candidate_prompt_hash(lead.candidates_prompt(story, contract, {})))
            self.assertNotEqual(old_criteria, lead.criteria_hash())


class SubmissionGuideExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def project(self, *, editor=False):
        project = StoryProject(Path(self.temp.name) / ('editor' if editor else 'app'))
        project.create(brief(editor=editor))
        body = '她把辞职信推给我。\n\n第一章\n\n' + '我签上自己的名字，等她回答。' * 90
        if editor:
            body = guide.prepare_submission_text(body, project.contract())
        record = project.save_draft(body)
        project.update_state(record, 'passed')
        return project, project.active_draft()[1]

    def report(self, project, body):
        return {'review_id': 'guide-reviewed', 'submission_guide': {
            **guide.submission_guide_context(project.contract(), 'review'), 'content_hash': digest(body)}}

    def export(self, project, report):
        with patch('short_fiction_agent.story_studio.channels.current_review', return_value=report), \
             patch('short_fiction_agent.story_studio.distribution.PublicationPreparationAgent.review',
                   return_value={'decision': 'editorial_ready', 'inputs': {'category': 'romance'}}):
            return StoryExporter(project).export('fanqie_short', titles=['她将辞职信递给了我'], synopsis='她让我替她决定。')

    def test_missing_stale_or_wrong_body_receipt_cannot_export(self):
        project, body = self.project()
        reports = [{'review_id': 'old-review'}, self.report(project, body),
                   self.report(project, body), self.report(project, body)]
        reports[1]['submission_guide']['version'] = 'old-guide'
        reports[2]['submission_guide']['content_hash'] = digest(body + '改稿')
        reports[3]['submission_guide']['semantic_requirements'][0]['acceptance'] = '只看是否填了字段'
        for report in reports:
            with self.subTest(report=report), self.assertRaisesRegex(StoryError, '投稿指南|投稿指南版本'):
                self.export(project, report)
        self.assertFalse((project.root / 'exports').exists())

    def test_export_keeps_reviewed_bytes_and_writes_application_evidence(self):
        for editor in (False, True):
            with self.subTest(editor=editor):
                project, body = self.project(editor=editor)
                package = self.export(project, self.report(project, body))
                self.assertEqual((package / 'manuscript.txt').read_text(), body)
                check = read_json(package / 'submission_guide_check.json')
                manifest = read_json(package / 'manifest.json')
                self.assertEqual(check['content_hash'], digest(body))
                self.assertEqual(check['application_binding'], 'current')
                self.assertIn('不证明语义合格', check['context']['meaning'])
                self.assertEqual(manifest['source_content_hash'], digest(body))
                self.assertIn('submission_guide_check.json', manifest['files_sha256'])
                self.assertEqual(manifest['submission_guide']['scope']['format'],
                                 'selected_editor' if editor else 'not_applied_to_app_layout')

    def test_editor_format_failure_does_not_silently_change_passed_body(self):
        project, body = self.project(editor=True)
        changed_body = body.replace('第一章\n', '第一章\n\n')
        record = project.save_draft(changed_body)
        project.update_state(record, 'passed')
        changed_body = project.active_draft()[1]
        with self.assertRaisesRegex(StoryError, '保存排版修订并重新审读'):
            self.export(project, self.report(project, changed_body))
        self.assertEqual(project.active_draft()[1], changed_body)
        draft = StoryExporter(project).export('fanqie_short', draft=True)
        self.assertEqual(read_json(draft / 'manifest.json')['delivery_status'], 'review_copy')
        self.assertTrue(read_json(draft / 'submission_guide_check.json')['format_errors'])


if __name__ == '__main__':
    unittest.main()
