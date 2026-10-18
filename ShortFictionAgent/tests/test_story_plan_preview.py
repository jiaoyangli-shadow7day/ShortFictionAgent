import argparse
import copy
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import plan_preview
from short_fiction_agent.story_studio.cli import add_parsers, run
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json, write_text
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM


class PlanPreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = "林禾发现信不见了。" + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 31 + "她找回信，也记得代价。"

    def project(self, *, channel="generic_text", format="micro_story", root=None):
        project = StoryProject(Path(root or self.tmp.name) / channel)
        length = ({"target": 8000, "min": 6400, "max": 9600} if format == "short_story"
                  else {"target": 900, "min": 600, "max": 1100})
        project.create(StoryBrief.from_dict({"title": "找信", "format": format, "channels": [channel],
                                           "length": length,
                                           "author_input": {"premise": "林禾找回一封信"}}))
        llm = ScriptedLLM(self.body)
        workflow = StoryWorkflow(project, llm)
        workflow.develop()
        return project, workflow, llm

    def parser(self):
        parser = argparse.ArgumentParser()
        add_parsers(parser.add_subparsers(dest="command"))
        return parser

    def cli_plan(self, project, workflow):
        output = io.StringIO()
        with patch("short_fiction_agent.story_studio.cli._workflow", return_value=workflow), redirect_stdout(output):
            code = run(self.parser().parse_args(["plan", "--project", str(project.root)]))
        self.assertEqual(code, 0)
        return json.loads(output.getvalue())

    def test_fanqie_page_uses_bound_entry_fields_and_existing_scenes_without_model_calls(self):
        project, workflow, llm = self.project(channel="fanqie_short")
        plan = workflow.plan()
        candidate, _ = workflow._selection()
        before = list(llm.tasks)
        with patch.object(StoryWorkflow, "call_json", side_effect=AssertionError("preview must not call model")):
            result = plan_preview.save(project, plan)
        self.assertEqual(before, llm.tasks)
        self.assertEqual(result["working_title"], candidate["working_title"])
        self.assertEqual(result["provisional_lead"]["text"], candidate["provisional_lead"]["text"])
        self.assertEqual(result["read_question"], plan["main_question"])
        self.assertEqual(result["turns"], candidate["turns"])
        self.assertEqual(result["ending_payoff"], candidate["ending_payoff"])
        self.assertEqual(result["scenes"], plan["scenes"])
        self.assertEqual(result["publication"]["route_id"], "fanqie_app_reading")
        text = Path(result["paths"]["text"]).read_text(encoding="utf-8")
        for label in ("工作标题", "具体追读问题", "人物欲望", "三次转折", "结尾回报", "场景摘要", "投稿路线"):
            self.assertIn(label, text)
        self.assertIn("不能作为正式投稿导语", text)
        self.assertIn("不预测结果", text)
        self.assertIsNone(read_json(project.root / "project.json")["active_artifact_id"])
        self.assertFalse((project.root / "drafts").exists())

    def test_generic_page_uses_selected_opening_and_does_not_invent_three_turns(self):
        project, workflow, _ = self.project()
        plan = workflow.plan()
        candidate, opening = workflow._selection()
        result = plan_preview.save(project, plan)
        self.assertEqual(result["working_title"], "找信")
        self.assertEqual(result["provisional_lead"]["text"], opening["text"])
        self.assertEqual(result["read_question"], plan["main_question"])
        self.assertEqual(result["character_desire"]["goal"], candidate["goal"])
        self.assertEqual(result["turns"], [])
        self.assertTrue(result["missing_preview_fields"])
        self.assertEqual(result["publication"]["name"], "通用文本")

    def test_save_reuses_unchanged_page_and_repairs_only_preview_text(self):
        project, workflow, llm = self.project()
        plan = workflow.plan()
        first = plan_preview.save(project, plan)
        paths = [Path(first["paths"][key]) for key in ("text", "json")]
        times = [path.stat().st_mtime_ns for path in paths]
        tasks = list(llm.tasks)
        second = plan_preview.save(project, plan)
        self.assertTrue(second["cache_reused"])
        self.assertEqual(times, [path.stat().st_mtime_ns for path in paths])
        self.assertEqual(tasks, llm.tasks)
        write_text(paths[0], "外部改动")
        repaired = plan_preview.current(project)
        self.assertTrue(repaired["cache_reused"])
        self.assertIn("故事方案", paths[0].read_text(encoding="utf-8"))
        self.assertEqual(tasks, llm.tasks)

    def test_cli_plan_stops_before_body_and_reuses_micro_proposal_without_new_calls(self):
        project, workflow, llm = self.project()
        result = self.cli_plan(project, workflow)
        self.assertEqual(result["stage"], "plan_only")
        self.assertTrue(Path(result["proposal_text"]).is_file())
        self.assertTrue(Path(result["proposal_json"]).is_file())
        self.assertEqual(llm.tasks.count("story_blueprint"), 1)
        self.assertEqual(llm.tasks.count("blueprint_audit"), 1)
        self.assertFalse(any(task.startswith("write_") for task in llm.tasks))
        before = list(llm.tasks)
        output = io.StringIO()
        with patch("short_fiction_agent.story_studio.cli._workflow", side_effect=AssertionError("cached preview needs no model")), redirect_stdout(output):
            code = run(self.parser().parse_args(["plan", "--project", str(project.root)]))
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.getvalue())["cache_reused"])
        self.assertEqual(before, llm.tasks)

    def test_short_story_cli_uses_existing_planner_cache_on_repeat(self):
        project, workflow, llm = self.project(format="short_story")
        self.cli_plan(project, workflow)
        before = list(llm.tasks)
        result = self.cli_plan(project, workflow)
        self.assertEqual(before, llm.tasks)
        self.assertTrue(result["cache_reused"])
        self.assertEqual(llm.tasks.count("story_plan"), 1)

    def test_modified_contract_candidate_opening_or_comparison_cannot_reuse_page(self):
        for target in ("contract", "candidate", "opening", "comparison", "selection"):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as root:
                project, workflow, _ = self.project(root=root)
                plan = workflow.plan()
                plan_preview.save(project, plan)
                if target == "contract":
                    brief = project.brief()
                    brief.title = "另一标题"
                    import yaml
                    write_text(project.brief_path, yaml.safe_dump(brief.to_dict(), allow_unicode=True))
                else:
                    filename = {"candidate": "candidates.json", "opening": "openings.json",
                                "comparison": "candidate_comparison.json", "selection": "selection.json"}[target]
                    value = read_json(project.root / filename)
                    if target == "candidate":
                        value["candidates"][0]["goal"] = "改成另一目标"
                    elif target == "opening":
                        value["openings"][0]["text"] = "另一开头"
                    elif target == "comparison":
                        value["comparisons"][0]["strength"] = "另一判断"
                    else:
                        value["candidate_hash"] = "错误绑定"
                    write_json(project.root / filename, value)
                with self.assertRaises(StoryError):
                    plan_preview.current(project)

    def test_wrong_plan_bindings_and_unstored_plan_are_rejected(self):
        project, workflow, _ = self.project()
        plan = workflow.plan()
        for key in ("contract_hash", "candidate_hash", "opening_hash", "selection_hash"):
            with self.subTest(key=key):
                bad = {**plan, key: "stale"}
                with self.assertRaises(StoryError):
                    plan_preview.save(project, bad)
        bad = copy.deepcopy(plan)
        bad["ending_resolution"] = "未审查的新结局"
        with self.assertRaisesRegex(StoryError, "已保存"):
            plan_preview.save(project, bad)

    def test_short_plan_preflight_receipt_cannot_be_relabelled_pass(self):
        project, workflow, _ = self.project(format="short_story")
        plan = workflow.plan()
        receipt = read_json(project.root / "plan" / "preflight_receipt.json")
        receipt["plan_hash"] = "another-plan"
        write_json(project.root / "plan" / "preflight_receipt.json", receipt)
        with self.assertRaisesRegex(StoryError, "预审记录"):
            plan_preview.save(project, plan)

    def test_edited_json_preview_is_rebuilt_from_bound_sources_without_new_model_calls(self):
        project, workflow, llm = self.project()
        plan = workflow.plan()
        saved = plan_preview.save(project, plan)
        value = read_json(project.root / "plan" / "proposal.json")
        value["working_title"] = "伪造的标题"
        write_json(project.root / "plan" / "proposal.json", value)
        before = list(llm.tasks)
        repaired = plan_preview.current(project)
        self.assertEqual(repaired["working_title"], saved["working_title"])
        self.assertEqual(before, llm.tasks)

    def test_generate_after_preview_proceeds_without_global_author_approval_state(self):
        project, workflow, _ = self.project()
        self.cli_plan(project, workflow)
        output = io.StringIO()
        with patch("short_fiction_agent.story_studio.cli._workflow", return_value=workflow), redirect_stdout(output):
            code = run(self.parser().parse_args(["generate", "--project", str(project.root)]))
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["decision"], "passed")
        self.assertEqual(project.active_draft()[0]["state"], "passed")

    def test_legacy_main_dispatches_plan(self):
        project, workflow, _ = self.project()
        from short_fiction_agent.cli import main
        output = io.StringIO()
        with patch("sys.argv", ["short-fiction-agent", "plan", "--project", str(project.root)]), \
             patch("short_fiction_agent.story_studio.cli._workflow", return_value=workflow), redirect_stdout(output):
            with self.assertRaises(SystemExit) as caught:
                main()
        self.assertEqual(caught.exception.code, 0)
        self.assertEqual(json.loads(output.getvalue())["stage"], "plan_only")


if __name__ == "__main__":
    unittest.main()
