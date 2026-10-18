"""Independent opening comparison rejects malformed inputs and stale evidence."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.selection_review import (
    OPENING_RUBRIC, opening_comparison_input_hash,
    validate_openings, validate_opening_comparison,
)
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_opening_binding import OpeningLLM


class OpeningSelectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / "story")
        self.project.create(StoryBrief.from_dict({"format": "micro_story",
            "length": {"target": 900, "min": 600, "max": 1100},
            "author_input": {"premise": "林禾找信", "locked_facts": []}}))
        self.llm = OpeningLLM("林禾找回了信。")
        self.flow = StoryWorkflow(self.project, self.llm)

    def responses(self):
        openings = json.loads(self.llm.generate("", "opening_candidates"))
        comparison = json.loads(self.llm.generate("", "opening_comparative_review"))
        return openings, comparison

    def prefer_third_opening(self):
        original = self.flow.call_json

        def call(task, prompt):
            result = original(task, prompt)
            if task == "opening_comparative_review":
                result["comparisons"][0]["rank"] = 3
                result["comparisons"][2]["rank"] = 1
                result["recommended_id"] = "opening-2"
            return result

        self.flow.call_json = call

    def test_independent_comparison_overrides_generator_recommendation(self):
        self.prefer_third_opening()
        result = self.flow.develop()
        self.assertEqual(result["selection"]["opening_index"], 2)
        self.assertEqual(result["selection"]["opening_selection_method"], "model_comparison")
        comparison = read_json(self.project.root / "opening_comparison.json")
        self.assertEqual(result["selection"]["opening_comparison_hash"], digest(comparison))
        self.assertEqual(comparison["assessment_scope"], "internal_editorial_only")
        self.assertEqual(comparison["commercial_validation"], "unverified")

    def test_explicit_selection_still_preserves_independent_comparison(self):
        self.prefer_third_opening()
        result = self.flow.develop(opening_index=1)
        self.assertEqual(result["selection"]["opening_index"], 1)
        self.assertEqual(result["selection"]["opening_selection_method"], "explicit_choice")
        comparison = read_json(self.project.root / "opening_comparison.json")
        self.assertEqual(comparison["recommended_id"], "opening-2")
        self.assertEqual(len(comparison["comparisons"]), 3)

    def test_exact_inputs_reuse_comparison_but_changed_candidate_does_not(self):
        self.flow.develop(opening_index=0)
        self.flow.develop(opening_index=1)
        self.assertEqual(self.llm.tasks.count("opening_comparative_review"), 1)
        self.assertEqual(self.llm.tasks.count("opening_candidates"), 1)
        self.flow.develop(candidate_index=2)
        self.assertEqual(self.llm.tasks.count("opening_comparative_review"), 2)
        self.assertEqual(self.llm.tasks.count("opening_candidates"), 2)

    def test_refresh_reassesses_opening_comparison(self):
        self.flow.develop()
        self.flow.develop(refresh=True)
        self.assertEqual(self.llm.tasks.count("opening_comparative_review"), 2)

    def test_openings_require_three_complete_nonempty_unique_items(self):
        response, _ = self.responses()
        validate_openings(response)
        malformed = []
        bad = copy.deepcopy(response); bad["openings"].pop(); malformed.append(bad)
        bad = copy.deepcopy(response); bad["openings"][1] = "正文"; malformed.append(bad)
        for key in ("id", "text", "rationale", "fulfillment_cost"):
            for value in (None, "", " \n", 0):
                bad = copy.deepcopy(response)
                bad["openings"][0][key] = value
                malformed.append(bad)
        bad = copy.deepcopy(response)
        bad["openings"][1]["id"] = bad["openings"][0]["id"]
        malformed.append(bad)
        bad = copy.deepcopy(response)
        bad["openings"][1]["id"] = " " + bad["openings"][0]["id"]
        malformed.append(bad)
        for index, bad in enumerate(malformed):
            with self.subTest(case=index), self.assertRaises(StoryError):
                validate_openings(bad)

    def test_unknown_generator_recommendation_never_falls_back_to_first(self):
        original = self.flow.call_json

        def call(task, prompt):
            response = original(task, prompt)
            if task == "opening_candidates":
                response["recommended_id"] = "unknown"
            return response

        self.flow.call_json = call
        with self.assertRaisesRegex(StoryError, "不存在的候选"):
            self.flow.develop()
        self.assertNotIn("opening_comparative_review", self.llm.tasks)
        self.assertFalse((self.project.root / "selection.json").exists())

    def test_comparison_requires_complete_unique_ranks_and_matching_recommendation(self):
        openings, response = self.responses()
        choices = validate_openings(openings)
        validate_opening_comparison(response, choices)
        malformed = []
        bad = copy.deepcopy(response); bad["comparisons"].pop(); malformed.append(bad)
        for field, value in (("opening_id", "opening-0"), ("rank", 1), ("rank", True),
                             ("strength", ""), ("weakness", " "), ("execution_risk", None)):
            bad = copy.deepcopy(response)
            bad["comparisons"][1][field] = value
            malformed.append(bad)
        for value in ("unknown", "opening-2", None):
            bad = copy.deepcopy(response); bad["recommended_id"] = value; malformed.append(bad)
        for index, bad in enumerate(malformed):
            with self.subTest(case=index), self.assertRaises(StoryError):
                validate_opening_comparison(bad, choices)

    def test_all_openings_and_rubric_are_part_of_comparison_fingerprint(self):
        openings, _ = self.responses()
        choices = validate_openings(openings)
        contract, candidate = {"audience": "学生"}, {"ending": "找回信"}
        baseline = opening_comparison_input_hash(contract, candidate, choices)
        changed = copy.deepcopy(choices); changed[2]["text"] = "未选开篇也变了。"
        for other in (
            opening_comparison_input_hash({"audience": "职场人"}, candidate, choices),
            opening_comparison_input_hash(contract, {"ending": "留下信"}, choices),
            opening_comparison_input_hash(contract, candidate, changed),
        ):
            self.assertNotEqual(baseline, other)
        with patch.dict(OPENING_RUBRIC, {"language": "修订后的语言标准"}):
            self.assertNotEqual(baseline, opening_comparison_input_hash(contract, candidate, choices))

    def test_unchosen_opening_change_invalidates_selection_evidence(self):
        self.flow.develop(opening_index=0)
        path = self.project.root / "openings.json"
        openings = read_json(path)
        openings["openings"][2]["text"] = "未选开篇的内容改变。"
        write_json(path, openings)
        with self.assertRaisesRegex(StoryError, "开篇比较输入"):
            self.flow._selection()

    def test_rubric_change_invalidates_selection_then_reassesses_on_develop(self):
        self.flow.develop()
        with patch.dict(OPENING_RUBRIC, {"language": "修订后的语言标准"}):
            with self.assertRaisesRegex(StoryError, "编辑标准已更改"):
                self.flow._selection()
            self.flow.develop()
            self.assertEqual(self.llm.tasks.count("opening_comparative_review"), 2)
            self.flow._selection()

    def test_modified_valid_comparison_cache_cannot_silently_reselect(self):
        self.flow.develop()
        path = self.project.root / "opening_comparison.json"
        comparison = read_json(path)
        comparison["comparisons"][0]["weakness"] = "外部改动过的评语"
        write_json(path, comparison)
        with self.assertRaisesRegex(StoryError, "开篇比较已更改"):
            self.flow._selection()
        with self.assertRaisesRegex(StoryError, "缓存开篇比较"):
            self.flow.develop()

    def test_missing_comparison_record_or_binding_requires_redevelopment(self):
        self.flow.develop()
        path = self.project.root / "selection.json"
        selection = read_json(path)
        selection.pop("opening_comparison_hash")
        write_json(path, selection)
        with self.assertRaisesRegex(StoryError, "开篇比较已更改或缺失"):
            self.flow._selection()

if __name__ == "__main__":
    unittest.main()
