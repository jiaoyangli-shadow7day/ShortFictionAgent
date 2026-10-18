import unittest
from uuid import uuid4

from short_fiction_agent.story_studio.contracts import (
    StoryBrief,
    StoryError,
    digest,
)
from short_fiction_agent.story_studio.market import (
    validate_market_contract,
    market_gate,
)
from short_fiction_agent.story_studio.quality import review_decision, review_rubric_hash


class TestMarketContractAndReview(unittest.TestCase):
    def setUp(self):
        self.valid_body = "周晓推开门，看见林晨把辞职信放在桌上。她没有哭，只是拿起了工卡。" + "。" * 1000
        self.valid_market = {
            "profile_id": "fanqie_shiqing",
            "reader_emotion": "清醒克制与解脱感",
            "opening_conflict": "开局提出离职面对现实清算",
            "protagonist_action": "主角主动收回投入明确划清界限",
            "payoff": "彻底切割并在职场重新掌握主动权",
        }
        self.valid_audience = {
            "description": "关注现实利益与职场成长的都市青年读者",
            "expectations": ["拒绝恋爱脑", "注重现实利益"],
        }
        self.valid_brief_dict = {
            "title": "测试故事",
            "genre": {"primary": "romance"},
            "format": "micro_story",
            "review_mode": "deep_review",  # Exercises the optional detailed market checks.
            "story_engine": "relationship_choice",
            "reader_promise": "兑现主角主动做出的现实选择",
            "channels": ["fanqie_short"],
            "audience": self.valid_audience,
            "market_contract": self.valid_market,
            "length": {"target": 1000},
            "author_input": {"premise": "现实职场故事"},
        }

    def _build_valid_report(self, body: str, contract: dict) -> dict:
        from short_fiction_agent.story_studio.submission_guide import submission_guide_context
        spans = [[0, len(body)]]
        dimensions = {
            "opening_pull": 4,
            "causal_progress": 4,
            "character_specificity": 4,
            "payoff": 4,
            "economy": 4,
            "coherence": 4,
            "voice": 4,
            "channel_fit": 4,
        }
        dimension_evidence = {k: f"{k} 表现优秀" for k in dimensions}
        market_checks = [
            {
                "id": "opening_conflict",
                "verdict": "fulfilled",
                "quote": "周晓推开门，看见林晨把辞职信放在桌上。",
                "reason": "开篇清晰交代了冲突情境",
            },
            {
                "id": "protagonist_action",
                "verdict": "fulfilled",
                "quote": "她没有哭，只是拿起了工卡。",
                "reason": "主角做出冷静明确的行动",
            },
            {
                "id": "payoff",
                "verdict": "fulfilled",
                "quote": "她没有哭，只是拿起了工卡。",
                "reason": "完成切割交代",
            },
            {
                "id": "reader_emotion",
                "verdict": "fulfilled",
                "quote": "她没有哭，只是拿起了工卡。",
                "reason": "达成清醒的情绪体验",
            },
        ]
        return {
            "review_id": str(uuid4()),
            "artifact_id": "art_001",
            "revision": 1,
            "content_hash": digest(body),
            "contract_hash": digest(contract),
            "review_rubric_hash": review_rubric_hash(),
            "review_coverage": {"complete": True, "spans": spans},
            "dimensions": dimensions,
            "dimension_evidence": dimension_evidence,
            "fact_checks": [],
            "market_checks": market_checks,
            "issues": [],
            "review_integrity_errors": [],
            "submission_guide": {**submission_guide_context(contract, "review"), "content_hash": digest(body)},
        }

    def test_valid_market_configuration(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        self.assertEqual(brief.market_contract["profile_id"], "fanqie_shiqing")
        effective = brief.effective_contract()
        self.assertIn("market_guidance", effective)
        self.assertIn("番茄世情", effective["market_guidance"])

    def test_market_contract_type_and_missing_fields(self):
        # 错误类型：非字典
        with self.assertRaises(StoryError):
            validate_market_contract("invalid_str", ["fanqie_short"], self.valid_audience, "承诺")

        # 缺少必要字段
        incomplete = self.valid_market.copy()
        incomplete.pop("payoff")
        with self.assertRaises(StoryError):
            validate_market_contract(incomplete, ["fanqie_short"], self.valid_audience, "承诺")

        # 包含未知字段
        with_unknown = self.valid_market.copy()
        with_unknown["extra_field"] = "extra"
        with self.assertRaises(StoryError):
            validate_market_contract(with_unknown, ["fanqie_short"], self.valid_audience, "承诺")

        # 未知 profile_id
        with_bad_profile = self.valid_market.copy()
        with_bad_profile["profile_id"] = "unknown_profile"
        with self.assertRaises(StoryError):
            validate_market_contract(with_bad_profile, ["fanqie_short"], self.valid_audience, "承诺")

    def test_channel_mismatch(self):
        # 渠道不匹配：fanqie_shiqing 必须包含 fanqie_short
        with self.assertRaises(StoryError):
            validate_market_contract(
                self.valid_market,
                ["douyin_carousel"],
                self.valid_audience,
                "承诺",
            )

    def test_fake_citation_rejected(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        contract = brief.effective_contract()
        report = self._build_valid_report(self.valid_body, contract)

        # 引文不存在于正文中（假引文）
        report["market_checks"][0]["quote"] = "一段根本不在正文中的编造引文"
        with self.assertRaises(StoryError) as ctx:
            market_gate(self.valid_body, contract, report["market_checks"])
        self.assertIn("非法原文", str(ctx.exception))

        # fulfilled 但引文为空
        report["market_checks"][0]["quote"] = ""
        with self.assertRaises(StoryError) as ctx:
            market_gate(self.valid_body, contract, report["market_checks"])
        self.assertIn("必须提供正文引文证据", str(ctx.exception))

    def test_missing_market_checks(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        contract = brief.effective_contract()
        report = self._build_valid_report(self.valid_body, contract)

        # 审查项缺失（只有3项）
        report["market_checks"] = report["market_checks"][:3]
        with self.assertRaises(StoryError) as ctx:
            market_gate(self.valid_body, contract, report["market_checks"])
        self.assertIn("必须包含全部4项审查项", str(ctx.exception))

    def test_low_channel_fit_blocks_even_if_all_other_scores_are_max(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        contract = brief.effective_contract()
        report = self._build_valid_report(self.valid_body, contract)

        # 即使其他全部是4分，channel_fit < 2 仍被阻断
        report["dimensions"]["channel_fit"] = 1
        decision = review_decision(self.valid_body, contract, report)
        self.assertEqual(decision["decision"], "revise_required")
        self.assertIn("dimension:channel_fit", decision["blockers"])

    def test_market_missing_or_unclear_blocks_even_if_scores_are_max(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        contract = brief.effective_contract()
        report = self._build_valid_report(self.valid_body, contract)

        # 将 opening_conflict 设为 missing
        report["market_checks"][0]["verdict"] = "missing"
        report["market_checks"][0]["quote"] = ""
        report["market_checks"][0]["reason"] = "开篇冲突未兑现"

        decision = review_decision(self.valid_body, contract, report)
        self.assertEqual(decision["decision"], "revise_required")
        self.assertIn("market:opening_conflict:missing", decision["blockers"])

    def test_modify_market_contract_invalidates_old_report(self):
        brief = StoryBrief.from_dict(self.valid_brief_dict)
        contract_v1 = brief.effective_contract()
        report_v1 = self._build_valid_report(self.valid_body, contract_v1)

        # 原始合同下审查通过
        decision_v1 = review_decision(self.valid_body, contract_v1, report_v1)
        self.assertEqual(decision_v1["decision"], "passed")

        # 修改市场合同中的字段生成 contract_v2
        modified_brief_dict = dict(self.valid_brief_dict)
        modified_market = self.valid_market.copy()
        modified_market["payoff"] = "修改后的回响与交代承诺"
        modified_brief_dict["market_contract"] = modified_market
        contract_v2 = StoryBrief.from_dict(modified_brief_dict).effective_contract()

        # 使用旧 report_v1 去校验 contract_v2 必须抛出版本不匹配错误
        with self.assertRaises(StoryError) as ctx:
            review_decision(self.valid_body, contract_v2, report_v1)
        self.assertIn("审查报告与当前正文或写作合同版本不匹配", str(ctx.exception))

class TestMarketWorkflowIntegration(unittest.TestCase):
    setUp = TestMarketContractAndReview.setUp
    _build_valid_report = TestMarketContractAndReview._build_valid_report
    def test_review_and_export_include_market_evidence(self):
        import tempfile
        from pathlib import Path
        from short_fiction_agent.story_studio.storage import StoryProject
        from short_fiction_agent.story_studio.quality import StoryReviewer
        from short_fiction_agent.story_studio.channels import StoryExporter
        from test_story_studio import ScriptedLLM
        with tempfile.TemporaryDirectory() as tmp:
            project = StoryProject(Path(tmp) / 'story')
            project.create(StoryBrief.from_dict(self.valid_brief_dict))
            project.save_draft(self.valid_body)
            body = project.active_draft()[1]
            checks = self._build_valid_report(body, project.contract())['market_checks']
            llm = ScriptedLLM(body)
            tasks = []
            def call(task, prompt):
                import json
                tasks.append(task)
                if task == 'market_review':
                    self.assertIn(self.valid_market['protagonist_action'], prompt)
                    return {'checks': checks}
                return json.loads(llm.generate(prompt, task))
            report = StoryReviewer(project, call).review()
            self.assertEqual(report['decision'], 'passed')
            self.assertIn('market_review', tasks)
            package = StoryExporter(project, call).export('fanqie_short')
            import json
            metadata = json.loads((package / 'metadata.json').read_text())
            self.assertEqual(metadata['market_contract']['profile_id'], 'fanqie_shiqing')
            self.assertEqual(metadata['commercial_validation'], 'unverified')
            with self.assertRaises(StoryError):
                StoryExporter(project).export('douyin_carousel')
            updated = project.brief()
            updated.market_contract['payoff'] = '新结局承诺'
            from short_fiction_agent.story_studio.storage import write_text
            import yaml
            write_text(project.brief_path, yaml.safe_dump(updated.to_dict(), allow_unicode=True))
            with self.assertRaises(StoryError):
                StoryExporter(project).export('fanqie_short')

    def test_market_error_leaves_no_passed_memory(self):
        import tempfile
        from pathlib import Path
        from short_fiction_agent.story_studio.storage import StoryProject
        from short_fiction_agent.story_studio.quality import StoryReviewer
        from test_story_studio import ScriptedLLM
        with tempfile.TemporaryDirectory() as tmp:
            project = StoryProject(Path(tmp) / 'story')
            project.create(StoryBrief.from_dict(self.valid_brief_dict))
            project.save_draft(self.valid_body)
            llm = ScriptedLLM(self.valid_body)
            def call(task, prompt):
                import json
                return {'checks': []} if task == 'market_review' else json.loads(llm.generate(prompt, task))
            with self.assertRaises(StoryError):
                StoryReviewer(project, call).review()
            self.assertEqual(project.active_draft()[0]['state'], 'review_error')
            self.assertFalse(list((project.root / 'memory/commits').glob('*.json')))
            self.assertTrue(list((project.root / 'runs').glob('market-review-*.json')))

    def test_contract_change_regenerates_candidates(self):
        import tempfile
        from pathlib import Path
        import yaml
        from test_story_studio import ScriptedLLM
        from short_fiction_agent.story_studio.storage import StoryProject, write_text
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        with tempfile.TemporaryDirectory() as tmp:
            project = StoryProject(Path(tmp) / 'story')
            project.create(StoryBrief.from_dict(self.valid_brief_dict))
            llm = ScriptedLLM(self.valid_body)
            flow = StoryWorkflow(project, llm)
            flow.develop()
            flow.develop()
            self.assertEqual(llm.tasks.count('story_candidates'), 1)
            updated = project.brief()
            updated.market_contract['reader_emotion'] = '新的读者情绪承诺'
            write_text(project.brief_path, yaml.safe_dump(updated.to_dict(), allow_unicode=True))
            flow.develop()
            self.assertEqual(llm.tasks.count('story_candidates'), 2)

    def test_duplicate_ids_and_unclear_cannot_pass(self):
        import copy
        contract = StoryBrief.from_dict(self.valid_brief_dict).effective_contract()
        report = self._build_valid_report(self.valid_body, contract)
        duplicate = copy.deepcopy(report['market_checks'])
        duplicate[1]['id'] = duplicate[0]['id']
        with self.assertRaises(StoryError):
            market_gate(self.valid_body, contract, duplicate)
        report['market_checks'][2].update(verdict='unclear', quote='')
        self.assertIn('market:payoff:unclear', review_decision(self.valid_body, contract, report)['blockers'])

    def test_generic_contract_hash_compatibility(self):
        brief = StoryBrief.from_dict({'author_input': {'premise': '测试'}})
        self.assertNotIn('market_contract', brief.effective_contract())
        self.assertNotIn('market_guidance', brief.effective_contract())
        self.assertNotIn('market_basis', brief.effective_contract())

    def test_market_failure_stops_cosmetic_auto_revision(self):
        import tempfile
        import json
        from pathlib import Path
        from unittest.mock import patch
        from test_story_studio import ScriptedLLM
        from short_fiction_agent.story_studio.storage import StoryProject
        from short_fiction_agent.story_studio.workflow import StoryWorkflow
        case = self
        class LLM(ScriptedLLM):
            def generate(self, prompt, task):
                if task == 'market_review':
                    checks = case._build_valid_report(case.valid_body, {})['market_checks']
                    checks[2].update(verdict='missing', quote='', reason='结尾未兑现合同结果')
                    return json.dumps({'checks': checks}, ensure_ascii=False)
                return super().generate(prompt, task)
        with tempfile.TemporaryDirectory() as tmp:
            project = StoryProject(Path(tmp) / 'story')
            project.create(StoryBrief.from_dict(self.valid_brief_dict))
            flow = StoryWorkflow(project, LLM(self.valid_body))
            # This isolates market revision policy with stubbed planning; native
            # preview provenance is covered by the dedicated preview regressions.
            with patch.object(flow, 'plan', return_value={}), \
                 patch.object(flow, '_selection', return_value=({}, {})), \
                 patch('short_fiction_agent.story_studio.plan_preview.save'), \
                 patch.object(flow, '_draft_text', return_value=self.valid_body), \
                 patch.object(flow, '_edit', return_value=self.valid_body) as editor:
                report = flow.generate()
            self.assertEqual(report['decision'], 'revise_required')
            self.assertEqual(project.active_draft()[0]['state'], 'needs_author')
            self.assertEqual(editor.call_count, 1)
            self.assertIn('market:payoff:missing', report['blockers'])

if __name__ == "__main__":
    unittest.main()
