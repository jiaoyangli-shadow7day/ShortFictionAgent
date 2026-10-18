import unittest
import test_story_opening_binding
from short_fiction_agent.story_studio.contracts import StoryError, digest
from short_fiction_agent.story_studio.storage import read_json
from short_fiction_agent.story_studio.distribution import valid_generation_lineage


class ResumeGenerationTests(unittest.TestCase):
    def setUp(self):
        fixture = test_story_opening_binding.OpeningBindingTests()
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.project, self.flow = fixture.project, fixture.flow
        self.flow.develop()
        self.flow.generate()

    def test_current_draft_resume_keeps_native_ancestry(self):
        original = read_json(self.project.root / 'generation_receipt.json')
        self.flow._edit = lambda body, **kwargs: body.replace('信不见了', '信被藏起来了')
        self.flow.resume_generation(issues=[{'reason': '修改触发事件'}])
        receipt = read_json(self.project.root / 'generation_receipt.json')
        self.assertEqual(receipt['entry_point'], 'StoryWorkflow.resume_generation')
        self.assertEqual(receipt['source_receipt_hash'], digest(original))
        self.assertTrue(valid_generation_lineage(self.project, receipt))
        self.assertEqual(receipt['content_hash'], self.project.active_draft()[0]['content_hash'])
        # Altered old evidence invalidates the derivative receipt, too.
        source = self.project.root/'drafts'/original['artifact_id']/str(original['revision'])/'body.md'
        source.write_text('被替换的历史正文')
        self.assertFalse(valid_generation_lineage(self.project, receipt))

    def test_unknown_review_resumes_saved_revision_without_edit_replay(self):
        review = self.flow.reviewer.review
        edits = []
        def edit(body, **kwargs):
            edits.append(body)
            return body.replace('信不见了', '信被藏起来了')
        self.flow._edit = edit
        self.flow.reviewer.review = lambda: (_ for _ in ()).throw(RuntimeError('unknown'))
        with self.assertRaises(RuntimeError):
            self.flow.resume_generation(issues=[{'reason': '修改触发事件'}])
        saved = self.project.active_draft()[0]
        self.flow.reviewer.review = review
        self.flow.resume_generation()
        self.assertEqual(len(edits), 1)
        self.assertEqual(saved['content_hash'], self.project.active_draft()[0]['content_hash'])

    def test_first_review_error_keeps_native_draft_resumable(self):
        fixture = test_story_opening_binding.OpeningBindingTests()
        fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        fixture.flow.develop()
        review = fixture.flow.reviewer.review
        fixture.flow.reviewer.review = lambda: (_ for _ in ()).throw(StoryError('引文格式错误'))
        with self.assertRaisesRegex(StoryError, '引文格式错误'):
            fixture.flow.generate()
        record, body = fixture.project.active_draft()
        checkpoint = read_json(fixture.project.root / 'generation_resume.json')
        source = read_json(fixture.project.root / 'generation_receipts' /
                           f"{checkpoint['source_receipt_hash']}.json")
        self.assertEqual(source['review_status'], 'pending')
        self.assertTrue(valid_generation_lineage(fixture.project, source))
        self.assertFalse((fixture.project.root / 'generation_receipt.json').exists())
        fixture.flow.reviewer.review = review
        fixture.flow.resume_generation()
        self.assertEqual(fixture.project.active_draft()[0]['content_hash'], record['content_hash'])
        self.assertEqual(fixture.project.active_draft()[1], body)

    def test_untracked_external_revision_cannot_gain_native_receipt(self):
        record, body = self.project.active_draft()
        self.project.save_draft(body + '外部追加。', artifact_id=record['artifact_id'], parent_revision=record['revision'])
        with self.assertRaisesRegex(StoryError, '缺少有效原生生成'):
            self.flow.resume_generation()
        self.assertEqual(read_json(self.project.root/'generation_receipt.json')['revision'], record['revision'])

    def test_editor_changes_are_bound_and_never_presented_as_model_output(self):
        _, body = self.project.active_draft()
        change = {"source_content_hash": digest(body), "edits": [{"quote": "信不见了", "replacement": "信被藏起来了", "kind": "rewrite", "benefit": "澄清行动", "risk": "需重新全文审查"}]}
        self.flow.resume_generation(editor_edits=change)
        files = list((self.project.root / "editor_revisions").glob("*.json"))
        self.assertEqual(len(files), 1)
        evidence = read_json(files[0])
        self.assertIn("not model-generated", evidence["provenance"])
        self.assertEqual(evidence["after_hash"], self.project.active_draft()[0]["content_hash"])
        with self.assertRaisesRegex(StoryError, "未绑定当前正文"):
            self.flow.resume_generation(editor_edits=change)
