"""Independent plan evidence transport, not a literary quality benchmark."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from short_fiction_agent.story_studio import scene_planning, target_reader
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_chapter_plan_review import chapter_plan
from test_story_chapter_production import ChapterTransportLLM
from test_story_scene_purpose_budget import example_plan, review_control
from test_story_studio import _prompt_object


def add_private_claims(plan):
    plan['premise_test'] = 'PRIVATE-CERT-ROOT：能力没有沦为装饰。'
    plan['reading_allocation_check'] = {'status': 'effective', 'reason': 'PRIVATE-CERT-AUDIT'}
    for index, scene in enumerate(plan['scenes']):
        for field in ('purpose', 'new_information', 'emotional_change', 'deletion_loss'):
            scene[field] = f'PRIVATE-CERT-{field}-{index}：这已证明读者会喜欢。'
        if isinstance(scene.get('dramatic_chain'), dict):
            scene['dramatic_chain']['deletion_loss'] = f'PRIVATE-CERT-CHAIN-{index}'
        for beat in scene.get('progression_beats', []):
            for field in ('reward', 'reader_question', 'next_expectation'):
                beat[field] = f'PRIVATE-CERT-{field}-{index}：设定已经生效。'
    for chapter in plan.get('chapters', []):
        for point in chapter.get('reading_points', []):
            point['note'] = 'PRIVATE-CERT-CHAPTER：本章阅读点非常充分。'
    return plan


class ProjectedPlanLLM(ChapterTransportLLM):
    def __init__(self):
        super().__init__()
        self.chief_inputs = []
        self.original_plan = None

    def generate(self, prompt, task_type):
        if task_type == 'story_plan':
            source = json.loads(super().generate(prompt, task_type))
            add_private_claims(source)
            # Real facts can be supplied only here; dropping them would be a
            # different change from removing a planner's quality claims.
            source['story_facts'] = [{'fact_id': 'ten-queries', 'fact': '查询过一次，剩余九次。'}]
            source['characters'] = [{'name': '林禾', 'identity': '已满十八岁的大学生'}]
            self.original_plan = copy.deepcopy(source)
            return json.dumps(source, ensure_ascii=False)
        if task_type == 'chief_plan_review':
            self.chief_inputs.append(_prompt_object(prompt, ('contract', 'plan')))
        return super().generate(prompt, task_type)


class IndependentPlanReviewViewTests(unittest.TestCase):
    def test_projection_retains_complete_events_facts_mapping_and_budget_without_keyword_filter(self):
        source = add_private_claims(chapter_plan())
        source['story_facts'] = [{'fact_id': 'rule', 'fact': '只可查询已经发生的历史，剩余九次。'}]
        source['characters'] = [{'name': '她', 'age_at_opening': 18, 'identity': '辩论队长'}]
        beat = source['scenes'][1]['progression_beats'][0]
        beat['action'] = '她把车票塞给他：“设定生效了？那先跟我走。”'
        source['scenes'][1]['dramatic_chain'] = {
            key: '角色当面作出的完整行动与回应。' for key in scene_planning.REVIEW_CHAIN_FIELDS}
        source['scenes'][1]['dramatic_chain']['deletion_loss'] = 'PRIVATE-CERT-LOSS'
        before = copy.deepcopy(source)
        view = scene_planning.review_view(source)
        visible = json.dumps(view, ensure_ascii=False)
        for hidden in ('PRIVATE-CERT-ROOT', 'PRIVATE-CERT-AUDIT', 'PRIVATE-CERT-purpose',
                       'PRIVATE-CERT-deletion_loss', 'PRIVATE-CERT-CHAIN',
                       'PRIVATE-CERT-reader_question', 'PRIVATE-CERT-next_expectation'):
            self.assertNotIn(hidden, visible)
        for candidate in ('PRIVATE-CERT-new_information', 'PRIVATE-CERT-emotional_change', 'PRIVATE-CERT-reward'):
            self.assertIn(candidate, visible)
        self.assertEqual({key: view['scenes'][1]['progression_beats'][0][key]
                          for key in ('action', 'response', 'change')},
                         {key: beat[key] for key in ('action', 'response', 'change')})
        self.assertEqual(view['scenes'][1]['dramatic_chain'], {
            key: source['scenes'][1]['dramatic_chain'][key] for key in scene_planning.REVIEW_CHAIN_FIELDS})
        self.assertEqual(view['story_facts'], source['story_facts'])
        self.assertEqual(view['characters'], source['characters'])
        self.assertEqual(view['continuity_map'], source['continuity_map'])
        self.assertEqual([row['budget'] for row in view['scenes']],
                         [row['budget'] for row in source['scenes']])
        self.assertEqual(view['chapters'], [{key: row[key] for key in ('chapter_id', 'title', 'scene_ids')}
                                           for row in source['chapters']])
        self.assertEqual([row['scene_id'] for row in view['scenes']],
                         [row['scene_id'] for row in source['scenes']])
        self.assertEqual(source, before)
        view['story_facts'][0]['fact'] = '改动视图不能改原案'
        view['scenes'][1]['progression_beats'][0]['action'] = '改动视图不能改原案'
        self.assertEqual(source, before)

    def test_production_review_is_independent_but_source_writer_and_repair_feedback_stay_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['fanqie_short'],
                'title': '十次查询机会', 'reader_promise': '信息优势改变机会，并得到对方主动偏爱。',
                'length': {'min': 1, 'target': 6000, 'max': None},
                'author_input': {'premise': '只可查询原历史，人物通过实际接招改变机会。',
                                 'locked_facts': ['双方均已成年。']}}))
            llm = ProjectedPlanLLM()
            flow = StoryWorkflow(project, llm, max_calls=100)
            flow.develop()
            plan = flow.plan()
            actual = llm.chief_inputs[0]
            self.assertEqual(actual['contract'], project.contract())
            self.assertEqual(actual['target_reader_context'],
                             target_reader.entry_context(project.contract(), include_author_promise=True))
            self.assertEqual(actual['plan_review_view_protocol'], scene_planning.REVIEW_VIEW_PROTOCOL)
            self.assertEqual(actual['plan'], scene_planning.review_view(llm.original_plan))
            self.assertNotIn('PRIVATE-CERT-ROOT', json.dumps(actual['plan'], ensure_ascii=False))
            self.assertNotIn('PRIVATE-CERT-purpose', json.dumps(actual['plan'], ensure_ascii=False))
            self.assertIn('PRIVATE-CERT-reward', json.dumps(actual['plan'], ensure_ascii=False))
            self.assertIn('PRIVATE-CERT', json.dumps(read_json(project.root / 'plan/scenes.json'), ensure_ascii=False))
            feedback = {'scope': 'budget_replan_from_scratch', 'source_request_hash': 'prior-request',
                        'issues': [{'reason': '前稿重复验题，须保留一次真实接招并合并其余。'}]}
            original = copy.deepcopy(plan)
            flow._preflight_plan_chief(plan, project.contract(), revision_context=feedback)
            self.assertEqual(llm.chief_inputs[-1]['revision_context'], feedback)
            flow._draft_text(plan, max_chapters=1)
            self.assertEqual(llm.writer_inputs[0]['scene'], plan['scenes'][0])
            self.assertIn('PRIVATE-CERT', json.dumps(llm.writer_inputs[0]['scene'], ensure_ascii=False))
            self.assertEqual(plan, original)
            self.assertEqual(read_json(project.root / 'plan/scenes.json'), original)

    def test_review_cannot_cite_hidden_claims_in_any_evidence_family(self):
        source = chapter_plan()
        hidden = '唯一未展示的策划者自证：设定已经充分发挥。'
        source['scenes'][1]['progression_beats'][0]['reader_question'] = hidden
        view = scene_planning.review_view(source)
        mutations = {
            'scene': lambda r: r['scene_checks'][1].update(change_quote=hidden),
            'premise': lambda r: r['premise_test']['evidence'][0].update(quote=hidden),
            'reward': lambda r: r['reading_allocation_check']['first_reward'].update(quote=hidden),
            'compression': lambda r: r['reading_allocation_check']['expansion_choices'][1]
                ['compression_trial']['retained_beats'][0].update(response_quote=hidden),
            'chapter': lambda r: r['chapter_checks'][0]['evidence'][0].update(
                scene_id='response', beat_index=0, quote=hidden),
        }
        for family, mutate in mutations.items():
            with self.subTest(family=family):
                response = review_control(source)
                mutate(response)
                # The old public API still accepts real source-plan quotes.
                scene_planning.validate_review(response, source)
                with self.assertRaises(StoryError):
                    scene_planning.validate_review(response, source, evidence_plan=view)

    def test_old_plan_without_beats_or_chapters_keeps_real_exchange_evidence(self):
        source = example_plan()
        view = scene_planning.review_view(source)
        response = review_control(view)
        scene_planning.validate_review(response, source, evidence_plan=view)
        self.assertEqual(response['chapter_checks'], [])
        for raw, projected in zip(source['scenes'], view['scenes']):
            self.assertEqual(projected['choice'], raw['choice'])
            self.assertEqual(projected['consequence'], raw['consequence'])
            self.assertEqual(projected['pressure'], raw['pressure'])

    def test_omitted_source_change_invalidates_receipt_without_changing_projected_input(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 1, 'target': 6000, 'max': None},
                'author_input': {'premise': '她递出票，他当面改签。'}}))
            llm = ProjectedPlanLLM()
            flow = StoryWorkflow(project, llm, max_calls=100)
            flow.develop()
            plan = flow.plan()
            previous = scene_planning.review_view(plan)
            previous_calls = len(llm.chief_inputs)
            changed = copy.deepcopy(plan)
            changed['scenes'][0]['purpose'] = '另一项未进入预审的策划自评。'
            self.assertEqual(scene_planning.review_view(changed), previous)
            flow._preflight_plan_chief(changed, project.contract())
            self.assertEqual(len(llm.chief_inputs), previous_calls + 1)
            receipt = read_json(project.root / 'plan/chief-preflight.json')
            self.assertEqual(receipt['binding']['source_plan_hash'], digest(changed))


if __name__ == '__main__':
    unittest.main()
