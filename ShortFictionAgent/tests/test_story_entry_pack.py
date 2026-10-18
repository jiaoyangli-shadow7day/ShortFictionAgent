"""Entry selection preserves model vetoes and verifies their original-text anchors."""
import copy
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import entry_pack
from short_fiction_agent.story_studio.contracts import StoryError
from short_fiction_agent.story_studio.selection_review import require_feasible_choice


def candidate(identifier="entry-1", *, weak=False):
    if weak:
        goal = "双方正确理解对方，完成沟通。"
        first = "我们坐下来讨论了一份生活安排。"
        event = "两人按顺序确认已有安排，没有人想争取或失去什么。"
        turns = [{"event": f"第{i + 1}次确认安排", "before": "双方没有异议。",
                  "after": "双方仍没有异议。", "new_action": "确认下一条安排。"}
                 for i in range(3)]
        question = "他们如何按步骤完成沟通？"
        payoff = "两人完成讨论，理解了沟通很重要。"
        title = "我们终于把安排说清了"
    else:
        goal = "我想被她主动选一次，保住两人约好的最后一个排练名额。"
        first = "她把唯一的双人排练名额给了我，还在搭档栏填了别人的名字。"
        event = "她递来已经签过的报名表，要我今晚带她哥哥登台。"
        turns = [
            {"event": "我当场签自己的搭档名字", "before": "她以为我会照表替哥哥排练。",
             "after": "她发现我选的是隔壁组的女孩，当众要求换回。", "new_action": "我请她自己说清想和谁跳。"},
            {"event": "她抢过表格却划掉自己的名字", "before": "她可以只靠换表保住面子。",
             "after": "她放弃原有名额，要与我重新参加双人选拔。", "new_action": "我答应同她从头排练。"},
            {"event": "选拔前哥哥临时要她替补", "before": "重新报名只需一起练舞。",
             "after": "她当着哥哥拒绝替补，先走上与我约好的舞台。", "new_action": "我把等她的位置留在自己身边。"},
        ]
        question = "她会亲口说想选我，还是继续让我替别人站台？"
        payoff = "她先把手递给我，报名表搭档栏亲手写下我的名字，一起走上舞台。"
        title = "她给我唯一的名额，搭档却写了别人"
    return {"id": identifier, "protagonist": "排练社的我", "goal": goal,
            "obstacle": "她把实际选择藏在分配表里。" if not weak else "讨论事项需要依次确认。",
            "cost": "错过唯一的排练名额。" if not weak else "花费一晚讨论。",
            "unique_element": "具体报名动作改变双方的选择。" if not weak else "围着生活安排聊天。",
            "core_scene": event, "ending": payoff, "promise": question,
            "weakness": "后续关系选择须在当场回应中出现。", "viable": True,
            "working_title": title, "provisional_lead": {
                "first_sentence": first, "concrete_event": event, "text": first + event},
            "read_question": question, "turns": turns, "ending_payoff": payoff}


def candidates(*, weak=False):
    return [candidate(f"entry-{i + 1}", weak=weak) for i in range(4)]


def editorial_row(option, rank, *, accept=True):
    """Recorded reviewer response, not a keyword-based substitute for that reviewer."""
    def check(fields, basis, passed=accept):
        sources = entry_pack._source_fields(option)
        return {"decision": "pass" if passed else "reject", "basis": basis,
                "evidence": [{"field": field, "quote": sources[field]} for field in fields]}
    entry = {
        "decision": "pass" if accept else "reject",
        "click": check(["working_title", "provisional_lead.first_sentence"],
                       "报名表把两人的实际选择撞在一起。" if accept else "只承诺说清安排，当前没有具体利害。"),
        "readthrough": check(["provisional_lead.text", "read_question"],
                             "想看她是否亲自改选搭档。" if accept else "只是依次完成已无争议的安排。"),
        "desire": check(["goal"], "我想被她主动选一次。" if accept else "没有要争取或保住的具体欲望。"),
        "turns": [{"turn_index": i, **check([f"turns.{i}.after"],
                   "对方作了新选择，逼主角改变办法。" if accept else "前后仍无异议，只是确认下一条。")} 
                  for i in range(len(option["turns"]))],
        "ending": check(["ending_payoff"], "她在当场行动中主动选择我。" if accept else "结尾只总结沟通很重要。"),
    }
    return {"candidate_id": option["id"], "rank": rank,
            "strength": "事件和前后安排写得清楚。", "weakness": "样包仍需正文兑现。",
            "execution_risk": "不能以补写的解释替代当场选择。",
            "feasibility": {"decision": "pass", "basis": "报名行为有已建立的权限与回应。", "blocking_claims": []},
            "entry_review": entry}


def comparison(options, *, accept=True):
    return {"comparisons": [editorial_row(option, i + 1, accept=accept) for i, option in enumerate(options)],
            "recommended_id": options[0]["id"] if accept else None}


class EntryPackTests(unittest.TestCase):
    def test_weak_process_pack_is_structurally_valid_but_all_rejected_by_reviewer(self):
        options = candidates(weak=True)
        self.assertEqual(entry_pack.validate_candidates({"candidates": options}), options)
        review = comparison(options, accept=False)
        result = entry_pack.validate_comparison(review, options)
        self.assertIsNone(result["recommended_id"])
        self.assertIsNone(result["recommended_index"])
        self.assertEqual(len(result["comparisons"]), 4)
        self.assertTrue(all(row["feasibility"]["decision"] == "pass" for row in result["comparisons"]))
        with self.assertRaisesRegex(StoryError, "入口被独立否决"):
            entry_pack.require_entry_choice(result, options[0]["id"])

    def test_all_rejected_cannot_be_changed_to_a_selected_id(self):
        options = candidates(weak=True)
        review = comparison(options, accept=False)
        review["recommended_id"] = options[0]["id"]
        with self.assertRaisesRegex(StoryError, "全部方案被独立否决"):
            entry_pack.validate_comparison(review, options)

    def test_independent_entry_veto_excludes_highest_rank(self):
        options = candidates()
        options[0] = candidate("entry-1", weak=True)
        review = comparison(options)
        review["comparisons"][0] = editorial_row(options[0], 1, accept=False)
        with self.assertRaisesRegex(StoryError, "入口被独立否决"):
            entry_pack.validate_comparison(review, options)
        review["recommended_id"] = "entry-2"
        result = entry_pack.validate_comparison(review, options)
        self.assertEqual(result["recommended_index"], 1)
        entry_pack.require_entry_choice(result, "entry-2")

    def test_entry_approval_never_bypasses_causal_veto(self):
        options = candidates()
        review = comparison(options)
        row = review["comparisons"][0]
        row["feasibility"] = {"decision": "revise", "basis": "名额改选的权限仍未成立。",
            "blocking_claims": [{"quote": options[0]["turns"][0]["after"], "reason": "尚未建立现场改选条件。"}]}
        with self.assertRaisesRegex(StoryError, "核心缺口"):
            entry_pack.validate_comparison(review, options)
        review["recommended_id"] = "entry-2"
        result = entry_pack.validate_comparison(review, options)
        with self.assertRaisesRegex(StoryError, "核心缺口"):
            require_feasible_choice(result, "entry-1")

    def test_all_causal_rejections_are_returned_with_entry_judgments(self):
        options = candidates()
        review = comparison(options)
        for row, option in zip(review["comparisons"], options):
            row["feasibility"] = {"decision": "revise", "basis": "权限未建立。",
                "blocking_claims": [{"quote": option["turns"][0]["after"], "reason": "需要先建立改选条件。"}]}
        review["recommended_id"] = None
        result = entry_pack.validate_comparison(review, options)
        self.assertIsNone(result["recommended_index"])
        self.assertTrue(all(row["entry_review"]["decision"] == "pass" for row in result["comparisons"]))

    def test_quotes_are_anchored_to_the_corresponding_candidate_and_field(self):
        options = candidates()
        options[1]["working_title"] = "只在第二案里出现的题目"
        for field, quote in (("working_title", options[1]["working_title"]),
                             ("working_title", options[0]["goal"]),
                             ("working_title", "实际输入没有的题目"), ("not_a_field", "我")):
            with self.subTest(field=field, quote=quote):
                review = comparison(options)
                review["comparisons"][0]["entry_review"]["click"]["evidence"][0] = {"field": field, "quote": quote}
                with self.assertRaisesRegex(StoryError, "准确引用"):
                    entry_pack.validate_comparison(review, options)

    def test_bad_recommended_id_duplicate_coverage_and_rank_are_rejected(self):
        options = candidates()
        bad_reviews = []
        for identifier in ("missing", None, "entry-4"):
            review = comparison(options); review["recommended_id"] = identifier; bad_reviews.append(review)
        review = comparison(options); review["comparisons"][1]["candidate_id"] = "entry-1"; bad_reviews.append(review)
        review = comparison(options); review["comparisons"][1]["rank"] = True; bad_reviews.append(review)
        for review in bad_reviews:
            with self.subTest(review=review), self.assertRaises(StoryError):
                entry_pack.validate_comparison(review, options)

    def test_missing_anchor_or_conflicting_overall_judgment_is_rejected(self):
        options = candidates()
        review = comparison(options)
        review["comparisons"][0]["entry_review"]["readthrough"]["evidence"].pop()
        with self.assertRaisesRegex(StoryError, "原文锚点"):
            entry_pack.validate_comparison(review, options)
        review = comparison(options)
        review["comparisons"][0]["entry_review"]["desire"]["decision"] = "reject"
        with self.assertRaisesRegex(StoryError, "矛盾"):
            entry_pack.validate_comparison(review, options)

    def test_candidate_structure_does_not_pretend_to_judge_dramatic_strength(self):
        options = candidates()
        options[0]["working_title"] = "青梅很野"
        options[0]["provisional_lead"]["text"] += "她用专业的分镜术语指出我把她画到了人群外。"
        self.assertEqual(entry_pack.validate_candidates({"candidates": options}), options)
        self.assertIn("不能用术语黑名单、句长", entry_pack.comparison_guidance())
        self.assertIn("没有具体欲望", entry_pack.comparison_guidance())

    def test_first_sentence_must_be_a_literal_prefix_and_three_turns_are_minimum(self):
        options = candidates()
        options[0]["provisional_lead"]["first_sentence"] = "试写里没有这句。"
        with self.assertRaisesRegex(StoryError, "前缀一致"):
            entry_pack.validate_candidates({"candidates": options})
        options = candidates()
        options[0]["turns"].pop()
        with self.assertRaisesRegex(StoryError, "三个"):
            entry_pack.validate_candidates({"candidates": options})
        options = candidates()
        options[0]["turns"].append(copy.deepcopy(options[0]["turns"][2]))
        self.assertEqual(len(entry_pack.validate_candidates({"candidates": options})[0]["turns"]), 4)
        entry_pack.validate_comparison(comparison(options), options)

    def test_every_turn_requires_its_own_assessment(self):
        options = candidates()
        review = comparison(options)
        review["comparisons"][0]["entry_review"]["turns"][1]["turn_index"] = 0
        with self.assertRaisesRegex(StoryError, "完整且唯一"):
            entry_pack.validate_comparison(review, options)

    def test_hash_binds_all_inputs_rubrics_and_schema(self):
        options = candidates()
        contract = {"channels": ["fanqie_short"], "title": "作者的标题"}
        baseline = entry_pack.comparison_input_hash(contract, options, "sources")
        changed = copy.deepcopy(options); changed[3]["turns"][0]["after"] += "她当场改选。"
        self.assertNotEqual(baseline, entry_pack.comparison_input_hash(contract, changed, "sources"))
        self.assertNotEqual(baseline, entry_pack.comparison_input_hash(contract, options, "new-sources"))
        with patch.dict(entry_pack.ENTRY_RUBRIC, {"click": "新的独立标准"}):
            self.assertNotEqual(baseline, entry_pack.comparison_input_hash(contract, options, "sources"))
        with patch.dict(entry_pack.ENTRY_PACK_SCHEMA, {"description": "新结构"}):
            self.assertNotEqual(baseline, entry_pack.comparison_input_hash(contract, options, "sources"))
        self.assertEqual(contract["title"], "作者的标题")
        self.assertTrue(entry_pack.applies(contract))
        self.assertFalse(entry_pack.applies({"channels": ["generic_text"]}))


if __name__ == "__main__":
    unittest.main()
