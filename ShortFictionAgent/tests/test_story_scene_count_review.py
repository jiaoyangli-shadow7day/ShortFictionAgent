"""Scene-unit protocol/gate controls, not a model scene-count accuracy test."""
import unittest
from unittest.mock import patch

from short_fiction_agent.story_studio import chief_editor as chief, narrative_audit as narrative
from short_fiction_agent.story_studio.chief_review import finish_review
from tests.narrative_fixtures import narrative_response


class SceneCountReviewTests(unittest.TestCase):
    def test_actual_reading_reread_and_synthesis_receive_scene_unit_boundaries(self):
        body = '她在店里邀我见面。\n我们去隔壁取票，她主动递来第二张。\n晚上回店，我答应一起去。'
        contract = {'scene_count': {'min': 2, 'max': 2}}
        prompt = chief.prompt(body, contract)
        raw = narrative_response(prompt, {'recommendation': 'ready', 'issues': [], 'facts': [],
            'observations': [], 'claims': [{'kind': 'prediction', 'subject': '本次见面',
                'statement': '两人本次应一起赴约。', 'comparison_scope': 'outcome',
                'assertion_ids': ['p0'], 'condition_ids': [], 'process_ids': [], 'outcome_ids': [],
                'status': 'pending', 'reason': '须核对实际赴约。'}]})
        reading = chief.validate_response(raw, body, require_facts=True)
        synthesis, _ = chief.synthesis_prompt(body, contract, [reading])
        reread = chief.claim_reread_plan(body, contract, [reading])['prompt']
        for stage, actual in (('reading', prompt), ('reread', reread), ('synthesis', synthesis)):
            with self.subTest(stage=stage):
                for rule in ('scene_count按独立剧情目标、接招、变化及因果判断剧情单元',
                             '换地点、时刻、章节或模型输出块不自动增加一场',
                             '不能用同一scene_id包住多场独立戏绕过作者场数要求',
                             '报超限须引用多出单元的原段与独立戏剧作用',
                             '有提供计划才对照计划单元',
                             '无计划只按实际正文判断，不声称已核验或超出计划',
                             '作者明确按时空或场标计数时遵从该定义'):
                    self.assertIn(rule, actual)

    def test_movement_and_time_changes_do_not_add_programmatic_scene_blockers(self):
        body = ('我在店里答应修好她的旧钟。\n'
                '她跟我去隔壁拿零件，递来另一枚齿轮。\n'
                '下午回店，我换好齿轮，她听见钟声后握住我的手。')
        contract = {'scene_count': {'min': 2, 'max': 2}}
        raw = narrative_response(chief.prompt(body, contract))
        checked = chief.validate_response(raw, body)
        calls = []
        final, _ = finish_review(body, contract, [checked], stage=lambda *a, **k: calls.append(a))
        self.assertEqual(final['recommendation'], 'ready')
        self.assertFalse(final['issues'])
        self.assertEqual(calls, [])

    def test_independent_units_cannot_hide_in_a_shared_scene_id(self):
        body = ('我在店里与客户争执押金，客户拒绝分期，我退了钱。\n'
                '第二天我向房东试租柜台，房东拒绝免租，我另付押金谈成。\n'
                '周末收购方要求转交名单，我拒签并失去报价。\n'
                '月底同行提出兼并，我交出独家设备换来保留门店。')
        contract = {'scene_count': {'min': 3, 'max': 3}}
        raw = narrative_response(chief.prompt(body, contract), {'recommendation': 'revise', 'claims': [],
            'observations': [], 'issues': [{'severity': 'blocker', 'scope': 'global',
                'evidence_ids': ['p0', 'p1', 'p2', 'p3'],
                'reason': '押金退还、谈租、拒绝收购和另一次兼并各有独立目标、接招与结果，第四单元超出三场要求。',
                'fix_intent': '合并独立交锋，不能靠共同scene_id隐藏第四场。'}]})
        checked = chief.validate_response(raw, body)
        final = narrative.final_gate(checked, body, [checked])
        self.assertEqual(final['recommendation'], 'revise')
        issue = chief.legacy_issues(final, body)[0]
        self.assertEqual(issue['repair_scope'], 'replan')
        self.assertEqual([row['text'] for row in issue['evidence']], body.splitlines())

    def test_explicit_author_time_space_definition_remains_binding(self):
        body = '我在家约她赴宴。\n她在店里买来两张票。\n周日我们到了展馆。'
        contract = {'scene_count': {'min': 2, 'max': 2},
            'author_input': {'premise': '本篇按每次时空切换计场，最多两场。'}}
        prompt = chief.prompt(body, contract)
        self.assertIn('按每次时空切换计场', prompt)
        raw = narrative_response(prompt, {'recommendation': 'revise', 'claims': [], 'observations': [],
            'issues': [{'severity': 'blocker', 'scope': 'global', 'evidence_ids': ['p0', 'p1', 'p2'],
                'reason': '作者明确按时空切换计场，三个独立时空违反两场上限。',
                'fix_intent': '按作者指定场标合并时空。'}]})
        checked = chief.validate_response(raw, body)
        self.assertEqual(narrative.final_gate(checked, body, [checked])['recommendation'], 'revise')

    def test_scene_unit_protocol_invalidates_old_review_criteria(self):
        current = chief.criteria_hash()
        for key, old in (('PROTOCOL', 'chief-editor-v2-narrative'),
                         ('SYNTHESIS_PROTOCOL', 'chief-synthesis-source-narrative-v17')):
            with self.subTest(key=key), patch.object(chief, key, old):
                self.assertNotEqual(chief.criteria_hash(), current)


if __name__ == '__main__':
    unittest.main()
