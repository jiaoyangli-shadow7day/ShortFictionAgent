"""Editorial mechanics only: these tests cannot measure human reader retention."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError
from short_fiction_agent.story_studio.engagement import (
    PROTOCOL, gate, validate_confirmations, validate_observations,
    validate_scene_beats, windows,
)
from short_fiction_agent.story_studio.quality import (
    StoryReviewer, current_review, review_decision, review_spans,
)
from short_fiction_agent.story_studio.storage import StoryProject, read_json, write_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, structure_fixture, reader_experience_fixture, reader_basis_fixture


def payload(prompt, key):
    return json.JSONDecoder().raw_decode(prompt[prompt.index('{"' + key + '":'):])[0]


def observation(w, verdict="flowing"):
    return {"window_id": w["window_id"], "verdict": verdict,
            "reader_question": "她会如何回应", "change": "把热粥留给对方，信任得到回应",
            "reward": "此前的照顾得到双向回应", "next_expectation": "两人如何共同面对代价",
            "reason": "关系改变或有待全文核对", "fix_intent": "保留有效互动，压缩重复解释",
            "evidence_paragraph_indices": [w["paragraph_start"]]}


class EngagementValidationTests(unittest.TestCase):
    def setUp(self):
        self.body = "\n\n".join(f"段{i}：" + "人物动作。" * 150 for i in range(5))
        self.windows = windows(self.body)

    def observations(self, verdict="flowing"):
        return validate_observations({"progression_observations": [observation(w, verdict)
            for w in self.windows]}, self.windows, self.body)

    def test_complete_paragraph_windows_cover_whitespace_and_long_paragraphs(self):
        text = "\n  \n" + "长段" * 900 + "\n\n   \n末段\n"
        grouped = windows(text, offset=20)
        self.assertEqual(len(grouped), 2)
        self.assertEqual(grouped[0]["paragraph_end"], 1)
        self.assertEqual(grouped[0]["span"][0], 20)
        self.assertEqual(grouped[-1]["span"][1], 20 + len(text))
        self.assertEqual(grouped[0]["span"][1], grouped[1]["span"][0])
        normalized = validate_observations({"progression_observations": [observation(w)
            for w in grouped]}, grouped, text)
        self.assertEqual(normalized[0]["evidence"][0]["quote"], "长段" * 900)
        self.assertEqual(normalized[0]["evidence"][0]["span"][0], 24)

    def test_missing_duplicate_and_malformed_window_ids_are_rejected(self):
        rows = [observation(w) for w in self.windows]
        for broken in (rows[:-1], rows + rows[:1], [{**rows[0], "window_id": []}] + rows[1:]):
            with self.subTest(broken=broken), self.assertRaises(StoryError):
                validate_observations({"progression_observations": broken}, self.windows, self.body)

    def test_window_evidence_cannot_use_future_or_boolean_paragraph_indices(self):
        for index in (self.windows[0]["paragraph_end"], True):
            rows = [observation(w) for w in self.windows]
            rows[0]["evidence_paragraph_indices"] = [index]
            with self.subTest(index=index), self.assertRaisesRegex(StoryError, "当前窗口"):
                validate_observations({"progression_observations": rows}, self.windows, self.body)

    def test_invalid_evidence_error_gives_exact_window_and_allowed_indices(self):
        rows = [observation(w) for w in self.windows]
        rows[1]["evidence_paragraph_indices"] = [999]
        w = self.windows[1]
        with self.assertRaises(StoryError) as caught:
            validate_observations({"progression_observations": rows}, self.windows, self.body)
        message = str(caught.exception)
        self.assertIn(w["window_id"], message)
        self.assertIn(f"允许编号 {w['paragraph_start']} 至 {w['paragraph_end'] - 1}", message)
        self.assertIn("收到 [999]", message)

    def test_future_climax_cannot_resolve_an_earlier_stall(self):
        observations = self.observations()
        observations[0]["verdict"] = "stalled"
        row = {"window_id": observations[0]["window_id"], "verdict": "context_resolves",
               "reason": "结尾有高潮", "fix_intent": "保留", "paragraph_index": 4}
        with self.assertRaisesRegex(StoryError, "后文高潮"):
            validate_confirmations({"progression_confirmations": [row]}, observations, self.body)

    def test_stall_confirmation_must_quote_its_own_window(self):
        observations = self.observations()
        observations[1]["verdict"] = "stalled"
        row = {"window_id": observations[1]["window_id"], "verdict": "stalled",
               "reason": "重复解释", "fix_intent": "压缩", "paragraph_index": 0}
        with self.assertRaises(StoryError):
            validate_confirmations({"progression_confirmations": [row]}, observations, self.body)
        for rows in ([], [row, row], [{**row, "window_id": []}]):
            with self.subTest(rows=rows), self.assertRaises(StoryError):
                validate_confirmations({"progression_confirmations": rows}, observations, self.body)

    def test_quiet_relationship_reward_can_resolve_a_suspect(self):
        observations = self.observations()
        observations[0]["verdict"] = "unclear"
        confirmations = validate_confirmations({"progression_confirmations": [{
            "window_id": observations[0]["window_id"], "verdict": "context_resolves",
            "reason": "留热粥回应了此前的关心，信任从单向变成双向", "fix_intent": "保留",
            "paragraph_index": 0}]}, observations, self.body)
        report = {"continuous_pull": {"protocol": PROTOCOL,
                  "observations": observations, "confirmations": confirmations}}
        self.assertEqual(gate(self.body, report), [])
        confirmations[0]["verdict"] = "stalled"
        self.assertEqual(gate(self.body, report), ["continuous_pull:w0:stalled"])
        confirmations[0]["verdict"] = "unclear"
        self.assertEqual(gate(self.body, report), ["continuous_pull:w0:unclear"])

    def test_persisted_coverage_and_evidence_cannot_be_deleted_or_forged(self):
        record = {"protocol": PROTOCOL, "observations": self.observations(), "confirmations": []}
        variants = []
        bad = copy.deepcopy(record); bad["observations"].pop(); variants.append(bad)
        bad = copy.deepcopy(record); bad["observations"][0]["evidence"][0]["quote"] = "编造引文"; variants.append(bad)
        bad = copy.deepcopy(record); bad["observations"][1]["span"][0] += 1; variants.append(bad)
        bad = copy.deepcopy(record); bad["observations"][0]["verdict"] = "stalled"; variants.append(bad)
        for item in variants:
            with self.subTest(item=item), self.assertRaises(StoryError):
                gate(self.body, {"continuous_pull": item})

    def test_scene_beats_require_real_fields_and_reject_identical_duplicates(self):
        beat = {"reader_question": "她是否信任他", "action": "留下自己的口粮",
                "change": "照顾变成双向", "reward": "关系回应", "next_expectation": "共同面对困难"}
        validate_scene_beats({"scenes": [{"progression_beats": [beat]}]})
        for scene in ({}, {"progression_beats": [beat, beat]},
                      {"progression_beats": [{**beat, "change": ""}]},
                      {"progression_beats": [beat] * 7}):
            with self.subTest(scene=scene), self.assertRaises(StoryError):
                validate_scene_beats({"scenes": [scene]})

    def test_distinct_concrete_beats_are_not_truncated_at_six(self):
        beats = [{"reader_question": f"第{i}个选择对方是否接受", "action": f"递出第{i}张邀请",
                  "change": f"第{i}个原先未定的约定落实", "reward": f"第{i}次实际回应",
                  "next_expectation": f"由第{i}个约定接出下一步"} for i in range(7)]
        source = copy.deepcopy(beats)
        validate_scene_beats({"scenes": [{"progression_beats": beats}]})
        self.assertEqual(beats, source)


class EngagementWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = StoryProject(Path(self.tmp.name) / "story")
        self.project.create(StoryBrief.from_dict({"review_mode": "deep_review", "format": "short_story", "genre": "甜宠",
            "length": {"target": 3200, "min": 2500, "max": 4000},
            "author_input": {"premise": "相互照顾之后，两人决定共同面对困难", "locked_facts": []}}))
        self.body = "\n\n".join(f"段{i}：" + "她把热粥留给他，他决定陪她去面对困难。" * 43 for i in range(4))

    def reviewer(self, verdict="stalled", *, omit=False):
        seen = []

        def call(task, prompt):
            seen.append((task, prompt))
            if task == 'reader_experience_review':
                data = payload(prompt, 'protocol')
                return reader_experience_fixture(data['reader_windows'])
            if task == 'reader_basis_grounding':
                data = payload(prompt, 'protocol')
                return reader_basis_fixture(data['reader_windows'], data['reader_basis'])
            if task == "cold_read_review":
                data = payload(prompt, "audience")
                response = {"issues": [], "reader_question": "如何回应", "want_to_continue": True,
                    "progression_observations": [observation(w, "stalled")
                        for w in data["progression_windows"]]}
                if omit:
                    response.pop("progression_observations")
                return response
            if task == "continuity_review":
                data = payload(prompt, "contract")
                from short_fiction_agent.story_studio.quality import DIMENSIONS
                return {"dimensions": {k: 4 for k in DIMENSIONS},
                    "dimension_evidence": {k: "测试桩给出的高分理由" for k in DIMENSIONS}, "issues": [],
                    "progression_confirmations": [{"window_id": w["window_id"], "verdict": verdict,
                        "reason": "连续重复同一段解释，选择没有落实" if verdict == "stalled" else "双向照顾改变信任",
                        "fix_intent": "用一次回应替换反复解释" if verdict == "stalled" else "保留",
                        "paragraph_index": data["paragraphs"].index(w["evidence"][0]["quote"])}
                        for w in data["progression_suspects"]]}
            if task == "causal_challenge":
                return {"issues": []}
            if task == 'whole_story_review':
                return structure_fixture(payload(prompt, 'contract')['paragraphs'])
            if task == 'whole_story_challenge':
                return {'claims':[]}
            raise AssertionError(task)

        self.project.save_draft(self.body)
        return StoryReviewer(self.project, call), seen

    def test_high_scores_cannot_erase_confirmed_stalls(self):
        reviewer, seen = self.reviewer()
        report = reviewer.review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertTrue(all(score == 4 for score in report["dimensions"].values()))
        self.assertTrue(any(b.startswith("continuous_pull:") for b in report["blockers"]))
        issues = [x for x in report["issues"] if x.get("origin") == "continuous_pull"]
        self.assertEqual(len(issues), len(windows(self.project.active_draft()[1])))
        self.assertTrue(all(x["severity"] == "blocker" and x["quote"] in self.body for x in issues))
        self.assertEqual([task for task, _ in seen], ["reader_experience_review", "reader_basis_grounding", "cold_read_review",
            "continuity_review", "causal_challenge", "whole_story_challenge", "whole_story_review"])

    def test_missing_editorial_observations_fail_closed(self):
        reviewer, _ = self.reviewer(omit=True)
        report = reviewer.review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertTrue(report["review_integrity_errors"])
        self.assertTrue(any(b.startswith("review_integrity:") for b in report["blockers"]))

    def test_completed_invalid_evidence_is_reassessed_once_with_original_record(self):
        reviewer, _ = self.reviewer("context_resolves")
        original = reviewer.call_json
        cold_calls = 0
        def call(task, prompt):
            nonlocal cold_calls
            response = original(task, prompt)
            if task == "cold_read_review":
                cold_calls += 1
                data = payload(prompt, "audience")
                for window in data['progression_windows']:
                    self.assertEqual([row['paragraph_index'] for row in window['evidence_paragraphs']],
                                     list(range(window['paragraph_start'],window['paragraph_end'])))
                    for row in window['evidence_paragraphs']:
                        self.assertEqual(row['text'],data['paragraphs'][row['paragraph_index']])
                if cold_calls == 1:
                    response['progression_observations'][0]['evidence_paragraph_indices'] = [True]
            return response
        reviewer.call_json = call
        self.assertEqual(reviewer.review()['decision'],'passed')
        self.assertEqual(cold_calls,2)
        errors=list((self.project.root/'invalid_responses').glob('*.json'))
        self.assertEqual(len(errors),1)
        self.assertEqual(read_json(errors[0])['response']['progression_observations'][0]['evidence_paragraph_indices'],[True])

    def test_transport_error_is_not_retried_as_schema_repair(self):
        reviewer, _ = self.reviewer()
        tasks=[]
        def call(task,prompt):
            tasks.append(task)
            raise RuntimeError('unknown CLI outcome')
        reviewer.call_json=call
        with self.assertRaisesRegex(RuntimeError,'unknown CLI'):
            reviewer.review()
        self.assertEqual(tasks,['reader_experience_review'])
        self.assertEqual(self.project.active_draft()[0]['state'],'review_error')

    def test_resolved_quiet_reward_passes_but_deleted_checks_do_not(self):
        reviewer, _ = self.reviewer("context_resolves")
        report = reviewer.review()
        self.assertEqual(report["decision"], "passed")
        self.assertEqual(current_review(self.project)["review_id"], report["review_id"])
        report.pop("continuous_pull")
        path = self.project.root / "reviews" / (report["review_id"] + ".json")
        write_json(path, report)
        with self.assertRaisesRegex(StoryError, "持续吸引力"):
            current_review(self.project)

    def test_later_batches_receive_prior_context_and_cover_every_character(self):
        self.body = "\n\n".join(f"段{i}：" + "她回应了关心。" * 140 for i in range(8))
        reviewer, seen = self.reviewer("context_resolves")
        report = reviewer.review()
        cold = [payload(prompt, "audience") for task, prompt in seen if task == "cold_read_review"]
        body = self.project.active_draft()[1]
        spans = review_spans(body)
        self.assertEqual(len(cold), len(spans))
        self.assertGreater(len(cold), 1)
        for data, span in zip(cold, spans):
            self.assertEqual(data["previous_text"], body[:span[0]])
        observations = report["continuous_pull"]["observations"]
        self.assertEqual(observations[0]["span"][0], 0)
        self.assertEqual(observations[-1]["span"][1], len(body))
        self.assertEqual(gate(body, report), [])

    def test_generation_repairs_stall_and_reviews_changed_text_again(self):
        class RepairLLM(ScriptedLLM):
            def __init__(self, body):
                super().__init__(body)
                self.prompts = []

            def generate(self, prompt, task_type):
                self.prompts.append((task_type, prompt))
                if task_type == "story_edit":
                    data = payload(prompt, "contract")
                    if any(x.get("origin") == "continuous_pull" for x in data["review_issues"]):
                        return json.dumps({"back_to_plan": False, "edits": [{
                            "quote": "待修复", "replacement": "已回应", "kind": "rewrite",
                            "benefit": "人物落实选择", "risk": "保持原有因果"}]}, ensure_ascii=False)
                result = json.loads(super().generate(prompt, task_type)) if task_type in {
                    "cold_read_review", "continuity_review"} else super().generate(prompt, task_type)
                if task_type == "cold_read_review":
                    data = payload(prompt, "audience")
                    if "待修复" in "".join(data["paragraphs"]):
                        result["progression_observations"][0]["verdict"] = "stalled"
                if task_type == "continuity_review":
                    data = payload(prompt, "contract")
                    for confirmation in result["progression_confirmations"]:
                        confirmation.update(verdict="stalled", reason="只有解释，人物还未落实选择",
                                            fix_intent="用行动回应期待")
                return json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else result

        llm = RepairLLM("待修复。\n" + self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report["decision"], "passed")
        _, final_body = self.project.active_draft()
        self.assertIn("已回应", final_body)
        self.assertNotIn("待修复", final_body)
        reports = [read_json(p) for p in (self.project.root / "reviews").glob("*.json")]
        failed = next(r for r in reports if r["decision"] != "passed")
        with self.assertRaisesRegex(StoryError, "版本不匹配"):
            review_decision(final_body, self.project.contract(), failed)
        self.assertEqual(llm.tasks.count("cold_read_review"), 2)
        plan_prompt = next(p for t, p in llm.prompts if t == "story_plan")
        write_prompt = next(p for t, p in llm.prompts if t == "write_short_story")
        self.assertIn("progression_beats", plan_prompt)
        self.assertIn("执行progression_beats", write_prompt)
        self.assertTrue(all("李卫东" not in p and "沈清荷" not in p for _, p in llm.prompts))

    def test_missing_beats_in_a_legacy_plan_cannot_bypass_writer(self):
        llm = ScriptedLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        plan = flow.plan()
        plan["scenes"][0].pop("progression_beats")
        before = len(llm.tasks)
        with self.assertRaisesRegex(StoryError, "progression_beats"):
            flow._draft_text(plan)
        self.assertEqual(len(llm.tasks), before)

    def test_unrepaired_stall_cannot_receive_generation_approval(self):
        llm = ScriptedLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        call = flow.reviewer.call_json

        def stalled(task, prompt):
            result = call(task, prompt)
            if task == "cold_read_review":
                result["progression_observations"][0]["verdict"] = "stalled"
            if task == "continuity_review":
                for row in result["progression_confirmations"]:
                    row.update(verdict="stalled", reason="重复解释没有落实选择", fix_intent="改写回应")
            return result

        flow.reviewer.call_json = stalled
        report = flow.generate()
        self.assertEqual(report["decision"], "revise_required")
        record, _ = self.project.active_draft()
        self.assertEqual(record["state"], "needs_author")
        self.assertFalse((self.project.root / "generation_receipt.json").exists())
        with self.assertRaises(StoryError):
            current_review(self.project)


if __name__ == "__main__":
    unittest.main()
