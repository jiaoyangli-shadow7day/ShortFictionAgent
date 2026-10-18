"""Check real prompt wiring and version invalidation, not model semantic accuracy."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.quality import current_review
from short_fiction_agent.story_studio.selection_review import CAUSAL_RUBRIC
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_opening_binding import OpeningLLM


class CausalGuidanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = ("林禾发现信不见了。"
                     + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 31
                     + "她找回信，也记得代价。")

    def workflow(self, fmt, target, minimum, maximum):
        project = StoryProject(Path(self.tmp.name) / fmt)
        project.create(StoryBrief.from_dict({"review_mode": "deep_review", "format": fmt,
            "length": {"target": target, "min": minimum, "max": maximum},
            "author_input": {"premise": "林禾找信", "locked_facts": []}}))
        llm = OpeningLLM(self.body)
        return project, llm, StoryWorkflow(project, llm)

    def assert_guidance_delivered(self, llm, tasks):
        serialized = json.dumps(CAUSAL_RUBRIC, ensure_ascii=False)
        for task in tasks:
            with self.subTest(task=task):
                matching = [prompt for name, prompt in llm.prompts if name == task]
                self.assertTrue(matching, f"No real workflow invocation of {task}")
                for prompt in matching:
                    self.assertIn(serialized, prompt)

    def test_micro_pipeline_receives_constraints_alternatives_and_motivation_checks(self):
        _, llm, flow = self.workflow("micro_story", 900, 600, 1100)
        flow.develop()
        flow.generate()
        self.assert_guidance_delivered(llm, (
            "story_candidates", "candidate_comparative_review", "opening_candidates",
            "opening_comparative_review", "story_blueprint", "blueprint_audit",
            "write_micro_story", "cold_read_review", "continuity_review", "causal_challenge",
        ))

    def test_short_story_plan_and_preflight_receive_the_same_checks(self):
        _, llm, flow = self.workflow("short_story", 4000, 2500, 5000)
        flow.develop()
        flow.plan()
        self.assert_guidance_delivered(llm, ("story_plan", "blueprint_audit"))

    def test_serial_plan_receives_the_same_checks(self):
        _, llm, flow = self.workflow("short_serial", 18000, 12000, 22000)
        flow.develop()
        flow.plan()
        self.assert_guidance_delivered(llm, ("serial_plan",))

    def test_causal_standard_change_invalidates_selection_and_full_review(self):
        project, _, flow = self.workflow("micro_story", 900, 600, 1100)
        flow.develop()
        flow.generate()
        with patch.dict(CAUSAL_RUBRIC, {"hard_constraints": "修订后的硬约束检查标准"}):
            with self.assertRaisesRegex(StoryError, "候选比较输入已更改"):
                flow._selection()
            with self.assertRaisesRegex(StoryError, "审查标准已更改"):
                current_review(project)


if __name__ == "__main__":
    unittest.main()
