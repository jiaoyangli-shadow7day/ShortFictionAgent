"""Selected openings must remain version-bound through planning and generation."""
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class OpeningLLM(ScriptedLLM):
    def __init__(self, body):
        super().__init__(body)
        self.prompts = []

    def generate(self, prompt, task_type):
        self.prompts.append((task_type, prompt))
        result = super().generate(prompt, task_type)
        if task_type == "opening_candidates":
            data = json.loads(result)
            for index, opening in enumerate(data["openings"]):
                opening["text"] = f"第{index + 1}种切入：信被取走了。"
            return json.dumps(data, ensure_ascii=False)
        return result


class OpeningBindingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / "story")
        self.project.create(StoryBrief.from_dict({
            "format": "micro_story",
            "length": {"target": 900, "min": 600, "max": 1100},
            "author_input": {"premise": "林禾找信", "locked_facts": []},
        }))
        self.body = ("林禾发现信不见了。"
                     + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 31
                     + "她找回信，也记得代价。")
        self.llm = OpeningLLM(self.body)
        self.flow = StoryWorkflow(self.project, self.llm)

    def test_selected_opening_reaches_plan_and_draft_context(self):
        result = self.flow.develop(opening_index=2)
        expected = digest(result["opening"])
        self.assertEqual(result["selection"]["opening_hash"], expected)
        plan = self.flow.plan()
        self.assertEqual(plan["opening_hash"], expected)
        self.assertEqual(read_json(self.project.root / "plan/scenes.json")["opening_hash"], expected)
        self.flow._draft_text(plan)
        for task in ("story_blueprint", "write_micro_story"):
            prompt = next(prompt for name, prompt in self.llm.prompts if name == task)
            self.assertIn(json.dumps(result["opening"], ensure_ascii=False), prompt)
            self.assertNotIn("第1种切入", prompt)

    def test_all_plan_formats_bind_the_selected_opening(self):
        for fmt, length in (
            ("micro_story", {"target": 900, "min": 600, "max": 1100}),
            ("short_story", {"target": 7800, "min": 6000, "max": 10000}),
            ("short_serial", {"target": 18000, "min": 12000, "max": 22000}),
        ):
            with self.subTest(format=fmt):
                project = StoryProject(Path(self.tmp.name) / fmt)
                project.create(StoryBrief.from_dict({"format": fmt, "length": length,
                    "author_input": {"premise": "林禾找信", "locked_facts": []}}))
                flow = StoryWorkflow(project, OpeningLLM(self.body))
                selected = flow.develop(opening_index=1)["opening"]
                plan = flow.plan()
                self.assertEqual(plan["opening_hash"], digest(selected))
                self.assertEqual(read_json(project.root / "plan/scenes.json")["opening_hash"],
                                 digest(selected))

    def test_changed_opening_text_or_id_invalidates_selection_and_plan(self):
        for field, value in (("text", "一封完全不同的信。"), ("id", "replacement")):
            with self.subTest(field=field):
                self.flow.develop(opening_index=1, refresh=True)
                path = self.project.root / "openings.json"
                openings = read_json(path)
                openings["openings"][1][field] = value
                write_json(path, openings)
                calls = self.flow.calls
                with self.assertRaisesRegex(StoryError, "所选开头已更改"):
                    self.flow._selection()
                with self.assertRaisesRegex(StoryError, "所选开头已更改"):
                    self.flow.plan()
                self.assertEqual(self.flow.calls, calls)

    def test_legacy_selection_without_hash_requires_redevelopment(self):
        self.flow.develop()
        path = self.project.root / "selection.json"
        selection = read_json(path)
        selection.pop("opening_hash")
        write_json(path, selection)
        before = path.read_bytes()
        with self.assertRaisesRegex(StoryError, "缺少版本绑定"):
            self.flow.plan()
        self.assertEqual(path.read_bytes(), before)

    def test_changed_selection_id_cannot_keep_old_hash(self):
        self.flow.develop()
        path = self.project.root / "selection.json"
        selection = read_json(path)
        selection["opening_id"] = "another-opening"
        write_json(path, selection)
        with self.assertRaisesRegex(StoryError, "所选开头已更改"):
            self.flow._selection()

    def test_legacy_plan_without_hash_cannot_start_prose_generation(self):
        self.flow.develop()
        plan = self.flow.plan()
        plan.pop("opening_hash")
        calls = self.flow.calls
        with self.assertRaisesRegex(StoryError, "场景方案.*缺少绑定"):
            self.flow._draft_text(plan)
        self.assertEqual(self.flow.calls, calls)

    def test_reselecting_opening_invalidates_old_plan(self):
        self.flow.develop(opening_index=0)
        plan = self.flow.plan()
        self.flow.develop(opening_index=2)
        calls = self.flow.calls
        with self.assertRaisesRegex(StoryError, "场景方案与所选开头版本不一致"):
            self.flow._draft_text(plan)
        self.assertEqual(self.flow.calls, calls)

    def test_generate_binds_receipt_to_the_selected_opening(self):
        result = self.flow.develop(opening_index=2)
        report = self.flow.generate()
        self.assertEqual(report["decision"], "passed")
        receipt = read_json(self.project.root / "generation_receipt.json")
        self.assertEqual(receipt["opening_hash"], digest(result["opening"]))
        self.assertEqual(receipt["selection_hash"], digest(read_json(self.project.root / "selection.json")))

    def test_independent_edit_before_first_review_keeps_source_and_reviews_new_body(self):
        self.flow.develop()
        source_body = self.flow._draft_text(self.flow.plan())
        edited = source_body.replace('信不见了', '信被藏起来了')
        original_review = self.flow.reviewer.review
        reviewed = []
        def review():
            reviewed.append(self.project.active_draft()[1])
            return original_review()
        self.flow.reviewer.review = review
        report = self.flow.generate(editor_edits={
            'source_content_hash': digest(source_body),
            'edits': [{'quote': '信不见了', 'replacement': '信被藏起来了'}]})
        self.assertEqual(report['decision'], 'passed')
        self.assertEqual(reviewed, [edited])
        evidence = read_json(next((self.project.root / 'editor_revisions').glob('*.json')))
        source = self.project.root / 'drafts' / evidence['source_artifact_id'] / str(evidence['source_revision']) / 'body.md'
        self.assertEqual(source.read_text(), source_body)
        self.assertIn('not model-generated or model-approved', evidence['provenance'])
        self.assertEqual(report['content_hash'], digest(edited))

    def test_stale_independent_edit_cannot_overwrite_generated_text(self):
        self.flow.develop()
        with self.assertRaisesRegex(StoryError, '未绑定本次生成正文'):
            self.flow.generate(editor_edits={'source_content_hash': digest('旧正文'), 'edits': []})
        self.assertFalse((self.project.root / 'editor_revisions').exists())
        self.assertFalse((self.project.root / 'generation_receipt.json').exists())

    def test_independent_edit_never_substitutes_for_model_review(self):
        self.flow.develop()
        source_body = self.flow._draft_text(self.flow.plan())
        self.flow.reviewer.review = lambda: (_ for _ in ()).throw(RuntimeError('review incomplete'))
        with self.assertRaisesRegex(RuntimeError, 'review incomplete'):
            self.flow.generate(editor_edits={'source_content_hash': digest(source_body),
                'edits': [{'quote': '信不见了', 'replacement': '信被藏起来了'}]})
        self.assertFalse((self.project.root / 'generation_receipt.json').exists())

    def test_opening_change_during_generation_prevents_success_receipt(self):
        self.flow.develop()
        original_review = self.flow.reviewer.review

        def review_then_change_opening():
            report = original_review()
            path = self.project.root / "openings.json"
            openings = read_json(path)
            openings["openings"][0]["text"] = "生成期间换掉开头。"
            write_json(path, openings)
            return report

        self.flow.reviewer.review = review_then_change_opening
        with self.assertRaisesRegex(StoryError, "所选开头已更改"):
            self.flow.generate()
        self.assertFalse((self.project.root / "generation_receipt.json").exists())


if __name__ == "__main__":
    unittest.main()
