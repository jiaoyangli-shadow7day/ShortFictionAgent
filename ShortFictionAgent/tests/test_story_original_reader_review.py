"""Input and adverse-routing regressions; scripted labels are not reader proof."""
import copy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chapter_reader, chief_first_read, expectation_ledger, quality, chief_editor
from short_fiction_agent.story_studio.contracts import StoryError, digest
from short_fiction_agent.story_studio.storage import write_json
from tests.test_story_chapter_reader import response_for


CONTRACT = {
    'channels': ['fanqie_short'], 'title': '重回2018，我能查询十次未来',
    'audience': {'description': '喜欢重生脑洞、主动恋爱与逆袭回报的男性读者',
                 'expectations': ['阅历与有限查询产生实际优势', '她主动争取他'],
                 'private': '不可公开的内部受众注释'},
    'genre': {'primary': '男生情感', 'secondary': ['重生', '恋爱'],
              'custom_description': '有限查询与重生阅历驱动的恋爱爽文',
              'ending': '不可公开的题材结局'},
    'tone': '口语、鲜活、有欲望，不上价值课', 'point_of_view': 'first_person_male',
    'reader_promise': '优势让他争取更好的起点与她的主动偏爱；结局秘密不供初读',
    'author_input': {'entry_facts': ['主角回到2018年，只有十次查询机会'],
                     'premise': '不可公开的作者剧情解释',
                     'locked_facts': ['不可公开的未来事实']},
    'planned_reader_promise': {'main_question': '不可公开的未来方案主问题'},
    'ending': '不可公开的终局',
}


def payload(plan):
    return json.loads(plan['prompt'].rsplit('\n', 1)[1])


class OriginalReaderReviewTests(unittest.TestCase):
    def test_cold_and_chapter_reading_keep_original_public_position_without_future(self):
        body = '我查到第一份考卷。\n她走过来，把自己的志愿表递给我。'
        cold = chief_first_read.next_plan(body, CONTRACT)
        chapter = chapter_reader.make_plan(body, CONTRACT)
        for plan in (cold, chapter):
            context = payload(plan)['public_context']
            self.assertTrue(context['enabled'])
            self.assertEqual(context['audience'], CONTRACT['audience']['description'])
            self.assertEqual(context['audience_expectations'], CONTRACT['audience']['expectations'])
            self.assertEqual(context['genre']['custom_description'], CONTRACT['genre']['custom_description'])
            self.assertEqual(context['genre']['secondary'], ['重生', '恋爱'])
            self.assertEqual(context['tone'], CONTRACT['tone'])
            self.assertEqual(context['point_of_view'], CONTRACT['point_of_view'])
            self.assertEqual(context['entry_facts'], CONTRACT['author_input']['entry_facts'])
            self.assertNotIn('reader_promise', context)
            self.assertNotIn('不可公开', plan['prompt'])
            self.assertNotIn('结局秘密', plan['prompt'])
        self.assertEqual(payload(cold)['public_context'], payload(chapter)['public_context'])

    def test_original_expectation_changes_invalidate_reading_binding_but_future_does_not(self):
        body = '她主动坐到我身边。'
        changed = copy.deepcopy(CONTRACT)
        changed['audience']['expectations'][0] = '普通校园成长，无设定优势期待'
        future = copy.deepcopy(CONTRACT)
        future['planned_reader_promise'] = {'main_question': '另一个方案解释'}
        future['author_input']['locked_facts'] = ['另一套结局']
        future['reader_promise'] = '另一段作者解释'
        for builder in (chief_first_read.next_plan, chapter_reader.make_plan):
            original = builder(body, CONTRACT)
            self.assertNotEqual(builder(body, changed)['plan_hash'], original['plan_hash'])
            self.assertEqual(builder(body, future)['plan_hash'], original['plan_hash'])

    def test_ledger_has_separate_original_entry_and_non_authoritative_plan(self):
        body = '我有十次查询机会。\n她把手交给我，我关掉查询界面。'
        plan_interpretation = {'main_question': '他能否学会不用优势，尊重未知？'}
        plan = expectation_ledger.next_plan(body, CONTRACT, planned_reader_promise=plan_interpretation)
        data = payload(plan)
        self.assertEqual(data['original_entry_context']['reader_promise'], CONTRACT['reader_promise'])
        self.assertEqual(data['original_entry_context']['audience_expectations'],
                         CONTRACT['audience']['expectations'])
        self.assertEqual(data['planned_reader_promise'], plan_interpretation)
        self.assertIn('不能覆盖原始基准', plan['prompt'])
        self.assertIn('不能因方案改写主问题而漏记', plan['prompt'])
        full = json.loads(expectation_ledger._full_prompt(body, CONTRACT, {}).rsplit('\n', 1)[1])
        self.assertEqual(full['original_entry_context'], data['original_entry_context'])

    def test_quality_keeps_original_baseline_with_a_drifting_plan_or_without_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            project = SimpleNamespace(root=Path(folder))
            original = quality._planned_reader_promise(project, CONTRACT)
            self.assertEqual(original['original_entry_context']['reader_promise'], CONTRACT['reader_promise'])
            plan = {'main_question': '他能否学会尊重未知？', 'ending_resolution': '他不用余下查询。'}
            write_json(project.root / 'plan' / 'scenes.json', plan)
            interpreted = quality._planned_reader_promise(project, CONTRACT)
            self.assertEqual(interpreted['main_question'], plan['main_question'])
            self.assertEqual(interpreted['source_hash'], digest(plan))
            self.assertEqual(interpreted['original_entry_context'], original['original_entry_context'])
            self.assertIn('不能替换原始承诺', interpreted['authority'])

    def test_grounded_entry_payoff_loss_is_structural_even_if_model_says_continue(self):
        plan = chapter_reader.make_plan('我关掉剩下九格查询。\n我又答应以后先问她的意见。', CONTRACT,
            previous_text='我用第一次查询赢得与她同校的机会。\n她让我别替她决定，我答应了。')
        raw = response_for(plan)
        raw['cumulative'].update(status='thin', repair_scope='structure',
            reason='查询收益退出后，仍重复此前已经讲完的先问意见，未接住入场的优势与主动恋爱期待。',
            fix_intent='重建本章争取、接招与回报，保留具体偏爱，不能只换阶段标签。',
            evidence_ids=['before:p0', 'before:p1', 'current:p0', 'current:p1'])
        record = chapter_reader.validate_response(raw, plan)
        self.assertEqual(record['model_verdict'], 'continue')
        self.assertEqual(record['verdict'], 'replan')
        self.assertEqual(chapter_reader.revision_instructions(record)['route'], 'replan')
        # A quiet reciprocal reward remains valid; no query-count or hook-count gate was added.
        quiet = chapter_reader.make_plan('她牵住我的手，带我回家。', CONTRACT, is_final=True)
        self.assertEqual(chapter_reader.validate_response(response_for(quiet), quiet)['verdict'], 'continue')

    def test_previous_protocols_cannot_reuse_old_reading_approvals(self):
        body = '她把手交给我。'
        with patch.object(chapter_reader, 'PROTOCOL', 'chapter-reader-v2-reading-value-and-compression'):
            old_plan = chapter_reader.make_plan(body, CONTRACT)
        with self.assertRaises(StoryError):
            chapter_reader.validate_response(response_for(old_plan), old_plan)
        with patch.object(chief_first_read, 'PROTOCOL', 'chief-source-first-read-v2-reader-priority'):
            old_cold = chief_first_read.next_plan(body, CONTRACT)
        with self.assertRaisesRegex(StoryError, '协议绑定'):
            chief_first_read.validate_response({}, body, first_read_plan=old_cold)
        with patch.object(expectation_ledger, 'PROTOCOL', 'source-bound-expectation-lifecycle-v2-reader-priority'):
            old_ledger = expectation_ledger.next_plan(body, CONTRACT)
        with self.assertRaisesRegex(StoryError, '规则'):
            expectation_ledger.validate_response({}, body, ledger_plan=old_ledger)

    def test_previous_chapter_handoff_cannot_keep_approval_after_audience_or_criteria_change(self):
        body = '她把手交给我。'
        plan = chapter_reader.make_plan(body, CONTRACT)
        record = chapter_reader.validate_response(response_for(plan), plan)
        changed = copy.deepcopy(CONTRACT)
        changed['audience']['expectations'] = ['只看普通关系成长']
        with self.assertRaisesRegex(StoryError, '原始受众已变化'):
            chapter_reader.make_plan('我们一起回家。', changed,
                previous_text=body, previous_reviews=[record])
        with patch.object(chapter_reader, 'INSTRUCTION', chapter_reader.INSTRUCTION + '新审读标准'):
            with self.assertRaisesRegex(StoryError, '审读标准'):
                chapter_reader.make_plan('我们一起回家。', CONTRACT,
                    previous_text=body, previous_reviews=[record])

    def test_chief_prompt_deduplicates_equal_entry_requirements_without_losing_other_fields(self):
        original = copy.deepcopy(CONTRACT)
        planned = {'main_question': '他是否主动争取她？',
                   'original_entry_context': chief_editor.target_reader.entry_context(
                       original, include_author_promise=True)}
        packed = chief_editor._review_context(original, planned)
        context, author = packed['original_entry_context'], packed['author_contract']
        self.assertEqual(context, planned['original_entry_context'])
        self.assertEqual(author['audience'], {'private': original['audience']['private']})
        self.assertEqual(author['genre'], {'ending': original['genre']['ending']})
        self.assertEqual(author['author_input'], original['author_input'])
        for key in ('title', 'tone', 'point_of_view', 'reader_promise'):
            self.assertNotIn(key, author)
            self.assertEqual(context[key], original[key])
        self.assertEqual(packed['planned_reader_promise']['main_question'], planned['main_question'])
        self.assertEqual(packed['planned_reader_promise']['original_entry_context_ref'], 'original_entry_context')
        self.assertNotIn('original_entry_context', packed['planned_reader_promise'])
        self.assertEqual(original, CONTRACT)
        self.assertIn('original_entry_context', planned)
        different = copy.deepcopy(planned)
        different['original_entry_context']['reader_promise'] = '另一个版本的承诺'
        self.assertEqual(chief_editor._review_context(original, different)['planned_reader_promise'], different)

    def test_synthesis_ledger_id_packing_is_reversible_and_keeps_all_judgments(self):
        ledger = {'paragraph_ids': [f'p{i}' for i in range(500)],
                  'questions': [{'question': '能力会带来什么收益？'}],
                  'intervals': [{'reason': '这个区间仍在重复已解决的关系教育。', 'verdict': 'thin'}]}
        packed = chief_editor._ledger_context(ledger)
        ids = packed['paragraph_ids']
        self.assertEqual(ids, {'encoding': 'continuous_p_ids', 'first': 'p0', 'last': 'p499', 'count': 500})
        self.assertEqual([f'p{i}' for i in range(ids['count'])], ledger['paragraph_ids'])
        self.assertEqual(packed['questions'], ledger['questions'])
        self.assertEqual(packed['intervals'], ledger['intervals'])
        noncontinuous = {**ledger, 'paragraph_ids': ['p0', 'p2', 'p3']}
        self.assertEqual(chief_editor._ledger_context(noncontinuous), noncontinuous)


if __name__ == '__main__':
    unittest.main()
