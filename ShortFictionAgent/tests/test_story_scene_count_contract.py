import copy
import json
import tempfile
import unittest
from pathlib import Path

from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, visible_count
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow
from test_story_studio import ScriptedLLM, _prompt_object


def nine_scene_route():
    """Distinct action/response units for schema routing, not quality evidence."""
    interactions = [
        ('认出寄信人', '我拿旧信到店里询问她', '她否认笔迹，却认出信里的旧地址', '旧地址能找到当年的邮差'),
        ('追查寄出经过', '我请邮差辨认寄件日期', '邮差出示她替我寄信的存根', '她曾替我保管未寄出的信'),
        ('问清隐瞒原因', '我拿存根当面追问她', '她交出我当年退回的信封', '错失联系源于我自己的退信'),
        ('修复旧日误会', '我承认退信是害怕拖累她', '她拒绝一句道歉了事，要我重新邀请', '过去的解释不能替代当下选择'),
        ('正式邀请', '我带两张演出票来约她', '她答应，但要求我先解决书店停租', '她愿意约会也不肯放下自己的店'),
        ('保住书店', '我向房东提出分期付租', '房东拒绝赊账，接受先办一场活动', '活动收入可以换取续租机会'),
        ('完成读者活动', '我照旧策划卖旧书', '她改为让读者带信来读，并接到预订', '她的方案带来第一批稳定读者'),
        ('承担共同经营', '我提出出钱后替她管店', '她要求共同出资且各自保留工作', '合伙成立需要平等分担而非替她安排'),
        ('确认今后的关系', '我按约交出合伙方案和第二张票', '她签下方案，主动挽住我去看演出', '旧信遗憾变成双方主动选择的生活'),
    ]
    scenes = []
    for index, (goal, action, response, change) in enumerate(interactions):
        scene_id = f's{index}'
        scenes.append({'scene_id': scene_id, 'purpose': goal, 'goal': goal,
            'pressure': '此前选择留下的新问题需要对方接招', 'choice': action,
            'consequence': response, 'new_information': change,
            'emotional_change': '依据对方实际回应调整期待',
            'budget': 3000 if index < 8 else 6000,
            'dependencies': [f's{index - 1}'] if index else [],
            'scene_role': 'core', 'reading_role': 'core_experience',
            'dramatic_chain': {'attempt': action, 'response': response,
                'adjustment': f'我依据“{change}”继续下一步选择', 'change': change,
                'next_effect': interactions[index + 1][0] if index + 1 < len(interactions) else '旧信与共同生活闭合',
                'deletion_loss': f'会失去“{response}”这一实际回应'},
            'progression_beats': [{'reader_question': goal, 'action': action, 'response': response,
                'change': change, 'reward': response,
                'next_expectation': interactions[index + 1][0] if index + 1 < len(interactions) else '关系闭合'}]})
    return {'scenes': scenes, 'chapters': [{'chapter_id': f'chapter-{index + 1}',
                'title': f'第{index + 1}章', 'scene_ids': [scene['scene_id']]}
                for index, scene in enumerate(scenes)],
        'main_question': '我能否查清退信误会，与她重新作出当下的共同选择？',
        'core_reading_experience': '测试路线：经历查信、重新邀请、她带赢和共同选择的实际互动。',
        'premise_test': '旧信和退信存根改变追查路径及男主对她的理解。',
        'ending_resolution': '两人按平等方案经营书店，并一起赴约。',
        'promises': [{'promise': '重新共同选择', 'payoff_scene': 's8', 'required': True}],
        'continuity_map': {'timeline': ['查信后解开误会，再邀请、续租及合伙'],
            'locations': ['书店、邮局、房东办公室和演出场馆'],
            'knowledge': ['男主查存根后才知道当年被自己退信'],
            'motives': ['她要平等相处，也要保住自己的店']}}


class SceneCountContractTests(unittest.TestCase):
    def test_explicit_seven_to_eight_scenes_blocks_six(self):
        brief = StoryBrief.from_dict({'format': 'short_story', 'title': '测试',
            'reader_promise': '七场故事', 'length': {'target': 10500},
            'scene_count': {'min': 7, 'max': 8}})
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(brief)
            flow = StoryWorkflow(project, object())
            with self.assertRaisesRegex(StoryError, '场景方案数量'):
                flow._validate_compact_plan({'scenes': [{'scene_id': str(i)} for i in range(6)]},
                                            project.contract())

    def test_legacy_brief_omits_empty_scene_count(self):
        brief = StoryBrief.from_dict({'format': 'short_story', 'title': '测试',
            'reader_promise': '完整故事'})
        self.assertNotIn('scene_count', brief.effective_contract())

    def test_twenty_seven_thousand_target_honors_explicit_five_to_seven_scenes(self):
        # Exercise planning, actual preflight, and draft dispatch. Repeating only
        # the numerical bounds would miss the conflicting instruction to the LLM.
        for scene_count in (5, 7):
            with self.subTest(scene_count=scene_count), tempfile.TemporaryDirectory() as folder:
                project = StoryProject(Path(folder))
                project.create(StoryBrief.from_dict({'format': 'short_story',
                    'channels': ['generic_text'], 'scene_count': {'min': 5, 'max': 7},
                    'length': {'min': 1, 'target': 27000, 'max': None},
                    'author_input': {'premise': '林禾找回一封信'}}))
                class LongPlanLLM(ScriptedLLM):
                    max_prompt_tokens = 200000
                    def generate(inner, prompt, task_type):
                        if task_type == 'story_plan':
                            inner.planning_prompt = prompt
                            response = json.loads(super().generate(prompt, task_type))
                            template = response['scenes'][0]
                            response['scenes'] = []
                            for index in range(scene_count):
                                scene = copy.deepcopy(template)
                                scene.update(scene_id=f's{index}',
                                    budget=27000 // scene_count + (1 if index < 27000 % scene_count else 0))
                                response['scenes'].append(scene)
                            response['chapters'] = [{'chapter_id': f'chapter-{index + 1}',
                                'title': f'第{index + 1}章', 'scene_ids': [scene['scene_id']]}
                                for index, scene in enumerate(response['scenes'])]
                            return json.dumps(response, ensure_ascii=False)
                        if task_type == 'write_story_scene':
                            inner.tasks.append(task_type)
                            data = _prompt_object(prompt, ('scene', 'scene_count'))
                            return '人' * data['target_visible_chars']
                        return super().generate(prompt, task_type)
                llm = LongPlanLLM('人' * 27000)
                flow = StoryWorkflow(project, llm, max_calls=100, whole_draft_limit=30000)
                flow.develop()
                before = project.contract()
                plan = flow.plan()
                self.assertEqual(len(plan['scenes']), scene_count)
                self.assertIn('本篇场数范围为5至7场', llm.planning_prompt)
                self.assertNotIn('本篇至少需要8场', llm.planning_prompt)
                self.assertEqual(llm.tasks.count('story_plan'), 1)
                self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
                self.assertNotIn('story_plan_repair', llm.tasks)
                # The authored target remains intact; chapter headings can add
                # a few characters to the scripted target-sized prose blocks.
                count = visible_count(flow._draft_text(plan))
                self.assertGreaterEqual(count, 27000)
                self.assertLess(count, 27100)
                self.assertEqual(project.contract(), before)
                self.assertIsNone(before['length']['max'])
                for count in (4, 8):
                    invalid = {**plan, 'scenes': [plan['scenes'][0]] * count}
                    with self.assertRaisesRegex(StoryError, '场景方案数量'):
                        flow._validate_compact_plan(invalid, before)

    def test_default_scene_bounds_depend_on_format_not_target_length(self):
        for story_format, expected in (('short_story', (2, None)), ('micro_story', (1, 3))):
            for target in (300, 27000):
                with self.subTest(story_format=story_format, target=target):
                    contract = StoryBrief.from_dict({'format': story_format,
                        'length': {'target': target}, 'reader_promise': '找回信'}).effective_contract()
                    self.assertEqual(StoryWorkflow._scene_count_bounds(contract), expected)

    def test_default_twenty_seven_thousand_accepts_three_scenes_through_draft_dispatch(self):
        # Real plan/preflight/draft routing with scripted answers. This verifies
        # that output capacity does not force extra plot scenes, not prose quality.
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'channels': ['generic_text'], 'length': {'min': 25000, 'target': 27000, 'max': None},
                'author_input': {'premise': '林禾找回一封信'}}))
            class ThreeSceneLLM(ScriptedLLM):
                max_prompt_tokens = 200000
                def __init__(inner):
                    super().__init__('不会调用的整篇输出')
                    inner.dispatch_inputs = []
                def generate(inner, prompt, task_type):
                    if task_type == 'story_plan':
                        inner.planning_prompt = prompt
                        response = json.loads(super().generate(prompt, task_type))
                        template = response['scenes'][0]
                        response['scenes'] = []
                        for index, budget in enumerate((1000, 20000, 6000)):
                            scene = copy.deepcopy(template)
                            scene.update(scene_id=f's{index}', budget=budget)
                            response['scenes'].append(scene)
                        response['chapters'] = [{'chapter_id': f'chapter-{index + 1}',
                            'title': f'第{index + 1}章', 'scene_ids': [scene['scene_id']]}
                            for index, scene in enumerate(response['scenes'])]
                        return json.dumps(response, ensure_ascii=False)
                    if task_type == 'write_story_scene':
                        inner.tasks.append(task_type)
                        data = _prompt_object(prompt, ('scene', 'scene_count'))
                        inner.dispatch_inputs.append(data)
                        return '人' * data['target_visible_chars']
                    return super().generate(prompt, task_type)
            llm = ThreeSceneLLM()
            flow = StoryWorkflow(project, llm, max_calls=100, whole_draft_limit=0)
            flow.develop()
            before = project.contract()
            self.assertNotIn('scene_count', before)
            plan = flow.plan()
            self.assertEqual(len(plan['scenes']), 3)
            self.assertIn('本篇至少2场，不设固定场数上限', llm.planning_prompt)
            self.assertNotIn('短篇2-8场', llm.planning_prompt)
            self.assertNotIn('本篇场数范围为8至8场', llm.planning_prompt)
            self.assertEqual(llm.tasks.count('story_plan'), 1)
            self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
            self.assertNotIn('story_plan_repair', llm.tasks)
            count = visible_count(flow._draft_text(plan))
            self.assertGreaterEqual(count, 27000)
            self.assertLess(count, 27100)
            self.assertNotIn('write_short_story', llm.tasks)
            self.assertEqual({row['scene']['scene_id'] for row in llm.dispatch_inputs}, {'s0', 's1', 's2'})
            self.assertTrue(all(row['scene_count'] == 3 for row in llm.dispatch_inputs))
            core = [row for row in llm.dispatch_inputs if row['scene']['scene_id'] == 's1']
            self.assertEqual(len(core), 4)
            self.assertTrue(all(row['transport_unit']['story_scene_id'] == 's1' for row in core))
            self.assertTrue(all(row['target_visible_chars'] <= 5000 for row in core))
            self.assertEqual(project.contract(), before)
            self.assertIsNone(before['length']['max'])
            calls = len(llm.tasks)
            self.assertEqual(flow.plan(), plan)
            self.assertEqual(len(llm.tasks), calls)
            for count in (1,):
                invalid = {**plan, 'scenes': [plan['scenes'][0]] * count}
                with self.assertRaisesRegex(StoryError, '场景方案数量'):
                    flow._validate_compact_plan(invalid, before)

    def test_thirty_thousand_default_plans_nine_distinct_units_without_count_repair(self):
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'channels': ['generic_text'], 'length': {'target': 30000, 'max': None},
                'author_input': {'premise': '查清旧信误会，再与她共同经营书店'}}))
            class NineSceneLLM(ScriptedLLM):
                max_prompt_tokens = 200000
                def generate(inner, prompt, task_type):
                    if task_type == 'story_plan':
                        inner.tasks.append(task_type)
                        inner.planning_prompt = prompt
                        return json.dumps(nine_scene_route(), ensure_ascii=False)
                    return super().generate(prompt, task_type)
            llm = NineSceneLLM('尚未写正文')
            flow = StoryWorkflow(project, llm, max_calls=100)
            flow.develop()
            plan = flow.plan()
            self.assertEqual(len(plan['scenes']), 9)
            self.assertEqual(sum(scene['budget'] for scene in plan['scenes']), 30000)
            self.assertIn('本篇至少2场，不设固定场数上限', llm.planning_prompt)
            self.assertNotIn('2-8场', llm.planning_prompt)
            self.assertEqual(llm.tasks.count('story_plan'), 1)
            self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
            self.assertNotIn('story_plan_repair', llm.tasks)
            self.assertNotIn('write_story_scene', llm.tasks)
            # Unlimited count does not relax the existing source/schema gates.
            for defect, message in (('duplicate_id', '场景 ID 重复'),
                                    ('missing_response', 'dramatic_chain'),
                                    ('invalid_budget', 'budget必须是正整数')):
                with self.subTest(defect=defect):
                    bad = copy.deepcopy(plan)
                    if defect == 'duplicate_id':
                        bad['scenes'][-1]['scene_id'] = bad['scenes'][0]['scene_id']
                    elif defect == 'missing_response':
                        bad['scenes'][-1]['dramatic_chain'].pop('response')
                    else:
                        bad['scenes'][-1]['budget'] = 0
                    with self.assertRaisesRegex(StoryError, message):
                        flow._validate_generated_chief_plan(bad, project.contract())

    def test_explicit_two_to_eight_still_rejects_nine_distinct_units(self):
        brief = StoryBrief.from_dict({'format': 'short_story',
            'scene_count': {'min': 2, 'max': 8}, 'length': {'target': 30000},
            'reader_promise': '查清旧信后共同选择'})
        contract = brief.effective_contract()
        with tempfile.TemporaryDirectory() as folder:
            project = StoryProject(Path(folder))
            project.create(brief)
            flow = StoryWorkflow(project, object())
            with self.assertRaisesRegex(StoryError, '场景方案数量'):
                flow._validate_compact_plan(nine_scene_route(), contract)

    def test_author_range_has_no_eight_cap_but_requires_positive_integer_bounds(self):
        for bounds in ({'min': 9, 'max': 12}, {'min': 1, 'max': 1}):
            with self.subTest(bounds=bounds):
                brief = StoryBrief.from_dict({'format': 'short_story', 'scene_count': bounds,
                    'reader_promise': '查清旧信后共同选择'})
                self.assertEqual(brief.effective_contract()['scene_count'], bounds)
                self.assertEqual(StoryWorkflow._scene_count_bounds(brief.effective_contract()),
                                 (bounds['min'], bounds['max']))
        for bounds in ({'min': 0, 'max': 9}, {'min': 2, 'max': 1},
                       {'min': True, 'max': 9}, {'min': 2, 'max': False},
                       {'min': 2.0, 'max': 9}, {'min': 2, 'max': None}, {'min': 2}):
            with self.subTest(bounds=bounds), self.assertRaisesRegex(StoryError, 'scene_count'):
                StoryBrief.from_dict({'format': 'short_story', 'scene_count': bounds,
                    'reader_promise': '查清旧信后共同选择'})


if __name__ == '__main__':
    unittest.main()
