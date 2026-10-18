import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio.channels import StoryExporter
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.publication import (
    activity_window_status, publication_check, publication_guidance, publication_routes,
)
from short_fiction_agent.story_studio.storage import StoryProject, read_json


class PublicationFixtures:
    def brief(self, publication=None, *, target=24000, minimum=21000, category="男生情感"):
        raw = {"project_id": "publication-test", "title": "约好的见面",
               "genre": {"primary": "romance"}, "format": "short_story",
               "channels": ["fanqie_short"], "length": {"target": target, "min": minimum, "max": 30000},
               "author_input": {"premise": "男主在见面时为关系作出选择"}}
        if publication is not None:
            raw["publication_contract"] = {"official_category": category, **publication}
        return StoryBrief.from_dict(raw)

    def gold(self, **updates):
        return {"route_id": "fanqie_ip", "activity": {"activity_id": "ip_gold", "period": "2026-10"}, **updates}

    def evidence(self, body, count, *, first=None):
        result = {"word_count": count, "content_hash": digest(body), "source": "author_backend",
                  "evidence_ref": "后台当前正文计字截图"}
        if first is not None:
            result["first_submission_word_count"] = first
        return result


class PublicationContractTests(PublicationFixtures, unittest.TestCase):
    def test_fanqie_default_is_ordinary_reading_without_activity_or_category_invention(self):
        brief = self.brief()
        contract = brief.effective_contract()
        publication = contract["publication_contract"]
        self.assertEqual(publication["route_id"], "fanqie_app_reading")
        self.assertIsNone(publication["activity"])
        self.assertEqual(publication["official_category"], "")
        self.assertEqual(brief.genre["primary"], "romance")
        # Keeping the opt-in field empty on disk also keeps older small drafts readable.
        self.assertEqual(brief.to_dict()["publication_contract"], {})

    def test_generic_contract_migration_keeps_publication_opt_in_and_changes_review_binding(self):
        brief = StoryBrief.from_dict({"project_id": "legacy-publication-test", "title": "找信",
                                     "length": {"target": 900}, "author_input": {"premise": "林禾找信"}})
        contract = brief.effective_contract()
        self.assertNotIn("publication_contract", contract)
        self.assertNotIn("publication_guidance", contract)
        self.assertEqual(contract['review_mode'], 'chief_editor')
        self.assertIsNone(contract['length']['max'])
        # New reading protocol and soft length policy must invalidate old approvals.
        self.assertNotEqual(digest(contract), "b7da9fa2a851c864b95a0d414f5036d387d1b0dc3972200a50450366f5456c6c")
        self.assertEqual(digest(contract), digest(StoryBrief.from_dict(brief.to_dict()).effective_contract()))

    def test_unknown_route_wrong_channel_wrong_period_and_route_activity_are_rejected(self):
        for publication in ({"route_id": "guaranteed_traffic"},
                            {"route_id": "fanqie_ip", "activity": {"activity_id": "ip_gold", "period": "2026-09"}},
                            {"route_id": "fanqie_app_reading", "activity": {"activity_id": "ip_gold", "period": "2026-10"}}):
            with self.subTest(publication=publication), self.assertRaises(StoryError):
                self.brief(publication)
        raw = self.brief().to_dict()
        raw.update(channels=["generic_text"], publication_contract=self.gold())
        with self.assertRaisesRegex(StoryError, "channels"):
            StoryBrief.from_dict(raw)

    def test_gold_target_20000_does_not_accept_implicit_minimum_16000(self):
        raw = self.brief().to_dict()
        raw.update(length={"target": 20000}, publication_contract=self.gold())
        with self.assertRaisesRegex(StoryError, "length.min.*20000"):
            StoryBrief.from_dict(raw)
        self.assertEqual(self.brief(self.gold(), target=20000, minimum=20000).length["min"], 20000)

    def test_local_length_failure_is_independent_of_platform_count(self):
        contract = self.brief(self.gold(), category="现言甜宠").effective_contract()
        result = publication_check("字" * 19000, contract, as_of="2026-10-08")
        self.assertEqual(result["status"], "mismatch")
        self.assertIn("local_length", result["blockers"])
        self.assertIn("platform_word_count", result["pending"])

    def test_sufficient_visible_characters_still_need_platform_count(self):
        contract = self.brief(self.gold(), category="现言甜宠").effective_contract()
        result = publication_check("字" * 22000, contract, as_of="2026-10-08")
        self.assertEqual(result["status"], "pending")
        self.assertIn("platform_word_count", result["pending"])
        self.assertIsNone(result["platform_confirmation"])
        self.assertIn("标点、章标题和导语", publication_routes()["counting"]["local_scope"])

    def test_real_male_romance_category_is_preserved_and_activity_mismatch_is_visible(self):
        brief = self.brief(self.gold())
        result = publication_check("字" * 22000, brief.effective_contract(), as_of="2026-10-08")
        self.assertEqual(result["official_category"], "男生情感")
        self.assertIn("activity_category", result["blockers"])
        self.assertEqual(brief.genre["primary"], "romance")
        self.assertEqual(brief.publication_contract["official_category"], "男生情感")
        changed = publication_check("字" * 22000, brief.effective_contract(), category="男生生活", as_of="2026-10-08")
        self.assertIn("official_category", changed["blockers"])

    def test_category_label_alone_cannot_prove_content_fit(self):
        contract = self.brief(self.gold(category_status="author_backend_confirmed"), category="男生生活").effective_contract()
        result = publication_check("字" * 22000, contract, as_of="2026-10-08")
        self.assertIn("activity_category", result["pending"])
        self.assertIn("无言情主线", publication_routes()["activities"]["ip_gold"]["category_definitions"]["男生生活"])

    def test_expired_window_is_not_current_but_historic_signing_can_still_be_checked(self):
        contract = self.brief(self.gold(), category="现言甜宠").effective_contract()
        result = publication_check("字" * 22000, contract, as_of="2026-11-01")
        self.assertEqual(result["activity_window_status"], "expired")
        self.assertIn("activity_window", result["blockers"])
        historic = copy.deepcopy(contract)
        historic["publication_contract"].update(signing_status="signed", signed_completed_at="2026-10-31T23:59:59+08:00")
        result = publication_check("字" * 22000, historic, as_of="2026-11-01")
        self.assertEqual(result["activity_window_status"], "expired")
        self.assertNotIn("activity_window", result["blockers"])

    def test_activity_window_includes_beijing_end_boundary(self):
        activity = publication_routes()["activities"]["ip_gold"]
        self.assertEqual(activity_window_status(activity, as_of="2026-09-30T23:59:59+08:00"), "not_started")
        self.assertEqual(activity_window_status(activity, as_of="2026-10-01T00:00:00+08:00"), "open")
        self.assertEqual(activity_window_status(activity, as_of="2026-10-31T15:59:59+00:00"), "open")
        self.assertEqual(activity_window_status(activity, as_of="2026-11-01T00:00:00+08:00"), "expired")

    def test_gold_exactly_20000_keeps_specific_rules_and_introduction_difference(self):
        contract = self.brief(self.gold(), target=20000, minimum=20000, category="现言甜宠").effective_contract()
        body = "字" * 20000
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 20000, first=20000))
        self.assertNotIn("platform_word_count", result["blockers"])
        self.assertIn("activity_word_boundary", result["pending"])
        boundary = publication_routes()["activities"]["ip_gold"]["word_boundary"]
        self.assertEqual(boundary["specific_reward_rules"], ">=20000")
        self.assertEqual(boundary["introduction"], ">20000")

    def test_echo_accepts_under_20000_for_base_scope_and_keeps_rewards_separate(self):
        contract = self.brief({"route_id": "fanqie_ip", "activity": {"activity_id": "ip_echo", "period": "3"}},
                              target=10000, minimum=8000, category="现言甜宠").effective_contract()
        body = "字" * 10000
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 10000, first=10000))
        self.assertNotIn("platform_word_count", result["blockers"])
        guidance = publication_guidance(contract)
        self.assertIn("至少20000字及番茄累计点赞TOP300", guidance)
        self.assertIn("不自动入选", json.dumps(result, ensure_ascii=False))

    def test_thousand_general_rewards_do_not_require_seven_potential_categories(self):
        contract = self.brief({"route_id": "fanqie_new_media", "activity": {"activity_id": "thousand_words_gold", "period": "4"}},
                              target=25000, minimum=24000).effective_contract()
        body = "字" * 25000
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 24950, first=24950))
        self.assertNotIn("activity_category", result["blockers"])
        self.assertIn("activity_word_tier", result["pending"])
        self.assertIn("rights", result["pending"])

    def test_platform_count_requires_current_body_evidence_and_first_submission_count(self):
        contract = self.brief(self.gold(), category="现言甜宠").effective_contract()
        body = "字" * 22000
        with self.assertRaisesRegex(StoryError, "当前正文"):
            publication_check(body, contract, platform_confirmation=self.evidence(body + "修改", 22000))
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 22000))
        self.assertIn("platform_word_count", result["pending"])
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 22000, first=16000))
        self.assertIn("platform_word_count", result["blockers"])

    def test_small_ordinary_draft_is_pending_and_does_not_infer_platform_rejection(self):
        contract = self.brief(target=1000, minimum=800).effective_contract()
        body = "字" * 1000
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 900))
        self.assertEqual(result["status"], "pending")
        self.assertFalse(result["blockers"])
        self.assertEqual(result["states"]["signing_status"], "not_submitted")

    def test_recorded_complete_evidence_can_be_ready_without_predicting_rewards(self):
        publication = self.gold(category_status="platform_confirmed", category_fit_status="platform_confirmed",
                                completion_status="complete", originality_status="confirmed",
                                first_publication_status="confirmed", exclusive_status="confirmed",
                                signing_status="signed", signed_completed_at="2026-10-07T12:00:00+08:00",
                                activity_status="selected")
        contract = self.brief(publication, category="现言甜宠").effective_contract()
        body = "字" * 22000
        result = publication_check(body, contract, as_of="2026-10-08", platform_confirmation=self.evidence(body, 21000, first=21000))
        self.assertEqual(result["status"], "ready")
        self.assertIn("不代表平台准入", result["meaning"])


class PublicationExportTests(PublicationFixtures, unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def project(self, brief, body):
        project = StoryProject(Path(self.temp.name) / "story")
        project.create(brief)
        record = project.save_draft(body)
        project.update_state(record, "passed")
        return project

    def test_default_pending_export_writes_traceable_checks_without_platform_claim(self):
        project = self.project(self.brief(target=1000, minimum=800), "他赴约，准备作出选择。\n\n第一章\n\n" + "甲" * 1000)
        from short_fiction_agent.story_studio.submission_guide import submission_guide_context
        review = {"review_id": "existing-review", "submission_guide": {
            **submission_guide_context(project.contract(), "review"), "content_hash": digest(project.active_draft()[1])}}
        with patch("short_fiction_agent.story_studio.channels.current_review", return_value=review), \
             patch("short_fiction_agent.story_studio.distribution.PublicationPreparationAgent.review",
                   return_value={"decision": "editorial_ready", "inputs": {"category": "romance"}}):
            package = StoryExporter(project).export("fanqie_short", titles=["约好的见面"], synopsis="男主赴约", publication_as_of="2026-10-08")
        result = read_json(package / "publication_check.json")
        manifest = read_json(package / "manifest.json")
        submission = read_json(package / "submission_info.json")
        self.assertEqual(result["status"], "pending")
        self.assertEqual(manifest["publication_status"], "pending")
        self.assertIn("publication_check.json", manifest["files_sha256"])
        self.assertEqual(manifest["files_sha256"]["publication_check.json"], digest((package / "publication_check.json").read_text()))
        self.assertFalse(manifest["published"])
        self.assertIsNone(submission["recommended_category"])
        self.assertEqual(submission["platform_eligibility"], "unverified")

    def test_activity_mismatch_blocks_formal_export_and_remains_visible_in_draft(self):
        project = self.project(self.brief(self.gold()), "甲" * 22000)
        with patch("short_fiction_agent.story_studio.channels.current_review", return_value={"review_id": "existing-review"}):
            with self.assertRaisesRegex(StoryError, "所选活动投稿目标不符"):
                StoryExporter(project).export("fanqie_short", titles=["约好的见面"], synopsis="男主赴约", publication_as_of="2026-10-08")
        package = StoryExporter(project).export("fanqie_short", draft=True, publication_as_of="2026-10-08")
        self.assertEqual(read_json(package / "publication_check.json")["status"], "mismatch")
        self.assertEqual(read_json(package / "manifest.json")["delivery_status"], "review_copy")


if __name__ == "__main__":
    unittest.main()
