"""Production prompt/cache regressions; scripted prose proves no reader appeal."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor, lead, lead_clarity
from short_fiction_agent.story_studio.contracts import StoryBrief, digest
from short_fiction_agent.story_studio.storage import StoryProject, read_json
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_manuscript_lead import assessed, diversity
from test_story_studio import ScriptedLLM, _prompt_object


FAILURE_ID = 'compressed_premise_example'
HONGKONG_STORY = (
    '我带着装有未来记录的手机，穿越到1990年的香港。兜里只剩八百港币。\n'
    '全港追捧的电影巨星、香港第一美人沈知棠正站在摄影棚的铁架下面。'
    '未来记录保存着未来三十年的历史记录，告诉我头顶的铁板十秒后会砸下来。\n'
    '我递给她字条，让她往后退三步，随后把她从砸落的铁板下救了出来。\n'
    '当晚我将仅有的八百港币全押在一匹冷门马上，四十二倍赔率使它变成三万四千。\n'
    '沈知棠找到我，追问我还知道多少秘密。我想发财，也想追她。'
    '历史记录能告诉我赛马的结果，却没有她今晚主动给出的吻。'
)
LONG_LEAD = (
    '我带着未来记录穿越到1990年的香港，兜里只剩八百港币。\n'
    '它让我去找全港追捧的电影巨星、香港第一美人沈知棠，'
    '告诉她十秒后头顶的铁板就会砸下来。\n'
    '她当我是骗子，十秒后，我把她从倒下的铁架旁拉开。\n'
    '当晚我将最后八百港币押在一匹冷门马上，四十二倍赔率让我拿回三万四千。\n'
    '沈知棠开始主动找我，追问我究竟还知道多少秘密。\n'
    '我当然想发财，也想追她。可手机保存的旧历史里没有我，'
    '更没有她今晚主动给出的吻。\n'
    '那些足以改写命运的记录，还能告诉我她为什么选择我吗？'
)


class LeadReceiver(ScriptedLLM):
    """Receive real workflow prompts and return deterministic transport fixtures."""
    def __init__(self):
        super().__init__(HONGKONG_STORY)
        self.prompts = []

    def generate(self, prompt, task_type):
        self.tasks.append(task_type)
        self.prompts.append((task_type, prompt))
        if task_type == 'lead_draft':
            return json.dumps({'text': LONG_LEAD}, ensure_ascii=False)
        if task_type == 'lead_material_read':
            data = _prompt_object(prompt, ('reading_turn', 'paragraphs'))
            rows = [r for r in data['paragraphs'] if r['text'].strip()]
            return json.dumps({'paragraph_ids': [r['id'] for r in rows[:3]]})
        if task_type == 'manuscript_lead_review':
            data = _prompt_object(prompt, ('lead_review_input',))['lead_review_input']
            story = lead.unmark_story_quotes(data['story'], data['story_quote_choices'])
            return json.dumps(assessed(data['lead'], story), ensure_ascii=False)
        if task_type == 'lead_comparative_ranking':
            data = _prompt_object(prompt, ('lead_ranking_input',))['lead_ranking_input']
            return json.dumps({
                'comparisons': [{'lead_id': r['id'], 'rank': i + 1}
                                for i, r in enumerate(data['leads'])],
                'recommended_id': data['leads'][0]['id'],
                'diversity': diversity(data['leads'])}, ensure_ascii=False)
        raise AssertionError(f'Unexpected live-call substitute task: {task_type}')


class LeadClarityTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project = StoryProject(Path(tmp.name) / 'story')
        self.project.create(StoryBrief.from_dict({
            'title': '带着未来记录穿回1990，它让我追香港第一美人',
            'format': 'short_story', 'channels': ['fanqie_short'],
            'point_of_view': '男性第一人称',
            'length': {'target': 24000, 'min': 1, 'max': None},
            'author_input': {'premise': '我穿越到1990年的香港，靠未来记录保存的未来记录赚钱并追求电影巨星。'}}))
        self.contract = self.project.contract()
        self.llm = LeadReceiver()
        self.flow = StoryWorkflow(self.project, self.llm, max_calls=40)

    def assert_case(self, research, *, expected=True):
        cases = research['compression_examples']
        self.assertLessEqual(len(cases), 1)
        self.assertEqual(FAILURE_ID in [c['id'] for c in cases], expected)

    def test_default_workflow_delivers_policy_and_matching_failure_without_200_char_cut(self):
        body = self.flow._write_single_lead(HONGKONG_STORY)
        self.assertEqual(self.llm.tasks, ['lead_draft'])
        prompt = self.llm.prompts[0][1]
        data = _prompt_object(prompt, ('contract', 'story', 'lead_research'))
        self.assertEqual(data['story'], HONGKONG_STORY)
        self.assertEqual(data['lead_research']['checks'], lead.CHECKS)
        self.assertIn(lead_clarity.POLICY, prompt)
        self.assert_case(data['lead_research'])
        self.assertGreater(len(LONG_LEAD), 200)
        self.assertEqual(lead.split(body), (LONG_LEAD, HONGKONG_STORY))
        # A cached result preserves the entire submitted lead rather than
        # regenerating a shorter one or slicing it to a nominal allowance.
        self.assertEqual(self.flow._write_single_lead(HONGKONG_STORY), body)
        self.assertEqual(self.llm.tasks, ['lead_draft'])
        result = read_json(next((self.project.root / 'lead_drafts').glob('*/result.json')))
        self.assertEqual(result['response']['text'], LONG_LEAD)

    def test_long_manuscript_route_keeps_policy_case_and_verbatim_materials(self):
        story = HONGKONG_STORY + '\n\n' + ''.join(
            f'第{i}段。' + '她提出新问题，我拿出证据再作选择。' * 18 + '\n\n'
            for i in range(145))
        self.assertGreater(len(story), self.llm.max_prompt_tokens * .7)
        body = self.flow._write_single_lead(story)
        reads = [_prompt_object(p, ('reading_turn', 'paragraphs'))
                 for task, p in self.llm.prompts if task == 'lead_material_read']
        self.assertGreaterEqual(len(reads), 2)
        self.assertEqual(''.join(row['text'] for turn in reads for row in turn['paragraphs']), story)
        prompt = next(p for task, p in self.llm.prompts if task == 'lead_draft')
        data = _prompt_object(prompt, ('contract', 'source_materials', 'lead_research'))
        self.assertNotIn('story', data)
        self.assertIn(lead_clarity.POLICY, prompt)
        self.assert_case(data['lead_research'])
        self.assertTrue(all(row['text'] == story[slice(*row['span'])]
                            for row in data['source_materials']))
        self.assertEqual(lead.split(body), (LONG_LEAD, story.strip()))

    def test_material_prompt_can_match_source_without_a_matching_title(self):
        contract = copy.deepcopy(self.contract)
        contract['title'] = '新故事'
        contract['author_input'] = {'premise': '主角争取事业与爱情'}
        prompt = lead.material_candidate_prompt(
            lead.story_paragraphs(HONGKONG_STORY), contract, {'scope': 'test-only'})
        data = _prompt_object(prompt, ('contract', 'source_materials', 'lead_research'))
        self.assert_case(data['lead_research'])

    def test_unrelated_male_romance_and_excluded_terms_do_not_retrieve_hongkong_case(self):
        generic = {'format': 'short_story', 'channels': ['fanqie_short'],
                   'genre': '男生情感', 'title': '我的合租室友每天都在预演如何向我表白',
                   'author_input': {'premise': '合租室友排练表白'}}
        for contract in (generic, {**generic,
                'avoid': ['香港', '沈知棠', '1990', '未来记录', '第一美人'],
                'notes': '不要写香港巨星，不写赛马，不走穿越'}):
            with self.subTest(contract=contract):
                research = lead._single_candidate_research(contract, '她对着空椅子练习表白，我却提前回了家。')
                self.assert_case(research, expected=False)

    def test_all_retained_writing_and_review_routes_share_clarity_policy(self):
        choices = [{'id': f'l{i}', 'text': LONG_LEAD,
                    'style': lead.STYLES[i], 'time_relation': '预告后回到片场',
                    'reader_question': '她为何主动找我', 'hook_mechanism': f'传输机制{i}',
                    'payoff_quote': HONGKONG_STORY[-24:]} for i in range(3)]
        prompts = {
            'three_candidates': lead.candidates_prompt(HONGKONG_STORY, self.contract, None),
            'independent_review': lead.review_prompt(HONGKONG_STORY, self.contract, text=LONG_LEAD),
            'comparison': lead.review_prompt(HONGKONG_STORY, self.contract, choices=choices),
            'chief': chief_editor.prompt(lead.assemble(LONG_LEAD, HONGKONG_STORY), self.contract),
        }
        self.assertIn(lead_clarity.POLICY, lead.CURRENT_POLICY)
        self.assertIn(lead_clarity.POLICY, lead.CHECKS['lead_pull'])
        research = lead.context(self.contract, 'review', HONGKONG_STORY)
        self.assertEqual(research['current_policy'], lead.CURRENT_POLICY)
        self.assertNotIn(lead.CURRENT_POLICY, research['principles'])
        for route, prompt in prompts.items():
            with self.subTest(route=route):
                self.assertIn(lead_clarity.POLICY, prompt)
        reviewed = _prompt_object(prompts['independent_review'], ('lead_review_input',))
        self.assertEqual(reviewed['lead_review_input']['lead'], LONG_LEAD)
        chief = _prompt_object(prompts['chief'], ('paragraphs', 'author_contract'))
        self.assertEqual(''.join(r['text'] for r in chief['paragraphs']).replace('\n', ''),
                         lead.assemble(LONG_LEAD, HONGKONG_STORY).replace('\n', ''))

    def test_policy_change_invalidates_completed_stepwise_review_cache(self):
        choices = [{'id': f'l{i}', 'text': LONG_LEAD + f'第{i}条传输测试。',
                    'style': lead.STYLES[i], 'time_relation': '预告后回到片场',
                    'reader_question': '她为何主动找我', 'hook_mechanism': f'传输机制{i}',
                    'payoff_quote': HONGKONG_STORY[-24:]} for i in range(3)]
        folder = self.project.root / 'shared-review-cache'
        old_hash = lead.criteria_hash()
        original = self.flow._review_lead_choices(
            HONGKONG_STORY, choices, folder=folder, force_stepwise=True)
        self.flow._review_lead_choices(HONGKONG_STORY, choices, folder=folder, force_stepwise=True)
        self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 3)
        with patch.object(lead, 'CURRENT_POLICY', lead.CURRENT_POLICY + '本次测试的新导语审读版本。'):
            self.assertNotEqual(lead.criteria_hash(), old_hash)
            updated = self.flow._review_lead_choices(
                HONGKONG_STORY, choices, folder=folder, force_stepwise=True)
            self.flow._review_lead_choices(HONGKONG_STORY, choices, folder=folder, force_stepwise=True)
            self.assertEqual(updated, original)
            self.assertEqual(self.llm.tasks.count('manuscript_lead_review'), 6)
            self.assertEqual(self.llm.tasks.count('lead_comparative_ranking'), 2)
        self.assertEqual(lead.criteria_hash(), old_hash)
        self.assertEqual(len(list(folder.glob('manuscript_lead_review-*.json'))), 6)

    def test_existing_research_is_retained_and_failure_is_an_additional_case(self):
        data = lead.library()
        # Freeze the pre-fix archive portion, allowing new rows to be appended.
        # Deliberate corrections to old evidence need an explicit test update.
        self.assertEqual(digest(data['principles'][:26]),
                         'fa1b9b9d4db3601ae27a12ca263bd4cebc80c848034953e402faa9ab66cd304f')
        self.assertEqual(digest(data['worked_examples'][:72]),
                         'b93bc15c077e1882954e6b0916dbbb8d35330a3035e4576317849c2a82049afb')
        case = next(row for row in data['counterexamples']
                    if isinstance(row, dict) and row.get('id') == FAILURE_ID)
        self.assertTrue(case['match_terms'])
        research = lead._single_candidate_research(self.contract, HONGKONG_STORY)
        self.assertEqual(research['library_fingerprint'], digest(data))
        self.assertEqual(len(research['sentence_examples']), 2)


if __name__ == '__main__':
    unittest.main()
