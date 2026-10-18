import unittest

from short_fiction_agent.story_studio import plan_patch, plan_reconciliation, setback_review, capability_provenance
from short_fiction_agent.story_studio.contracts import StoryError


class PlanReconciliationTests(unittest.TestCase):
    def test_structural_repair_cannot_pass_by_rebinding_same_plan(self):
        before = {"candidate_hash": "old", "scenes": [{"scene_id": "sc_01", "choice": "仍靠同一办法"}]}
        self.assertFalse(plan_patch.substantive_change(before, {**before, "candidate_hash": "new"}))
        self.assertTrue(plan_patch.substantive_change(before, {**before,
            "scenes": [{"scene_id": "sc_01", "choice": "换了有代价的新办法"}]}))

    def test_capability_review_requires_full_scene_coverage_and_current_quote(self):
        plan = {"scenes": [{"scene_id": "sc_01", "choice": "我借陌生系统协议直接夺回全部权限。"}]}
        issue = {"path": "scenes[0].choice", "reason": "协议没有来源",
                 "missing_link": "建立协议来源", "use_quote": plan["scenes"][0]["choice"]}
        response = {"decision": "revise", "issues": [issue], "scene_ids_checked": ["sc_01"]}
        self.assertEqual(capability_provenance.validate(response, plan)["decision"], "revise")
        with self.assertRaisesRegex(StoryError, "连续原文"):
            capability_provenance.validate({**response, "issues": [{**issue,
                "use_quote": "我借陌生系统协议夺回了所有权限。"}]}, plan)
        with self.assertRaisesRegex(StoryError, "场景覆盖"):
            capability_provenance.validate({**response, "scene_ids_checked": []}, plan)

    def test_patch_path_check_and_whole_fact_list(self):
        plan = {"scenes": [{"choice": "查原始记录"}],
                "continuity_map": {"timeline": ["次日查证"]}}
        self.assertFalse(plan_patch.can_edit_path(plan, "candidate.cost"))
        self.assertFalse(plan_patch.can_edit_path(plan, "scenes[1].choice"))
        self.assertTrue(plan_patch.can_edit_path(plan, "plan.continuity_map.timeline"))
        patched = plan_patch.apply(plan, {"field_edits": [
            {"path": "plan.continuity_map.timeline", "replacement": ["次日查证", "第三日对质"]}]},
            [{"path": "plan.continuity_map.timeline"}])
        self.assertEqual(len(patched["continuity_map"]["timeline"]), 2)

    def test_evidence_must_quote_current_plan_or_author_contract(self):
        plan = {"scenes": [{"consequence": "主角先成为特聘技术顾问并接受调查。",
                            "progression_beats": [{"change": "主角当场成为正式合伙人并掌握股份。"}]}]}
        contract = {"author_input": {"premise": "正式合伙人身份必须在完成注资后获得。"}}
        issue = {"path": "plan.scenes[0].progression_beats[0].change",
                 "reason": "取得身份的时间矛盾", "missing_link": "延后合伙身份",
                 "quote_a": "主角先成为特聘技术顾问并接受调查。",
                 "quote_b": "主角当场成为正式合伙人并掌握股份。"}
        self.assertEqual(plan_reconciliation.validate(
            {"decision": "revise", "issues": [issue]}, plan, contract)["decision"], "revise")
        with self.assertRaisesRegex(StoryError, "连续原文"):
            plan_reconciliation.validate(
                {"decision": "revise", "issues": [{**issue, "quote_b": "主角当场成为皇帝"}]},
                plan, contract)

    def test_patch_must_cover_original_zero_based_path(self):
        plan = {"scenes": [{"consequence": "第一场"}, {"consequence": "第二场"}]}
        issues = [{"path": "plan.scenes[0].consequence"}]
        with self.assertRaisesRegex(StoryError, "逐项处理"):
            plan_patch.apply(plan, {"field_edits": [
                {"path": "plan.scenes[1].consequence", "replacement": "改了第二场"}]}, issues)
        patched = plan_patch.apply(plan, {"field_edits": [
            {"path": "plan.scenes[0].consequence", "replacement": "改了第一场"}]}, issues)
        self.assertEqual(patched["scenes"][0]["consequence"], "改了第一场")
        self.assertEqual(plan["scenes"][0]["consequence"], "第一场")

    def test_explicit_setback_needs_prior_gain_loss_and_new_strategy_quotes(self):
        contract = {"author_input": {"premise": "中段损失一项已得成果，然后改变打法"}}
        self.assertTrue(setback_review.enabled(contract))
        plan = {"scenes": [{"consequence": "主角先获得特聘顾问资格与进入会场的机会。",
                            "choice": "他第一次通过本机底层日志举报代理。"},
                           {"consequence": "对手设局导致主角的顾问资格被暂停，会场通行证失效。",
                            "choice": "主角改用留存的纸质合同和外包签收记录证明掉包。"}]}
        proof = {"prior_gain_quote": plan["scenes"][0]["consequence"],
                 "adversary_loss_quote": plan["scenes"][1]["consequence"],
                 "first_strategy_quote": plan["scenes"][0]["choice"],
                 "changed_strategy_quote": plan["scenes"][1]["choice"]}
        self.assertEqual(setback_review.validate(
            {"decision": "pass", "issues": [], "proof": proof}, plan)["decision"], "pass")
        with self.assertRaisesRegex(StoryError, "原文证明"):
            setback_review.validate(
                {"decision": "pass", "issues": [], "proof": {**proof,
                    "adversary_loss_quote": "主角失去了所有奖励和所有朋友。"}}, plan)

    def test_patch_accepts_plan_prefixed_ending_and_indexed_fact_row(self):
        plan = {"scenes": [], "ending_resolution": "反派当场被抓走",
                "continuity_map": {"timeline": ["主角报案", "反派当场被抓走"]}}
        issues = [{"path": "plan.ending_resolution"},
                  {"path": "plan.continuity_map.timeline[1]"}]
        result = plan_patch.apply(plan, {"field_edits": [
            {"path": "plan.ending_resolution", "replacement": "反派失去合同与客户"},
            {"path": "plan.continuity_map.timeline[1]", "replacement": "资方终止合作"}]}, issues)
        self.assertEqual(result["ending_resolution"], "反派失去合同与客户")
        self.assertEqual(result["continuity_map"]["timeline"][1], "资方终止合作")

    def test_setback_review_normalizes_model_path_only_rejection(self):
        result = setback_review.validate(
            {"decision": "revise", "issues": ["scenes[3].choice"], "proof": {}},
            {"scenes": []})
        self.assertEqual(result["issues"][0]["path"], "scenes[3].choice")
        self.assertIn("对手夺走", result["issues"][0]["missing_link"])
