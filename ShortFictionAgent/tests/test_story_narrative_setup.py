"""A missing motive cannot be waived by a high score or later payoff."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio import narrative_setup as setup
from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.quality import StoryReviewer, review_decision
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from test_story_studio import ScriptedLLM


class NarrativeSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / "story")
        self.project.create(StoryBrief.from_dict({"review_mode": "deep_review", "format": "micro_story",
            "length": {"target": 900, "min": 600, "max": 1100},
            "research_preferences": {"reader_tags": ["重生", "婚姻家庭", "女性成长"]},
            "author_input": {"premise": "女儿重生回退休宴，改变妈妈的晚年", "locked_facts": []}}))
        self.body = "我妈退休那天，弟弟让我妈拿存折。\n\n" + "我按住她的手，不想再劝她忍了。" * 50
        self.project.save_draft(self.body)
        self.llm = ScriptedLLM(self.body)

    def reviewer(self, verdict="supported", omit=False):
        seen = []
        def call(task, prompt):
            seen.append((task, prompt))
            response = json.loads(self.llm.generate(prompt, task))
            if task == "cold_read_review" and not omit:
                data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"audience":'):])[0]
                response["narrative_checks"] = [dict(id=item["id"],
                    verdict=verdict if item["id"] == "return_anchor" else "supported",
                    reason="未交代回到哪一刻" if verdict != "supported" else "测试桩确认依据",
                    fix_intent="补齐回到现场的过程", evidence_paragraph_indices=[0])
                    for item in data["narrative_requirements"]]
            if task == "continuity_review":
                response["dimensions"] = {key: 4 for key in response["dimensions"]}
            return response
        return StoryReviewer(self.project, call), seen

    def test_missing_return_blocks_even_with_full_scores_and_no_other_issues(self):
        reviewer, _ = self.reviewer("missing")
        report = reviewer.review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertIn("narrative_setup:return_anchor", report["blockers"])
        self.assertTrue(all(value == 4 for value in report["dimensions"].values()))
        self.assertTrue(any(i.get("origin") == "narrative_setup" and i["severity"] == "blocker"
                            for i in report["issues"]))
        # Even a later summary that drops issues cannot erase this failed check.
        report["issues"] = []
        self.assertEqual(review_decision(self.project.active_draft()[1], self.project.contract(), report)["decision"],
                         "revise_required")
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export("fanqie_short")

    def test_omitted_checks_are_integrity_failure(self):
        reviewer, _ = self.reviewer(omit=True)
        report = reviewer.review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertTrue(any(i["stage"] == "narrative_setup" for i in report["review_integrity_errors"]))

    def test_grounded_checks_are_saved_with_exact_opening_quotes(self):
        reviewer, seen = self.reviewer()
        report = reviewer.review()
        self.assertEqual(report["decision"], "passed")
        for row in report["narrative_setup"]["checks"]:
            self.assertEqual(row["quotes"], [self.body.splitlines()[0]])
        prompt = next(p for t, p in seen if t == "cold_read_review")
        self.assertIn('"opening_paragraphs"', prompt)
        self.assertNotIn('"continuity_map"', prompt)
        full_prompt = next(p for t, p in seen if t == "continuity_review")
        self.assertNotIn('"continuity_map"', full_prompt)

    def test_later_evidence_duplicate_missing_and_bool_indices_are_rejected(self):
        expected = setup.requirements(self.project.contract())
        rows = [dict(id=item["id"], verdict="supported", reason="依据", fix_intent="保留",
                     evidence_paragraph_indices=[0]) for item in expected]
        for kind in ("later", "boolean", "empty", "duplicate", "omitted"):
            bad = copy.deepcopy(rows)
            if kind == "later": bad[0]["evidence_paragraph_indices"] = [2]
            if kind == "boolean": bad[0]["evidence_paragraph_indices"] = [True]
            if kind == "empty": bad[0]["evidence_paragraph_indices"] = []
            if kind == "duplicate": bad[1]["id"] = bad[0]["id"]
            if kind == "omitted": bad.pop()
            with self.subTest(kind=kind), self.assertRaises(StoryError):
                setup.validate_checks(bad, expected, ["开头原文"])

    def test_transmigration_is_not_forced_into_previous_life_victimhood(self):
        contract = {"author_input": {"premise": "穿越到另一个世界，先找住处"}}
        self.assertEqual([r["id"] for r in setup.requirements(contract)],
                         ["arrival_anchor", "changed_choice"])
        self.assertEqual(setup.requirements({"author_input": {"premise": "母女开店"},
                                           "constraints": {"avoid": ["重生", "穿越"]}}), [])

    def test_negative_genre_mentions_in_realistic_premise_do_not_require_rebirth(self):
        for premise in (
            "1997年下岗工人创业。不加重生、系统、预知股价和隐身富豪。",
            "普通工人自救，不用重生或穿越，结尾发下工资。",
            "现实故事，无重生设定。不是穿越故事。",
            "考公青年创业，四候选必须保留现实前提，不换成重生豪门。",
            "女主去杭州创业，不改成穿越故事。",
        ):
            with self.subTest(premise=premise):
                self.assertEqual(setup.requirements({'author_input': {'premise': premise}}), [])

    def test_negative_clause_does_not_hide_later_affirmative_genre(self):
        self.assertEqual([r['id'] for r in setup.requirements(
            {'author_input': {'premise': '不换成重生豪门，但女主穿越到古代。'}})],
            ['arrival_anchor', 'changed_choice'])
        self.assertEqual([r['id'] for r in setup.requirements(
            {'author_input': {'premise': '不用重生，但主角穿越到古代。'}})],
            ['arrival_anchor', 'changed_choice'])
        self.assertEqual([r['id'] for r in setup.requirements(
            {'author_input': {'premise': '女儿重生回退休宴，不写复仇或惨死。'}})],
            ['past_consequence', 'return_anchor', 'changed_choice'])
        self.assertEqual([r['id'] for r in setup.requirements(
            {'research_preferences': {'reader_tags': ['重生']},
             'author_input': {'premise': '不写复仇。'}})],
            ['past_consequence', 'return_anchor', 'changed_choice'])

    def test_draft_submission_keeps_tags_category_revision_and_file_hashes(self):
        package = StoryExporter(self.project).export("fanqie_short", draft=True, category="婚姻家庭")
        info = read_json(package / "submission_info.json")
        self.assertEqual(info["delivery_status"], "review_copy")
        self.assertIsNone(info["review_id"])
        self.assertIn("不得作为投稿成品", info["delivery_presentation"]["instruction"])
        self.assertIn("审阅草稿", (package / "submission_info.txt").read_text())
        self.assertEqual(info["recommended_tags"], ["重生", "婚姻家庭", "女性成长"])
        self.assertEqual(info["recommended_category"], "婚姻家庭")
        self.assertEqual(info["source_revision"], 1)
        self.assertEqual(info["source_content_hash"], self.project.active_draft()[0]["content_hash"])
        manifest = read_json(package / "manifest.json")
        self.assertEqual(manifest["delivery_status"], "review_copy")
        for name in ("submission_info.txt", "submission_info.json"):
            self.assertEqual(manifest["files_sha256"][name], digest((package / name).read_text()))
        self.assertEqual(read_json(package / "metadata.json")["publication_tags"], info["recommended_tags"])


if __name__ == "__main__":
    unittest.main()
