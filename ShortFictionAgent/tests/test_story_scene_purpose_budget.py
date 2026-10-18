"""Regressions for irrelevant procedures and uniform scene expansion.

Model answers below are explicit protocol controls, not literary benchmarks.
"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import scene_planning, target_reader
from tests.narrative_fixtures import chapter_checks_control, premise_checks_control
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow, scene_budget


def example_plan():
    return {'scenes': [
        {'scene_id': 'arrival', 'scene_role': 'transition', 'purpose': '她提前到了约定的门口',
         'goal': '见面', 'pressure': '雨开始下', 'choice': '她收好伞等他',
         'consequence': '两人在门口碰面', 'new_information': '他带了两杯热茶',
         'emotional_change': '紧张', 'dependencies': [], 'budget': 200},
        {'scene_id': 'response', 'scene_role': 'core', 'purpose': '试探被实际回应',
         'goal': '确认他想见她', 'pressure': '他昨天取消过约会', 'choice': '她把多带的票递过去',
         'consequence': '他接过票并当面改掉原来的返程车次',
         'new_information': '他愿意为这次同行留一整天', 'emotional_change': '从试探转为主动邀请',
         'dependencies': ['arrival'], 'budget': 2800},
        {'scene_id': 'payoff', 'scene_role': 'core', 'purpose': '兑现主动选择',
         'goal': '一起走出门口', 'pressure': '她还不敢牵手', 'choice': '她伸手接过他那杯茶',
         'consequence': '他把伞向她倾斜，两人一起走进雨里',
         'new_information': '两人约好下一次见面', 'emotional_change': '放松',
         'dependencies': ['response'], 'budget': 1000}],
        'main_question': '取消过约会的他，这次会主动留下与她同行吗？',
        'ending_resolution': '双方主动选择下一次见面', 'promises': [],
        'continuity_map': {'timeline': ['当天傍晚'], 'locations': ['门口'],
                           'knowledge': ['双方知道昨天取消过约会'], 'motives': ['想确认对方的选择']}}


def reading_allocation_control(plan):
    """Invented protocol control; this does not establish literary value."""
    scenes = plan['scenes']
    first = next((scene for scene in scenes if scene.get('scene_role') != 'transition'), scenes[0])
    first_index = scenes.index(first)
    return {'status': 'effective',
        'core_experience_quote': plan.get('core_reading_experience') or scene_planning.main_question(plan, required=True),
        'total_budget': sum(scene['budget'] for scene in scenes),
        'first_reward': {'scene_id': first['scene_id'], 'quote': first['consequence'],
            'budget_before': sum(scene['budget'] for scene in scenes[:first_index]),
            'within_scene_wait': {'before_reward': '协议控制：先递票再接票，不能把场景序号当等待。',
                'estimate_chars': None, 'basis': '协议控制：方案未细分节拍篇幅，不伪造场内位置。'},
            'action': 'keep', 'reason': '协议控制：引用实际回应，不证明读者喜欢。',
            'alternative': '协议控制：比较提前回应和保留准备过程，只测字段绑定。'},
        'expansion_choices': [{'scene_ids': [scene['scene_id']],
            'reading_role': scene.get('reading_role', 'support' if scene.get('scene_role') == 'transition' else 'core_experience'),
            'planned_budget': scene['budget'], 'action': 'keep',
            'alternative': '协议控制：压缩本场，只检验对照要求存在。',
            'compression_trial': {'retained_beats': [{'scene_id': scene['scene_id'],
                'action_quote': scene['choice'],
                'response_quote': '' if scene.get('scene_role') == 'transition' else scene['consequence']}],
                'compressed_route': '协议控制：保留行动与回应，将两者写在同一次交锋内。',
                'removable_repetition': '协议控制：合并同一选择的重复心理说明，不删实际回应。'},
            'preserved_value': '协议控制：原方案已经写明的选择。',
            'lost_value': '协议控制：原方案实际回应的展开空间。',
            'reason': '协议控制：仅测试覆盖和预算，不证明保留合理。'} for scene in scenes],
        'sequence_comparison': '协议控制：逐组覆盖，只验证同类任务比较字段存在。',
        'whole_story_tradeoff': '协议控制：比较整篇展开与等待，不构成真实文学验收。'}


def review_control(plan, contract=None):
    rows = []
    for scene in plan['scenes']:
        rows.append({'scene_id': scene['scene_id'], 'kind': scene['scene_role'],
            'status': 'effective', 'attempt_quote': scene['choice'],
            'response_quote': '' if scene['scene_role'] == 'transition' else scene['consequence'],
            'change_quote': '' if scene['scene_role'] == 'transition' else scene.get('new_information', scene['consequence']),
            'next_scene_effect': '协议控制：仅检查输入场景覆盖。',
            'loss_if_removed': '协议控制：不构成文学必要性证明。',
            'reason': '仅验证真实引用和完整覆盖。'})
    result = {'decision': 'pass', 'issues': [], 'scene_checks': rows,
            'chapter_checks': chapter_checks_control(plan),
            'reading_allocation_check': reading_allocation_control(plan),
            'main_question_check': {'status': 'effective',
                'question_quote': scene_planning.main_question(plan, required=True),
                'opening_scene_id': plan['scenes'][0]['scene_id'],
                'ending_scene_id': plan['scenes'][-1]['scene_id'],
                'ending_quote': plan['ending_resolution'], 'reason': '协议控制，仅验证首尾绑定。'},
            'premise_test': {'status': 'ordinary', 'reason': '普通约会场景的协议控制。',
                'without_setting_changes': '无超自然机制；仍需双方主动选择。',
                'evidence': [{'scene_id': 'response',
                              'quote': plan['scenes'][1]['consequence']}]}}
    if contract is not None:
        result['premise_test']['checks'] = premise_checks_control(contract, plan)
    return result


class ScenePurposeBudgetTests(unittest.TestCase):
    def test_continuation_accepts_each_defined_or_observed_question_field(self):
        questions = {
            'main_question': '作者明确给出的主问题',
            'story_question': '周四相见结束后，他们会主动选择彼此吗？',
            'opening_story_question': '我会不会为了八万元，把维修店、未结订单和客户资料一并交给收购方？',
            'question': '旧版规划的主问题',
        }
        for key, value in questions.items():
            with self.subTest(field=key):
                plan = example_plan()
                plan.pop('main_question')
                plan[key] = value
                before = copy.deepcopy(plan)
                self.assertEqual(scene_planning.continuation_context(plan, 0)['main_question'], value)
                self.assertEqual(plan, before)

    def test_continuation_question_priority_is_explicit_for_all_four_fields(self):
        plan = example_plan()
        keys = ('main_question', 'story_question', 'opening_story_question', 'question')
        for key in keys:
            plan[key] = key + '对应的主问题'
        for key in keys:
            with self.subTest(preferred_field=key):
                self.assertEqual(scene_planning.continuation_context(plan, 0)['main_question'], plan[key])
                del plan[key]

    def test_continuation_question_falls_back_past_null_empty_and_whitespace(self):
        keys = ('main_question', 'story_question', 'opening_story_question', 'question')
        for index, preferred in enumerate(keys):
            for empty in (None, '', ' \n\t '):
                with self.subTest(preferred_field=preferred, skipped_value=empty):
                    plan = example_plan()
                    plan.update({key: empty for key in keys[:index]})
                    plan.update({key: '低优先级问题' for key in keys[index + 1:]})
                    plan[preferred] = '  有正文依据的主问题  '
                    self.assertEqual(scene_planning.continuation_context(plan, 0)['main_question'],
                                     '有正文依据的主问题')

    def test_continuation_does_not_invent_question_when_all_aliases_are_empty(self):
        plan = example_plan()
        plan.update(main_question=None, story_question='', opening_story_question=' \n ', question='')
        plan['read_question'] = '候选追读问题不能冒充当前计划主问题'
        with self.assertRaisesRegex(StoryError, '缺少明确main_question'):
            scene_planning.continuation_context(plan, 0)

    def test_question_alias_protocol_invalidates_previous_planning_criteria(self):
        old_hash = digest({'protocol': 'scene-purpose-and-budget-v2-main-question',
                           'plan': scene_planning.PLAN_INSTRUCTION,
                           'review': scene_planning.REVIEW_INSTRUCTION})
        self.assertEqual(scene_planning.PROTOCOL, 'scene-purpose-and-budget-v15-explicit-expectation-coverage')
        self.assertNotEqual(scene_planning.criteria_hash(), old_hash)

    def test_actual_planner_and_chief_receive_realized_value_and_budget_comparison(self):
        """Verify transmission and real inputs, without scoring literary words."""
        from test_story_studio import ScriptedLLM, _prompt_object

        class PromptRecorder(ScriptedLLM):
            def __init__(self):
                super().__init__('协议控制正文。')
                self.prompts = {}

            def generate(self, prompt, task_type):
                self.prompts.setdefault(task_type, []).append(prompt)
                return super().generate(prompt, task_type)

        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 1, 'target': 6000, 'max': None},
                'author_input': {'premise': '她作出已经生效的工作选择，重新安排自己的生活'}}))
            llm = PromptRecorder()
            flow = StoryWorkflow(project, llm, max_calls=100)
            flow.develop()
            plan = flow.plan()
            planner_prompt = llm.prompts['story_plan'][0]
            chief_prompt = llm.prompts['chief_plan_review'][0]
            self.assertIn(scene_planning.PLAN_INSTRUCTION, planner_prompt)
            self.assertIn(scene_planning.REVIEW_INSTRUCTION, chief_prompt)
            self.assertIn(target_reader.PLAN_INSTRUCTION, planner_prompt)
            self.assertIn(target_reader.REVIEW_INSTRUCTION, chief_prompt)
            planner_input = _prompt_object(planner_prompt, ('contract', 'candidate', 'opening'))
            chief_input = _prompt_object(chief_prompt, ('contract', 'plan'))
            expected_entry = target_reader.entry_context(project.contract(), include_author_promise=True)
            self.assertEqual(planner_input['target_reader_context'], expected_entry)
            self.assertEqual(chief_input['target_reader_context'], expected_entry)
            self.assertFalse(expected_entry['enabled'])  # Generic prose keeps its own audience.
            self.assertEqual(chief_input['contract'], project.contract())
            self.assertEqual(chief_input['plan'], scene_planning.review_view(plan))
            self.assertEqual(chief_input['chapter_budget_context'],
                scene_planning.chapter_budget_context(plan))
            self.assertIn('本轮由你本人直接完成审读，不使用工具、不委派', chief_prompt)
            self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
            self.assertNotIn('story_plan_repair', llm.tasks)
            old_hash = digest({'protocol': 'scene-purpose-and-budget-v5-whole-story-reading-value',
                'plan': scene_planning.PLAN_INSTRUCTION, 'review': scene_planning.REVIEW_INSTRUCTION})
            self.assertNotEqual(scene_planning.criteria_hash(), old_hash)

    def test_short_transition_uses_its_plan_instead_of_equal_share(self):
        plan = example_plan()
        length = {'min': 3500, 'target': 4000, 'max': 5000}
        budget = scene_budget(length, 3, 0, 0, scenes=plan['scenes'])
        self.assertLess(budget['target'], 250)
        self.assertEqual(budget['min'], 1)
        self.assertEqual(budget['scene_role'], 'transition')
        core = scene_budget(length, 3, 1, 200, scenes=plan['scenes'])
        self.assertGreater(core['target'], 2700)
        self.assertGreater(core['target'], budget['target'] * 10)

    def test_legacy_positive_budgets_are_scaled_without_faking_scene_evidence(self):
        plan = example_plan()
        for scene in plan['scenes']:
            scene.pop('scene_role')
        before = copy.deepcopy(plan)
        length = {'min': 7000, 'target': 8000, 'max': None}
        budget = scene_planning.allocate_budget(length, plan['scenes'], 0, 0)
        self.assertLess(budget['target'], 450)
        self.assertEqual(plan, before)
        self.assertNotIn('dramatic_chain', plan['scenes'][0])

    def test_unbounded_long_story_keeps_planned_ending_after_target_exceeded(self):
        plan = example_plan()
        length = {'min': 30000, 'target': 45000, 'max': None}
        budget = scene_planning.allocate_budget(length, plan['scenes'], 2, 50000)
        self.assertIsNone(budget['max'])
        self.assertEqual(budget['min'], 1)
        self.assertGreater(budget['target'], 8000)

    def test_finite_ceiling_reserves_future_scenes_without_truncating(self):
        scenes = example_plan()['scenes']
        length = {'min': 3500, 'target': 4000, 'max': 5000}
        first = scene_planning.allocate_budget(length, scenes, 0, 0)
        self.assertLess(first['max'], 5000)
        ending = scene_planning.allocate_budget(length, scenes, 2, 3000)
        self.assertEqual(ending['min'], 500)
        self.assertEqual(ending['max'], 2000)
        with self.assertRaisesRegex(StoryError, '剩余篇幅'):
            scene_planning.allocate_budget(length, scenes, 2, 5000)

    def test_ending_does_not_absorb_missing_main_story_as_padding(self):
        with self.assertRaisesRegex(StoryError, '结构需退回规划层'):
            scene_planning.allocate_budget({'min': 3500, 'target': 4000, 'max': None},
                                          example_plan()['scenes'], 2, 200)

    def test_invalid_budget_is_not_silently_replaced_with_equal_allocation(self):
        for invalid in (0, -1, True, '200', None):
            plan = example_plan()
            plan['scenes'][0]['budget'] = invalid
            with self.subTest(invalid=invalid), self.assertRaisesRegex(StoryError, 'budget'):
                scene_planning.validate_budgets(plan, {'target': 4000})

    def test_quiet_reciprocity_and_brief_transition_can_pass_same_review(self):
        plan = example_plan()
        response = review_control(plan)
        self.assertEqual(scene_planning.validate_review(response, plan), response)

    def test_empty_pass_cannot_replace_scene_or_premise_review(self):
        plan = example_plan()
        with self.assertRaisesRegex(StoryError, '主问题'):
            scene_planning.validate_review({'decision': 'pass', 'issues': []}, plan)
        response = review_control(plan)
        response.pop('premise_test')
        with self.assertRaisesRegex(StoryError, '去设定'):
            scene_planning.validate_review(response, plan)

    def test_actual_quote_must_belong_to_corresponding_scene(self):
        plan = example_plan()
        response = review_control(plan)
        response['scene_checks'][1]['response_quote'] = plan['scenes'][2]['consequence']
        with self.assertRaisesRegex(StoryError, '对应场景'):
            scene_planning.validate_review(response, plan)

    def test_legacy_plan_cannot_escape_core_review_by_labeling_everything_transition(self):
        plan = example_plan()
        response = review_control(plan)
        for scene, row in zip(plan['scenes'], response['scene_checks']):
            scene.pop('scene_role')
            row.update(kind='transition', response_quote='', change_quote='')
        with self.assertRaisesRegex(StoryError, '全篇都标为过渡'):
            scene_planning.validate_review(response, plan)

    def test_missing_response_cannot_pass_or_escape_as_transition(self):
        plan = example_plan()
        response = review_control(plan)
        response['scene_checks'][1]['response_quote'] = ''
        with self.assertRaisesRegex(StoryError, '实际接招'):
            scene_planning.validate_review(response, plan)
        response['scene_checks'][1]['kind'] = 'transition'
        with self.assertRaisesRegex(StoryError, '改标过渡'):
            scene_planning.validate_review(response, plan)

    def test_structural_failure_must_route_back_to_real_plan_path(self):
        plan = example_plan()
        response = review_control(plan)
        response['scene_checks'][1]['status'] = 'needs_replan'
        with self.assertRaisesRegex(StoryError, '退回规划'):
            scene_planning.validate_review(response, plan)
        response.update(decision='revise', issues=[{'path': 'plan.scenes[1].choice',
            'reason': '协议负控制：本场需重新安排实际回应。', 'missing_link': '改变后续选择的接招。'}])
        self.assertEqual(scene_planning.validate_review(response, plan), response)
        response['issues'][0]['path'] = 'plan.scenes[99].choice'
        with self.assertRaisesRegex(StoryError, '真实方案'):
            scene_planning.validate_review(response, plan)

    def test_scene_continuation_contains_future_route_without_asserting_it_happened(self):
        plan = example_plan()
        orientation = scene_planning.continuation_context(plan, 0)
        self.assertEqual(orientation['current_scene_id'], 'arrival')
        self.assertEqual([row['scene_id'] for row in orientation['future_core_turns']],
                         ['response', 'payoff'])
        self.assertIn('不得当作已经发生', orientation['authority'])

    def test_native_scene_prompts_use_unequal_budgets_and_internal_fact_guidance(self):
        from test_story_studio import ScriptedLLM
        review_llm = ScriptedLLM('协议控制正文。')
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 3500, 'target': 4000, 'max': 5000},
                'author_input': {'premise': '她想知道昨天取消约会的他是否仍愿意同行'}}))
            flow = StoryWorkflow(project, None, whole_draft_limit=0)
            plan = example_plan()
            opening = {'text': '她把多带的票藏在口袋里。'}
            plan['opening_hash'] = digest(opening)
            inputs = []
            def write(task, prompt):
                if task == 'chief_editor_chapter_reader':
                    return review_llm.generate(prompt, task)
                self.assertEqual(task, 'write_story_scene')
                data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                inputs.append(data)
                return '字' * data['target_visible_chars']
            with patch.object(flow, '_selection', return_value=({'id': 'test'}, opening)), \
                    patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False), \
                    patch.object(flow, 'call_text', side_effect=write):
                flow._draft_text(plan)
            self.assertEqual(len(inputs), 3)
            self.assertLess(inputs[0]['target_visible_chars'], 250)
            self.assertGreater(inputs[1]['target_visible_chars'], 2700)
            self.assertIn('narrative_expression', inputs[0])
            self.assertIn('内部核对', inputs[0]['fact_use'])
            self.assertIn('future_core_turns', inputs[0]['whole_story_orientation'])

    def test_one_large_core_scene_splits_output_without_changing_story_scene_count(self):
        from test_story_studio import ScriptedLLM
        review_llm = ScriptedLLM('协议控制正文。')
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 8500, 'target': 9000, 'max': None},
                'author_input': {'premise': '一次试探改变下一次见面'}}))
            flow = StoryWorkflow(project, None, whole_draft_limit=0)
            plan = example_plan()
            plan['scenes'][1]['budget'] = 7800
            opening = {'text': '她拿出两张票。'}
            plan['opening_hash'] = digest(opening)
            inputs = []
            def write(task, prompt):
                if task == 'chief_editor_chapter_reader':
                    return review_llm.generate(prompt, task)
                self.assertEqual(task, 'write_story_scene')
                data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                inputs.append(data)
                unit = data.get('transport_unit') or {}
                marker = 'FIRST-PART-FACT' if unit.get('part') == 1 else '正文'
                return marker + '字' * (data['target_visible_chars'] - len(marker))
            with patch.object(flow, '_selection', return_value=({'id': 'test'}, opening)), \
                    patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False), \
                    patch.object(flow, 'call_text', side_effect=write):
                body = flow._draft_text(plan)
            core_inputs = [data for data in inputs if data['scene']['scene_id'] == 'response']
            self.assertEqual(len(core_inputs), 2)
            self.assertTrue(all(data['scene_count'] == 3 for data in inputs))
            self.assertTrue(all(data['target_visible_chars'] <= 5000 for data in core_inputs))
            self.assertFalse(core_inputs[0]['transport_unit']['scene_ending_allowed'])
            self.assertTrue(core_inputs[1]['transport_unit']['scene_ending_allowed'])
            self.assertIn('FIRST-PART-FACT', core_inputs[1]['previous_text'])
            self.assertIn('FIRST-PART-FACT', body)

    def test_interrupted_transport_reuses_completed_parts_with_source_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story',
                'author_input': {'premise': '一次较长的交锋'}}))
            flow = StoryWorkflow(project, None)
            scene_input = {'scene': {'scene_id': 'single-core'}, 'scene_index': 0,
                'scene_count': 1, 'target_visible_chars': 10000, 'previous_text': '已发生事实。'}
            prompt = '只写指定正文。\n' + json.dumps(scene_input, ensure_ascii=False, sort_keys=True)
            calls = []
            def write(task, prompt):
                data = json.JSONDecoder().raw_decode(prompt[prompt.index('{'):])[0]
                part = data['transport_unit']['part']
                calls.append(part)
                if calls == [1, 2]:
                    raise RuntimeError('transport result pending')
                return f'输出块{part}的真实动作。' * 10
            checkpoint = project.root / 'test-checkpoint'
            with patch.object(flow, 'call_text', side_effect=write):
                with self.assertRaisesRegex(RuntimeError, 'pending'):
                    flow._write_scene_transport_parts(prompt, scene_input, checkpoint)
                body = flow._write_scene_transport_parts(prompt, scene_input, checkpoint)
            self.assertEqual(calls, [1, 2, 2])
            self.assertIn('输出块1的真实动作', body)
            self.assertIn('输出块2的真实动作', body)

    def test_underwritten_main_story_saves_partial_and_requires_replan_before_ending(self):
        from test_story_studio import ScriptedLLM
        review_llm = ScriptedLLM('协议控制正文。')
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 3500, 'target': 4000, 'max': None},
                'author_input': {'premise': '两个人的真实回应'}}))
            flow = StoryWorkflow(project, None, whole_draft_limit=0)
            plan = example_plan()
            opening = {'text': '她拿出票。'}
            plan['opening_hash'] = digest(opening)
            write_tasks = []
            def write(task, prompt):
                if task == 'chief_editor_chapter_reader':
                    return review_llm.generate(prompt, task)
                self.assertEqual(task, 'write_story_scene')
                write_tasks.append(task)
                return '实际正文。' * 10
            with patch.object(flow, '_selection', return_value=({'id': 'test'}, opening)), \
                    patch('short_fiction_agent.story_studio.workflow.engagement_enabled', return_value=False), \
                    patch.object(flow, 'call_text', side_effect=write):
                with self.assertRaisesRegex(StoryError, '结构需退回规划层'):
                    flow._draft_text(plan)
            self.assertEqual(len(write_tasks), 2)
            self.assertEqual(review_llm.tasks, ['chief_editor_chapter_reader'] * 2)
            request = json.loads((project.root / 'structure_repair_request.json').read_text())
            self.assertEqual(request['status'], 'needs_replan')
            self.assertEqual(request['review_status'], 'pending')
            self.assertTrue(Path(request['partial_body_path']).is_file())

    def test_generate_cannot_send_missing_main_story_to_terminal_pass(self):
        from test_story_studio import ScriptedLLM
        class MissingStory(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == 'write_story_scene':
                    self.tasks.append(task_type)
                    return '未展开的场景摘要。'
                return super().generate(prompt, task_type)
        with tempfile.TemporaryDirectory() as directory:
            project = StoryProject(Path(directory) / 'story')
            project.create(StoryBrief.from_dict({'format': 'short_story', 'channels': ['generic_text'],
                'length': {'min': 6000, 'target': 7600, 'max': 9500},
                'author_input': {'premise': '她追查那封信并决定承担交换记忆的代价'}}))
            llm = MissingStory('不会使用的整篇稿')
            flow = StoryWorkflow(project, llm, max_calls=100, whole_draft_limit=0)
            flow.develop()
            with self.assertRaisesRegex(StoryError, '结构需退回规划层'):
                flow.generate(auto_revise=True)
            self.assertEqual(llm.tasks.count('chief_plan_review'), 1)
            self.assertEqual(llm.tasks.count('chief_editor_review'), 0)
            self.assertEqual(llm.tasks.count('story_edit'), 0)
            self.assertEqual(llm.tasks.count('repair_story_scene_length'), 0)
            self.assertFalse((project.root / 'generation_receipt.json').exists())
            request = json.loads((project.root / 'structure_repair_request.json').read_text())
            self.assertEqual(request['status'], 'needs_replan')
            self.assertEqual(request['review_status'], 'pending')


if __name__ == '__main__':
    unittest.main()
