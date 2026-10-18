"""Opening audit and changed editorial standards must invalidate old approvals."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.distribution import (
    PREPARATION_CHECKS, PublicationPreparationAgent, audit_workflow,
)
from short_fiction_agent.story_studio.quality import (
    REVIEW_RUBRIC, current_review, review_rubric_hash,
)
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class ReviewVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / "story")
        self.project.create(StoryBrief.from_dict({"review_mode": "deep_review", "format": "micro_story",
            "length": {"target": 900, "min": 600, "max": 1100},
            "author_input": {"premise": "林禾找信", "locked_facts": []}}))
        body = ("林禾发现信不见了。" + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 31
                + "她找回信，也记得代价。")
        self.llm = ScriptedLLM(body)
        self.flow = StoryWorkflow(self.project, self.llm)
        self.flow.develop()
        self.report = self.flow.generate()
        self.inputs = {"channel": "fanqie_short", "titles": ["找回那封信"],
                       "synopsis": "一封信引发一次选择。", "category": "custom"}
        self.agent = PublicationPreparationAgent(self.project, self.flow.call_json)

    def snapshot(self):
        return {str(path): path.read_bytes() for path in self.project.root.rglob("*") if path.is_file()}

    def test_complete_workflow_audits_opening_selection_and_plan_without_writes(self):
        self.agent.review(**self.inputs)
        before = self.snapshot()
        result = audit_workflow(self.project)
        statuses = {row["check"]: row["status"] for row in result["checks"]}
        self.assertEqual(result["status"], "complete")
        for name in ("opening_comparison", "opening_plan_binding", "native_generation"):
            self.assertEqual(statuses[name], "pass")
        self.assertEqual(self.snapshot(), before)

    def test_altered_or_missing_opening_comparison_cannot_pass_audit(self):
        self.agent.review(**self.inputs)
        path = self.project.root / "opening_comparison.json"
        original = read_json(path)
        for mutation in ("missing", "comment", "input_hash"):
            with self.subTest(mutation=mutation):
                write_json(path, original)
                if mutation == "missing":
                    path.unlink()
                else:
                    data = read_json(path)
                    if mutation == "comment":
                        data["comparisons"][0]["weakness"] = "修改了比较结果"
                    else:
                        data["input_hash"] = "different-input"
                    write_json(path, data)
                before = self.snapshot()
                result = audit_workflow(self.project)
                opening = next(row for row in result["checks"] if row["check"] == "opening_comparison")
                self.assertEqual(opening["status"], "incomplete")
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(self.snapshot(), before)

    def test_unchosen_opening_change_invalidates_audit(self):
        self.agent.review(**self.inputs)
        path = self.project.root / "openings.json"
        data = read_json(path)
        data["openings"][2]["text"] = "不同的开场改变了比较输入。"
        write_json(path, data)
        result = audit_workflow(self.project)
        opening = next(row for row in result["checks"] if row["check"] == "opening_comparison")
        self.assertEqual(opening["status"], "incomplete")

    def test_missing_plan_or_receipt_opening_hash_is_incomplete(self):
        self.agent.review(**self.inputs)
        for relative, check_name in (("plan/scenes.json", "opening_plan_binding"),
                                     ("generation_receipt.json", "native_generation")):
            with self.subTest(relative=relative):
                path = self.project.root / relative
                original = read_json(path)
                data = dict(original); data.pop("opening_hash")
                write_json(path, data)
                result = audit_workflow(self.project)
                check = next(row for row in result["checks"] if row["check"] == check_name)
                self.assertEqual(check["status"], "incomplete")
                write_json(path, original)

    def test_old_full_review_without_standard_hash_requires_new_review(self):
        path = self.project.root / "reviews" / f"{self.report['review_id']}.json"
        report = read_json(path)
        report.pop("review_rubric_hash")
        write_json(path, report)
        before = self.snapshot()
        with self.assertRaisesRegex(StoryError, "报告缺少标准版本"):
            current_review(self.project)
        self.assertEqual(self.snapshot(), before)

    def test_independent_veto_blocks_even_a_model_pass_for_same_body(self):
        record, _ = self.project.active_draft()
        write_json(self.project.root / "independent_veto.json", {
            "content_hash": record["content_hash"], "decision": "reject",
            "issues": [{"reason": "独立复核发现关键事实矛盾"}],
        })
        with self.assertRaisesRegex(StoryError, "独立复核否决"):
            current_review(self.project)
        with self.assertRaisesRegex(StoryError, "独立复核否决"):
            StoryExporter(self.project, self.flow.call_json).export("fanqie_short")

    def test_archived_independent_veto_still_blocks_older_draft(self):
        record, _ = self.project.active_draft()
        history = self.project.root / "independent_veto_history"
        history.mkdir()
        write_json(history / "older-revision.json", {
            "content_hash": record["content_hash"], "decision": "reject",
            "issues": [{"reason": "旧版本的独立否决不能因归档而失效"}],
        })
        with self.assertRaisesRegex(StoryError, "独立复核否决"):
            current_review(self.project)

    def test_changed_full_review_standard_blocks_export_of_same_body(self):
        self.agent.review(**self.inputs)
        old_hash = self.report["review_rubric_hash"]
        with patch.dict(REVIEW_RUBRIC, {"language": "补充了新的语言审读要求"}):
            self.assertNotEqual(old_hash, review_rubric_hash())
            with self.assertRaisesRegex(StoryError, "审查标准已更改"):
                current_review(self.project)
            with self.assertRaisesRegex(StoryError, "审查标准已更改"):
                StoryExporter(self.project, self.flow.call_json).export("fanqie_short")
            self.assertFalse(list((self.project.root / "exports").glob("*/manifest.json")))

    def test_rereview_under_new_standard_does_not_reuse_old_preparation(self):
        first = self.agent.review(**self.inputs)
        with patch.dict(REVIEW_RUBRIC, {"language": "补充了新的语言审读要求"}):
            new_report = self.flow.reviewer.review()
            self.assertEqual(new_report["review_rubric_hash"], review_rubric_hash())
            with self.assertRaisesRegex(StoryError, "缺少当前版本的发布准备审读"):
                PublicationPreparationAgent(self.project).review(**self.inputs)
            second = self.agent.review(**self.inputs)
            self.assertNotEqual(first["input_hash"], second["input_hash"])
            self.assertEqual(self.llm.tasks.count("publication_preparation"), 2)

    def test_changed_publication_standard_does_not_reuse_cached_pass(self):
        first = self.agent.review(**self.inputs)
        with patch.dict(PREPARATION_CHECKS, {"title_alignment": "新增独立标题误读检查"}):
            with self.assertRaisesRegex(StoryError, "缺少当前版本的发布准备审读"):
                PublicationPreparationAgent(self.project).review(**self.inputs)
            second = self.agent.review(**self.inputs)
            self.assertNotEqual(first["input_hash"], second["input_hash"])
            self.assertEqual(self.llm.tasks.count("publication_preparation"), 2)


if __name__ == "__main__":
    unittest.main()
