import json
import re
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from short_fiction_agent.story_studio.channels import StoryExporter, render_cards
from short_fiction_agent.story_studio.blueprint import normalize_blueprint, validate_blueprint
from short_fiction_agent.story_studio.contracts import StoryBrief, StoryError, digest
from short_fiction_agent.story_studio.inspiration import add_seed, discover_sources, load_seeds, prompt_cards
from short_fiction_agent.story_studio.quality import current_review, review_decision, fact_gate, review_spans, review_rubric_hash
from short_fiction_agent.story_studio.storage import StoryProject
from short_fiction_agent.story_studio.workflow import StoryWorkflow, scene_share
from short_fiction_agent.story_studio.cli import import_legacy
from short_fiction_agent.agents.submission_gate_agent import SubmissionGateAgent
from short_fiction_agent.evaluation.harness import compare_snapshots
from tests.narrative_fixtures import generated_scene_plan_control


def structure_fixture(paragraphs, verdict='sufficient', scope='none'):
    """Transport fixture only; semantic accuracy is tested with real model controls."""
    from short_fiction_agent.story_studio.whole_story import CRITERIA
    indices = list(dict.fromkeys([0, len(paragraphs) - 1]))
    result = {'reader_promise': '测试所选主问题', 'phase_chain': [
        {'paragraph_index': i, 'event': '测试阶段', 'prior_effect': '前场信息',
         'later_effect': '后场选择'} for i in indices], 'checks': [
        {'id': key, 'verdict': verdict, 'reason': '测试传输与门禁依据',
         'fix_intent': '保留' if scope == 'none' else '重建行动后果', 'repair_scope': scope,
         'evidence_paragraph_indices': indices} for key in CRITERIA]}
    result['checks'][-1]['scene_checks'] = [{
        'expectation': '传输测试的核心期待', 'tension': '传输测试的互动',
        'turn': '传输测试的选择', 'reason': '仅校验协议，非真人情绪判断',
        'verdict': 'enacted' if verdict == 'sufficient' else 'summarized',
        'beat_evidence': {role: indices for role in ('attempt', 'response', 'turn', 'payoff')}}]
    return result


def _prompt_object(prompt, required_keys):
    """Read actual task input, including when protocol JSON precedes it."""
    decoder = json.JSONDecoder()
    for index, character in enumerate(prompt):
        if character != '{':
            continue
        try:
            value, _ = decoder.raw_decode(prompt[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and set(required_keys).issubset(value):
            return value
    raise AssertionError(f'Transport fixture lacks task input: {required_keys}')


def entry_pack_fixture(option):
    """Complete synthetic sample for transport tests, not a semantic endorsement."""
    first = '林禾的信被摆在记忆店的柜台上，价签写着她最想留住的那一天。'
    event = '店主当面递来收据，要她先说出愿意交易哪段亲历记忆。'
    return {**option, 'weakness': '传输测试：具体因果仍由独立评判控制。',
        'working_title': f'我的信上了记忆店的价签{option["id"]}',
        'provisional_lead': {'first_sentence': first, 'concrete_event': event, 'text': first + event},
        'read_question': '林禾能否拿回信，又保住自己最想留住的记忆？',
        'turns': [
            {'event': '林禾拿着收据追问', 'before': '她不知道信被谁取走。',
             'after': '店主承认自己持有信，只接受亲历记忆。', 'new_action': '林禾提出交换一段普通记忆。'},
            {'event': '店主展示交易价签', 'before': '林禾以为普通记忆就能换回信。',
             'after': '价签明确交易的是与写信人相识的那一天。', 'new_action': '她从收据里找原主约定。'},
            {'event': '林禾读出原主留下的取信条件', 'before': '店主等她交出重要记忆。',
             'after': '双方按收据约定改换另一段亲历记忆。', 'new_action': '她付出约定代价，再拆开信。'},
        ], 'ending_payoff': '林禾实际拿回信，保住与写信人相识的那一天，接受另一段记忆的损失。'}


def entry_review_fixture(option):
    """Anchor every scripted judgment to this candidate's supplied field."""
    def assessment(fields):
        evidence = []
        for field in fields:
            value = option
            for key in field.split('.'):
                value = value[int(key)] if isinstance(value, list) else value[key]
            assert isinstance(value, str) and value.strip(), field
            evidence.append({'field': field, 'quote': value})
        return {'decision': 'pass', 'basis': '传输测试：验证原文锚点与门禁，不代表模型吸引力判断。',
                'evidence': evidence}
    return {'decision': 'pass',
        'click': assessment(['working_title', 'provisional_lead.first_sentence']),
        'readthrough': assessment(['provisional_lead.text', 'read_question']),
        'desire': assessment(['goal']),
        'turns': [{'turn_index': index, **assessment([f'turns.{index}.after'])}
                  for index in range(len(option['turns']))],
        'ending': assessment(['ending_payoff'])}


def reader_experience_fixture(reader_windows):
    """Pass transport fixture with real window IDs and paragraph indices only.

    These scripted observations test evidence binding, caching and control flow;
    they do not establish that the source is interesting or good prose.
    """
    assert reader_windows, 'Reader transport fixture requires actual windows'
    result = []
    for window in reader_windows:
        paragraphs = window['paragraphs']
        assert paragraphs, 'Reader transport fixture requires actual paragraphs'
        first, last = paragraphs[0], paragraphs[-1]
        indices = list(dict.fromkeys([first['paragraph_index'], last['paragraph_index']]))
        first_text, last_text = first['text'][:100], last['text'][:100]
        result.append({'window_id': window['window_id'], 'verdict': 'continue',
            'reason': '传输测试：固定正面控制验证窗口门禁，不代表真实读者评价。',
            'fix_intent': '传输测试保留当前输入；语义优劣由独立校准用例判断。',
            'question': {'status': 'active', 'text': f'传输测试锚点：当前段“{first_text}”之后发生什么。',
                         'evidence_paragraph_indices': indices},
            'pull': {'strength': 'sufficient',
                     'reason_to_continue': f'传输测试固定正控制：关注原文当前选择“{first_text}”。',
                     'expected_next_action': f'传输测试固定正控制：等待当前选择的接招，锚定“{last_text}”。',
                     'incentive': f'传输测试固定正控制：保留当前窗口中的信息“{first_text}”。',
                     'evidence_paragraph_indices': indices},
            'change': {'kind': 'understanding', 'before': f'传输测试起段：{first_text}',
                       'action': f'传输测试仅记录输入动作：{first_text}',
                       'response': f'传输测试仅记录输入回应：{last_text}',
                       'after': f'传输测试末段：{last_text}', 'evidence_paragraph_indices': indices},
            'most_skippable': {'paragraph_index': first['paragraph_index'], 'pressure': 'none',
                'necessity': 'needed', 'burden': 'tolerable',
                'reason': f'传输测试固定保留控制，锚定原文：{first_text}',
                'loss_if_removed': f'传输测试移除此段会失去该窗口输入：{first_text}',
                'compression': {'replacement': first['text'], 'interest_effect': 'preserved',
                                'reason': '传输测试原样保留控制，保留当前输入事实；不代表语义质量判断。'}}})
    return {'windows': result}


def reader_basis_fixture(reader_windows, reader_basis, *, status='supported'):
    """Source-bound transport control, never evidence of semantic accuracy."""
    from short_fiction_agent.story_studio.reader_experience import GROUNDING_FIELDS
    assert status in {'supported', 'inferred', 'unsupported'}
    assert reader_windows, 'Reader basis fixture requires actual windows'
    assert isinstance(reader_basis, list), 'Reader basis fixture requires actual claims'
    by_id = {row['window_id']: row for row in reader_basis}
    assert len(by_id) == len(reader_basis), 'Reader basis fixture requires unique windows'
    assert set(by_id) == {window['window_id'] for window in reader_windows}
    result = []
    for window in reader_windows:
        claims = by_id[window['window_id']]['claims']
        assert set(claims) == set(GROUNDING_FIELDS), 'Reader basis claims must cover all fields'
        assert 'strength' not in claims['pull'], 'Grounding must remain blind to interest strength'
        assert 'verdict' not in by_id[window['window_id']], 'Grounding must remain blind to verdict'
        first = window['paragraphs'][0]
        result.append({'window_id': window['window_id'], 'checks': [{
            'field': field, 'status': status,
            'reason': f'传输测试固定{status}控制：仅核对{field}协议与原文锚点“{first["text"][:100]}”，不证明语义成立。',
            'evidence_paragraph_indices': [first['paragraph_index']],
        } for field in GROUNDING_FIELDS]})
    return {'windows': result}


class ScriptedLLM:
    max_prompt_tokens = 50000

    def __init__(self, body):
        self.body = body
        self.tasks = []
        self.scene_number = 0

    def generate(self, prompt, task_type):
        self.tasks.append(task_type)
        if task_type in {'chapter_reader_review', 'chief_editor_chapter_reader'}:
            data = _prompt_object(prompt, ('chapter_paragraphs', 'observation_windows'))
            first = data['chapter_paragraphs'][0]['id']
            actual = next((row for row in data['chapter_paragraphs']
                           if not re.fullmatch(r'第[零〇一二三四五六七八九十百千万\d]+[章节卷][^。！？]*', row['text'].strip())),
                          data['chapter_paragraphs'][0])
            actual_id, actual_text = actual['id'], actual['text']
            note = {'text': '仅验证传输与流程，不代表真实阅读吸引力。', 'evidence_ids': [first]}
            return json.dumps({'verdict': 'continue', 'reader_expectation': note,
                'delivered_experience': note, 'next_pull': note,
                'windows': [{'window_id': w['window_id'], 'status': 'effective',
                    'reason': '传输正控制，不证明文学质量。', 'fix_intent': '保留',
                    'repair_scope': 'none', 'evidence_ids': [w['paragraph_ids'][0]]}
                    for w in data['observation_windows']],
                'cumulative': {'status': 'effective', 'reason': '传输正控制，不证明连读体验。',
                    'fix_intent': '保留', 'repair_scope': 'none', 'evidence_ids': [first]},
                'reading_value_check': {'status': 'effective', 'reason': '传输正控制，不证明正文不可压缩。',
                    'fix_intent': '保留', 'repair_scope': 'none', 'evidence_ids': [actual_id]},
                'compression_trial': {'action': 'keep', 'compact_version': actual_text,
                    'preserved_experience': '传输正控制：引用当前正文，不证明已保留真实核心体验。',
                    'lost_experience': '传输正控制：原样保留引用段，不主张存在压缩收益。',
                    'reason': '仅验证当前正文引用与流程，不证明文学必要性。',
                    'evidence_ids': [actual_id],
                    'retained_beats': [{'action_quote': actual_text, 'response_quote': actual_text,
                                       'evidence_ids': [actual_id]}],
                    'source_result_quote': actual_text, 'source_result_evidence_ids': [actual_id]},
                'actual_handoff': {'facts': [note], 'answered_questions': [],
                                   'pending_questions': [], 'relationship_changes': []}}, ensure_ascii=False)
        if task_type == 'chief_editor_first_read':
            from tests.narrative_fixtures import narrative_response
            return json.dumps(narrative_response(_prompt_object(prompt, ('paragraphs', 'reading_scope'))))
        if task_type in {'chief_editor_expectation_ledger',
                         'chief_editor_publication_expectation_ledger'}:
            # Source-bound transport fixture; semantic judgment is exercised
            # separately with real-model positive and negative controls.
            from tests.narrative_fixtures import narrative_response
            return json.dumps(narrative_response(_prompt_object(prompt, ('paragraphs', 'response_schema'))))
        if task_type in {'chief_editor_review', 'chief_editor_synthesis',
                         'chief_editor_publication', 'chief_editor_publication_synthesis'}:
            result = {'claims': [], 'observations': [], 'recommendation': 'ready', 'issues': []}
            if 'reading_turn' in prompt:
                result['facts'] = []
            from tests.narrative_fixtures import narrative_response
            return json.dumps(narrative_response(_prompt_object(prompt, ('paragraphs',)), result))
        if task_type == 'chief_plan_review':
            from tests.test_story_scene_purpose_budget import reading_allocation_control
            from tests.narrative_fixtures import chapter_checks_control, premise_checks_control
            data = _prompt_object(prompt, ('contract', 'plan'))
            scenes = data['plan']['scenes']
            # Explicit transport positive control. These judgments deliberately
            # do not assert that the synthetic plan is good fiction.
            return json.dumps({'decision': 'pass', 'issues': [],
                'chapter_checks': chapter_checks_control(data['plan']),
                'reading_allocation_check': reading_allocation_control(data['plan']), 'scene_checks': [{
                'scene_id': scene['scene_id'], 'kind': 'core', 'status': 'effective',
                'attempt_quote': scene['choice'], 'response_quote': scene['consequence'],
                'change_quote': scene.get('new_information', scene['consequence']),
                'next_scene_effect': '传输正控制：仅验证逐场覆盖协议。',
                'loss_if_removed': '传输正控制：引用输入场景，不证明文学必要性。',
                'reason': '传输正控制，不证明语义判断准确。'} for scene in scenes],
                'main_question_check': {'status': 'effective',
                    'question_quote': data['plan']['main_question'],
                    'opening_scene_id': scenes[0]['scene_id'], 'ending_scene_id': scenes[-1]['scene_id'],
                    'ending_quote': data['plan']['ending_resolution'],
                    'reason': '传输正控制：验证主问题与首尾方案绑定，不证明文学吸引力。'},
                'premise_test': {'status': 'active', 'reason': '传输正控制，不证明设定作用。',
                    'checks': premise_checks_control(data['contract'], data['plan']),
                    'without_setting_changes': '传输正控制：只验证证据绑定。',
                    'evidence': [{'scene_id': scenes[0]['scene_id'], 'quote': scenes[0]['choice']}]}}
                , ensure_ascii=False)
        if task_type == 'lead_draft':
            return json.dumps({'text': '我的信被摆上了记忆店的价签。店主说，要拿回它，我得先交出与写信人相识的那一天。'}, ensure_ascii=False)
        if task_type == 'reader_reward_review':
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"reader_rewards":'):])[0]
            return json.dumps({'checks': [{'id': row['id'], 'verdict': 'fulfilled',
                'repair_scope': 'none', 'reason': '测试夹具已有具体回报', 'fix_intent': '保留',
                'quotes': [data['body'].splitlines()[0]]} for row in data['reader_rewards']]}, ensure_ascii=False)
        if task_type in {'whole_story_challenge', 'whole_story_replan_challenge'}:
            return '{"claims":[]}'
        if task_type in {'whole_story_review', 'whole_story_plan_review'}:
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
            return json.dumps(structure_fixture(data['paragraphs']), ensure_ascii=False)
        if task_type == 'reader_experience_review':
            from short_fiction_agent.story_studio.reader_experience import PROTOCOL
            data = _prompt_object(prompt, ('protocol', 'reader_windows'))
            assert data['protocol'] == PROTOCOL
            return json.dumps(reader_experience_fixture(data['reader_windows']), ensure_ascii=False)
        if task_type == 'reader_basis_grounding':
            from short_fiction_agent.story_studio.reader_experience import GROUNDING_PROTOCOL, PROTOCOL
            data = _prompt_object(prompt, ('protocol', 'reader_protocol', 'reader_windows', 'reader_basis'))
            assert data['protocol'] == GROUNDING_PROTOCOL
            assert data['reader_protocol'] == PROTOCOL
            return json.dumps(reader_basis_fixture(data['reader_windows'], data['reader_basis']), ensure_ascii=False)
        if task_type == 'reader_basis_repair':
            _prompt_object(prompt, ('basis_repair',))
            raise AssertionError('依据修复测试须显式提供纠正后的审读响应；通用传输桩不能抹去未确认依据或拒稿。')
        if task_type == "story_candidates":
            options = [dict(id=i, protagonist="林禾", goal="找回信", obstacle="封锁",
                cost="失去信任", unique_element="记忆交易", core_scene="交换", ending="找到信",
                promise="找信", weakness="", viable=True) for i in range(4)]
            if prompt:
                contract = _prompt_object(prompt, ('channels', 'author_input'))
                if 'fanqie_short' in contract['channels']:
                    options = [entry_pack_fixture(option) for option in options]
            return json.dumps({"candidates": options}, ensure_ascii=False)
        if task_type == "candidate_comparative_review":
            data = _prompt_object(prompt, ('contract', 'candidates')) if prompt else None
            options = data['candidates'] if data is not None else [{'id': i} for i in range(4)]
            rows = [dict(candidate_id=option['id'], rank=i+1,
                strength="有找信行动", weakness="人物差异不足", execution_risk="记忆规则需要明确",
                feasibility=dict(decision="pass", basis="架空记忆交易待正文建立，当前候选无矛盾", blocking_claims=[]))
                for i, option in enumerate(options)]
            if data is not None and 'fanqie_short' in data['contract']['channels']:
                for row, option in zip(rows, options):
                    row['entry_review'] = entry_review_fixture(option)
            return json.dumps({"comparisons": rows, "recommended_id": options[0]['id']}, ensure_ascii=False)
        if task_type == "publication_preparation":
            from short_fiction_agent.story_studio.distribution import PREPARATION_CHECKS
            return json.dumps({"checks": [dict(id=k, verdict="pass", evidence_id="e0",
                reason="测试故事以林禾找信推进", revision="无需修改") for k in PREPARATION_CHECKS]}, ensure_ascii=False)
        if task_type == "opening_candidates":
            return json.dumps({"openings": [dict(id=f"opening-{i}", text="信被取走了", rationale="直接入事",
                fulfillment_cost="必须找到信") for i in range(3)], "recommended_id": "opening-0"}, ensure_ascii=False)
        if task_type == "opening_comparative_review":
            return json.dumps({"comparisons": [dict(opening_id=f"opening-{i}", rank=i+1,
                strength="信被取走形成具体疑问", weakness="人物动机尚未清楚", execution_risk="后文须回答信为何被取走",
                feasibility=dict(decision="pass", basis="信被取走不依赖未建立的制度权限", blocking_claims=[]))
                for i in range(3)], "recommended_id": "opening-0"}, ensure_ascii=False)
        if task_type == "story_blueprint":
            return json.dumps({"question": "信在哪里", "answer": "林禾找回信", "mechanism": "只能交易亲历记忆",
                "cost": "林禾失去一段记忆", "characters": ["林禾"], "locations": ["县城"],
                "objects": [{"name": "信", "holder_start": "店主", "holder_end": "林禾",
                             "rule": "交换记忆后交还"}],
                "beats": [{"scene_id": "s0", "time": "当天傍晚", "place": "县城记忆店",
                           "object_state": "信由店主交给林禾", "action": "林禾追查信", "evidence": "店主出示收据",
                           "inference": "信在店主手里", "choice": "交换记忆", "consequence": "找回信",
                           "emotional_change": "从焦虑转为决心，但仍惧怕失去记忆",
                           "budget": 900}]}, ensure_ascii=False)
        if task_type == "story_blueprint_repair":
            return self.generate(prompt, "story_blueprint")
        if task_type == "story_plan":
            scene_count = 1 if '"format": "micro_story"' in prompt else 4
            return json.dumps(generated_scene_plan_control({"scenes": [dict(scene_id=f"s{i}", purpose="发现", goal="找信", pressure="封锁",
                choice="交换", consequence="找回", new_information="代价", emotional_change="改变",
                budget=1900, dependencies=[], progression_beats=[{
                    "reader_question": "信在哪", "action": "询问店主", "response": "店主出示持有信的收据", "change": "发现收据",
                    "reward": "确认线索", "next_expectation": "交换需要什么代价"}, {
                    "reader_question": "是否愿意付代价", "action": "作出交换选择", "response": "店主接受约定记忆并交还信", "change": "找回信",
                    "reward": "主问题得到回应", "next_expectation": "面对记忆缺失"}]) for i in range(scene_count)],
                "main_question": "林禾能否拿回信，又是否愿意失去写信人的记忆？",
                "ending_resolution": "找回信", "promises": [{"promise": "信在哪里",
                    "payoff_scene": "s0", "required": True}],
                "continuity_map": {"timeline": ["失信后调查"], "locations": ["县城"],
                    "knowledge": ["林禾起初不知道信在哪"], "motives": ["林禾想找回信"]}}), ensure_ascii=False)
        if task_type == "blueprint_audit":
            return '{"decision":"pass","issues":[]}'
        if task_type == "serial_plan":
            return json.dumps({"episodes": [dict(episode_id=f"e{episode}",
                local_question="本集线索是什么", local_resolution="找到一条线索",
                scenes=[dict(scene_id=f"e{episode}s{scene}", purpose="发现", goal="找信",
                    pressure="封锁", choice="交换", consequence="找到线索", new_information="代价",
                    emotional_change="改变", budget=2000, dependencies=[])
                    for scene in range(1, 4)]) for episode in range(1, 4)],
                "main_question": "林禾能否找回失去的信？", "ending_resolution": "找回信", "promises": [],
                "continuity_map": {"timeline": ["调查再找回"], "locations": ["县城"],
                    "knowledge": ["线索逐集增加"], "motives": ["林禾想找回信"]}}, ensure_ascii=False)
        if task_type == "write_micro_story":
            return self.body
        if task_type == "write_short_story":
            return self.body
        if task_type == "write_story_scene":
            self.scene_number += 1
            return f"第{self.scene_number}场，林禾开始调查。" + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 80
        if task_type == "story_edit":
            return '{"back_to_plan":false,"edits":[]}'
        if task_type == "scene_progression_review":
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"previous_text":'):])[0]
            return json.dumps({"progression_observations": [{
                "window_id": w["window_id"], "verdict": "flowing", "reader_question": "信在哪",
                "change": "测试桩记录人物选择", "reward": "测试桩记录线索回应", "next_expectation": "选择后果",
                "reason": "测试桩，不代表模型或真人判断", "fix_intent": "保留",
                "evidence_paragraph_indices": [w["paragraph_start"]]}
                for w in data["progression_windows"]], "actual_handoff": {
                    "facts": [{"text": "测试桩的已发生事件", "paragraph_index": 0}],
                    "answered_questions": [], "pending_questions": []},
                'promise_alignment': {'verdict':'aligned','paragraph_index':0,
                    'reason':'测试桩：找信行动与作者承诺一致','fix_intent':'保留'}}, ensure_ascii=False)
        if task_type == "cold_read_review":
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"audience":'):])[0]
            return json.dumps({"issues": [], "reader_question": "信在哪", "drop_point": None,
                "want_to_continue": True, "progression_observations": [{
                    "window_id": w["window_id"], "verdict": "flowing", "reader_question": "信在哪",
                    "change": "人物作出选择", "reward": "线索得到回应", "next_expectation": "选择的后果",
                    "reason": "测试桩的有效推进判断", "fix_intent": "无需修改",
                    "evidence_paragraph_indices": [w["paragraph_start"]]}
                    for w in data.get("progression_windows", [])]}, ensure_ascii=False)
        if task_type == "continuity_review":
            data = json.JSONDecoder().raw_decode(prompt[prompt.index('{"contract":'):])[0]
            return json.dumps({"dimensions": {key: 3 for key in (
                "opening_pull", "causal_progress", "character_specificity", "payoff",
                "economy", "coherence", "voice", "channel_fit")},
                "dimension_evidence": {key: "有明确选择和结果" for key in (
                "opening_pull", "causal_progress", "character_specificity", "payoff",
                "economy", "coherence", "voice", "channel_fit")}, "issues": [],
                "progression_confirmations": [{"window_id": w["window_id"], "verdict": "context_resolves",
                    "reason": "测试桩复核关系回应有效", "fix_intent": "保留",
                    "paragraph_index": data["paragraphs"].index(w["evidence"][0]["quote"])}
                    for w in data.get("progression_suspects", [])]})
        if task_type == "story_fact_review":
            return json.dumps({"checks": [{"fact_id": "lost_letter", "verdict": "supported",
                "quote": "林禾发现信不见了", "reason": "正文写明信不见"}]}, ensure_ascii=False)
        if task_type == "causal_challenge":
            return '{"issues":[]}'
        if task_type == "channel_metadata":
            return '{"titles":["找回那封信"],"synopsis":"一封信引发一次选择。"}'
        if task_type == "carousel_adapt":
            return json.dumps({"cards": [dict(title=f"第{i}页", text=f"第{i}页：林禾找信。",
                                              source_quote="林禾") for i in range(1, 11)]}, ensure_ascii=False)
        if task_type == "carousel_review":
            return '{"passed":true,"issues":[]}'
        raise AssertionError(task_type)


class StoryStudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = StoryProject(Path(self.temp.name) / "story")
        self.brief = StoryBrief.from_dict({"genre": {"primary": "县城科幻推理",
            "custom_description": "县城中可交易记忆，但只能转移亲历过的记忆"},
            "format": "micro_story", "length": {"target": 900, "min": 600, "max": 1100},
            "story_engine": "mystery_reveal", "author_input": {"premise": "林禾找一封信",
            "locked_facts": []}, "world": {"rule": "只能转移亲历过的记忆"}})
        self.project.create(self.brief)
        self.body = "林禾发现信不见了。" + "她追查记忆交易的线索，决定用自己的记忆换回那封信。" * 31 + "她找回信，也记得代价。"
        self.llm = ScriptedLLM(self.body)
        self.flow = StoryWorkflow(self.project, self.llm)

    def use_deep_review(self):
        # These tests exercise the explicitly retained legacy multi-review protocol.
        import yaml
        from short_fiction_agent.story_studio.storage import write_text
        raw = self.project.brief().to_dict()
        raw['review_mode'] = 'deep_review'
        self.brief = StoryBrief.from_dict(raw)
        write_text(self.project.brief_path, yaml.safe_dump(raw, allow_unicode=True))

    def test_micro_story_full_flow_and_exports(self):
        self.assertEqual(self.brief.support_level, "experimental")
        self.assertEqual(self.project.contract()["world"]["rule"], "只能转移亲历过的记忆")
        self.flow.develop()
        report = self.flow.generate()
        self.assertEqual(report["decision"], "passed")
        self.assertTrue(report["length"]["within_range"])
        self.assertEqual(report["review_coverage"]["spans"], [[0, len(self.project.active_draft()[1])]])
        self.assertEqual(len(list((self.project.root / "memory" / "commits").glob("*.json"))), 1)
        exporter = StoryExporter(self.project, self.flow.call_json)
        generic = exporter.export("generic_text")
        self.assertTrue((generic / "manifest.json").exists())
        cards = exporter.export("douyin_carousel")
        self.assertEqual(len(list((cards / "cards").glob("[0-9]*.png"))), 10)
        self.assertTrue((cards / "cards" / "contact-sheet.png").exists())
        self.assertEqual(len(json.loads((cards / "manifest.json").read_text())["files_sha256"]),
                         len([p for p in cards.rglob("*") if p.is_file()]) - 1)
        self.assertNotIn("校园", json.dumps(self.project.contract(), ensure_ascii=False))

    def test_inspiration_sources_are_traceable_and_do_not_inject_page_text(self):
        author = add_seed(self.project.root, source_type="author_note",
                          summary="我在雨天见过一个人归还十年前借走的伞。")
        self.assertEqual(author["rights_status"], "owned")
        with self.assertRaises(StoryError):
            add_seed(self.project.root, source_type="public_fact", summary="一则可核实的公共记录事实。",
                     url="https://127.0.0.1/private")
        with self.assertRaises(StoryError):
            add_seed(self.project.root, source_type="licensed_work", summary="一篇他人小说的设定摘要。",
                     url="https://1.1.1.1/work", rights_status="commercial_license")
        unverified = add_seed(self.project.root, source_type="licensed_work",
                              summary="一篇尚未确认改编权的他人小说只作来源登记。",
                              url="https://1.1.1.1/work")
        cards = prompt_cards(load_seeds(self.project.root))
        self.assertEqual(cards[0]["id"], author["id"])
        self.assertNotIn("source_url", cards[0])
        self.assertNotIn(unverified["id"], [card["id"] for card in cards])
        with self.assertRaisesRegex(StoryError, "已经导入"):
            add_seed(self.project.root, source_type="author_note",
                     summary="我在雨天见过一个人归还十年前借走的伞。")

    def test_discovery_keeps_only_requested_public_domain(self):
        class Response:
            content = ("<rss><channel><item><title>短故事创作指南</title>"
                       "<link>https://gov.cn/article/123</link>"
                       "<description>公开课程</description></item>"
                       "<item><title>站外复制</title><link>https://other.example/article</link>"
                       "</item></channel></rss>").encode()

            def raise_for_status(self):
                pass

        with patch("short_fiction_agent.story_studio.inspiration._check_public_url"), \
             patch("short_fiction_agent.story_studio.inspiration.requests.get", return_value=Response()):
            result = discover_sources(self.project.root, query="短故事", domain="gov.cn")
        self.assertEqual(len(result["results"]), 1)
        self.assertEqual(result["status"], "unverified_search_results")
        self.assertEqual(load_seeds(self.project.root), [])

    def test_official_catalog_discovery_extracts_article_links(self):
        class Response:
            content = b"<html></html>"
            text = ('<a class="title" href="https://security.zijieapi.com/api/link?'
                    'targetUrl=https%3A%2F%2Ffanqienovel.com%2Fwriter%2Fzone%2Farticle%2F123">'
                    '短故事创作指南</a>')

            def raise_for_status(self):
                pass

        with patch("short_fiction_agent.story_studio.inspiration._check_public_url"), \
             patch("short_fiction_agent.story_studio.inspiration.requests.get", return_value=Response()):
            result = discover_sources(self.project.root, query="短故事", domain="fanqienovel.com")
        self.assertEqual(result["results"][0]["url"],
                         "https://fanqienovel.com/writer/zone/article/123")
        self.assertEqual(result["status"], "official_catalog_titles_unverified_content")

    def test_seeded_development_tracks_sources_and_invalidates_old_selection(self):
        first = add_seed(self.project.root, source_type="author_note",
                         summary="十年前借出的伞今天忽然被归还，伞骨上刻着陌生名字。")

        class SeededLLM(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == "story_candidates":
                    self.tasks.append(task_type)
                    assert first["id"] in prompt
                    data = json.loads(super().generate(prompt, task_type))
                    for index, candidate in enumerate(data["candidates"]):
                        candidate["seed_ids"] = [first["id"]] if index else []
                    return json.dumps(data, ensure_ascii=False)
                return super().generate(prompt, task_type)

        flow = StoryWorkflow(self.project, SeededLLM(self.body))
        result = flow.develop()
        self.assertEqual(result["candidate"]["seed_ids"], [])
        self.assertEqual(json.loads((self.project.root / "candidates.json").read_text())
                         ["candidates"][1]["seed_ids"], [first["id"]])
        add_seed(self.project.root, source_type="author_note",
                 summary="有人在拆旧屋时发现一张从未兑现的取货票。")
        with self.assertRaisesRegex(StoryError, "灵感池已更改"):
            flow.plan()

    def test_free_idea_development_does_not_lock_a_nonexistent_premise(self):
        project = StoryProject(Path(self.temp.name) / "free")
        project.create(StoryBrief.from_dict({"genre": {"primary": "mystery"},
            "reader_promise": "一个有因果回报的完整悬疑短故事",
            "author_input": {"premise": "", "locked_facts": []}}))

        class FreeLLM(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == "story_candidates":
                    assert "自由选题" in prompt
                    assert "保留作者前提" not in prompt
                return super().generate(prompt, task_type)

        self.assertEqual(StoryWorkflow(project, FreeLLM(self.body)).develop()
                         ["selection"]["candidate_index"], 0)

    def test_blueprint_normalizes_descriptive_role_entries(self):
        raw = json.loads(ScriptedLLM(self.body).generate("", "story_blueprint"))
        raw["characters"] = [{"name": "林禾", "role": "主角"}]
        raw["locations"] = [{"name": "县城", "role": "行动地点"}]
        normalized = normalize_blueprint(raw)
        validate_blueprint(normalized)
        self.assertEqual(normalized["characters"], ["林禾：主角"])
        normalized["beats"][0].pop("object_state")
        with self.assertRaises(StoryError):
            validate_blueprint(normalized)

    def test_stale_review_cannot_export(self):
        self.flow.develop()
        self.flow.generate()
        prior_package = StoryExporter(self.project).export("generic_text")
        record, body = self.project.active_draft()
        self.project.save_draft(body + "改", artifact_id=record["artifact_id"],
                                parent_revision=record["revision"])
        with self.assertRaises(StoryError):
            current_review(self.project)
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export("generic_text")
        self.assertEqual(json.loads((prior_package / "status.json").read_text())["state"], "stale")
        self.assertTrue(list((self.project.root / "memory" / "commits").glob("*.status.json")))

    def test_tampered_review_decision_cannot_export(self):
        self.flow.develop()
        self.flow.generate()
        review_path = next((self.project.root / "reviews").glob("*.json"))
        report = json.loads(review_path.read_text())
        report["blockers"] = ["manually-added"]
        review_path.write_text(json.dumps(report, ensure_ascii=False))
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export("generic_text")

    def test_bad_review_quote_is_blocker_without_memory(self):
        self.use_deep_review()
        self.flow.develop()
        self.project.save_draft(self.body)
        def bad_review(task, prompt):
            if task == "cold_read_review":
                return {"issues": [{"quote": "并不存在的句子", "severity": "blocker"}]}
            if task == "causal_challenge":
                return {"issues": []}
            return {"dimensions": {key: 3 for key in (
                "opening_pull", "causal_progress", "character_specificity", "payoff",
                "economy", "coherence", "voice", "channel_fit")},
                "dimension_evidence": {key: "有明确选择和结果" for key in (
                "opening_pull", "causal_progress", "character_specificity", "payoff",
                "economy", "coherence", "voice", "channel_fit")}, "issues": []}
        from short_fiction_agent.story_studio.quality import StoryReviewer
        report = StoryReviewer(self.project, bad_review).review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertEqual(report["review_integrity_errors"][0]["reason"], "评审提供的引文不在正文，不能用另一段替代")
        self.assertEqual(self.project.active_draft()[0]["state"], "revise_required")
        self.assertFalse((self.project.root / "memory" / "commits").exists())

    def test_malformed_review_fields_become_integrity_blockers(self):
        self.use_deep_review()
        self.project.save_draft(self.body)
        from short_fiction_agent.story_studio.quality import StoryReviewer
        report = StoryReviewer(self.project, lambda task, prompt: {
            "issues": "not-an-array", "dimensions": "bad", "dimension_evidence": None
        }).review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertTrue(any(item.startswith("review_integrity:") for item in report["blockers"]))
        with self.assertRaises(StoryError):
            StoryExporter(self.project).export("generic_text")

    def test_numbered_paragraph_issue_is_anchored_to_source(self):
        from short_fiction_agent.story_studio.quality import ground_issue
        body = "第一段。\n\n第二段有问题。\n"
        issue = ground_issue({"paragraph_index": 1, "severity": "warning"}, body)
        self.assertEqual(issue["quote"], "第二段有问题。")
        self.assertEqual(body[slice(*issue["span"])], issue["quote"])

    def test_causal_challenge_blocks_an_unsupported_core_inference(self):
        self.use_deep_review()
        self.project.save_draft(self.body)
        from short_fiction_agent.story_studio.quality import StoryReviewer
        def challenged(task, prompt):
            if task == "causal_challenge":
                return {"issues": [{"paragraph_index": 0, "severity": "blocker",
                    "reason": "核心线索不足", "missing_link": "补充物证"}]}
            if task == "continuity_review":
                return {"dimensions": {key: 3 for key in (
                    "opening_pull", "causal_progress", "character_specificity", "payoff",
                    "economy", "coherence", "voice", "channel_fit")},
                    "dimension_evidence": {key: "有选择" for key in (
                    "opening_pull", "causal_progress", "character_specificity", "payoff",
                    "economy", "coherence", "voice", "channel_fit")}, "issues": []}
            return {"issues": []}
        report = StoryReviewer(self.project, challenged).review()
        self.assertEqual(report["decision"], "revise_required")
        self.assertEqual(report["issues"][-1]["category"], "coherence")

    def test_card_overflow_leaves_no_png(self):
        folder = Path(self.temp.name) / "overflow"
        with self.assertRaises(StoryError):
            render_cards([{"title": "过长", "text": "汉" * 1000}], folder)
        self.assertFalse(list(folder.glob("*.png")))

    def test_review_contract_rejects_shortened_and_stale(self):
        report = {"content_hash": digest(self.body), "contract_hash": digest(self.project.contract()),
                  "review_rubric_hash": review_rubric_hash(),
                  "review_coverage": {"complete": True, "spans": [[0, len(self.body)]]},
                  "dimensions": {k: 3 for k in ("opening_pull", "causal_progress", "character_specificity",
                   "payoff", "economy", "coherence", "voice", "channel_fit")},
                  "dimension_evidence": {k: "有明确选择和结果" for k in ("opening_pull", "causal_progress",
                   "character_specificity", "payoff", "economy", "coherence", "voice", "channel_fit")},
                  "issues": []}
        self.assertEqual(review_decision(self.body, self.project.contract(), report)["decision"], "passed")
        with self.assertRaises(StoryError):
            review_decision(self.body + "改", self.project.contract(), report)

    def test_two_payoff_warnings_block_closed_ending(self):
        contract = self.project.contract()
        contract["ending_mode"] = "closed"
        report = {"content_hash": digest(self.body), "contract_hash": digest(contract),
                  "review_rubric_hash": review_rubric_hash(),
                  "review_coverage": {"complete": True, "spans": [[0, len(self.body)]]},
                  "dimensions": {key: 3 for key in (
                      "opening_pull", "causal_progress", "character_specificity", "payoff",
                      "economy", "coherence", "voice", "channel_fit")},
                  "dimension_evidence": {key: "有证据" for key in (
                      "opening_pull", "causal_progress", "character_specificity", "payoff",
                      "economy", "coherence", "voice", "channel_fit")},
                  "issues": []}
        for index in range(2):
            quote = self.body[index:index + 5]
            report["issues"].append({"quote": quote, "span": [index, index + 5],
                                     "severity": "warning", "category": "payoff"})
        decision = review_decision(self.body, contract, report)
        self.assertIn("payoff_warning_cluster", decision["blockers"])

    def test_multiple_consistency_warnings_cannot_hide_behind_high_scores(self):
        self.use_deep_review()
        self.flow.develop()
        report = self.flow.generate()
        body = self.project.active_draft()[1]
        contract = self.project.contract()
        report["dimensions"] = {key: 4 for key in report["dimensions"]}
        quote = body[:5]
        issue = {"quote":quote,"span":[0,5],"severity":"warning",
                 "category":"coherence","reason":"人物年份前后不一致"}
        report["issues"] = [issue, dict(issue)]
        self.assertEqual(review_decision(body, contract, report)["decision"], "passed")
        report["issues"].append({"quote":body[5:10],"span":[5,10],"severity":"warning",
                                 "category":"coherence","reason":"关键物件属性前后不一致"})
        self.assertIn("coherence_warning_cluster", review_decision(body, contract, report)["blockers"])

    def test_author_facts_block_literal_conflict_and_unproven_claim(self):
        raw = self.brief.to_dict()
        raw["story_facts"] = [{"id": "lost_letter", "statement": "信丢失后林禾追查",
            "required_text": ["信不见了"], "forbidden_text": ["信从未丢失"], "critical": True}]
        contract = StoryBrief.from_dict(raw).effective_contract()
        checks = [{"fact_id": "lost_letter", "verdict": "supported",
                   "quote": "林禾发现信不见了", "reason": "正文写明"}]
        self.assertEqual(fact_gate(self.body, contract, checks), [])
        self.assertIn("fact:lost_letter:forbidden:信从未丢失",
                      fact_gate(self.body + "信从未丢失", contract, checks))
        self.assertIn("fact:lost_letter:unclear", fact_gate(self.body, contract,
            [{"fact_id": "lost_letter", "verdict": "unclear", "quote": "", "reason": "证据不足"}]))
        with self.assertRaises(StoryError):
            fact_gate(self.body, contract, [{"fact_id": "lost_letter", "verdict": "supported",
                "quote": "不存在的引文", "reason": "虚构"}])

    def test_plan_requires_continuity_map(self):
        from short_fiction_agent.story_studio.workflow import validate_continuity_map
        with self.assertRaises(StoryError):
            validate_continuity_map({"continuity_map": {"timeline": ["前后"], "locations": [],
                "knowledge": ["知道"], "motives": ["想做"]}})

    def test_malformed_candidates_and_ungrounded_edit_retry_once(self):
        self.use_deep_review()
        class RecoveringLLM(ScriptedLLM):
            candidate_calls = 0
            edit_calls = 0

            def generate(self, prompt, task_type):
                if task_type == "story_candidates":
                    self.candidate_calls += 1
                    if self.candidate_calls == 1:
                        return '{"candidates":[{"id":1}]}'
                if task_type == "story_edit":
                    self.edit_calls += 1
                    if self.edit_calls == 1:
                        return json.dumps({"back_to_plan": False, "edits": [
                            {"quote": "不存在的原文", "replacement": "新文"}]}, ensure_ascii=False)
                return super().generate(prompt, task_type)

        llm = RecoveringLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        report = flow.generate(auto_revise=False)
        self.assertEqual(report["decision"], "passed")
        self.assertEqual((llm.candidate_calls, llm.edit_calls), (2, 2))

    def test_missing_edit_array_retries_once(self):
        self.use_deep_review()
        class MissingEditsLLM(ScriptedLLM):
            edit_calls = 0

            def generate(self, prompt, task_type):
                if task_type == "story_edit":
                    self.edit_calls += 1
                    return '{"back_to_plan":false}' if self.edit_calls == 1 else \
                           '{"back_to_plan":false,"edits":[]}'
                return super().generate(prompt, task_type)

        llm = MissingEditsLLM(self.body)
        self.assertEqual(StoryWorkflow(self.project, llm)._edit(self.body), self.body + "\n")
        self.assertEqual(llm.edit_calls, 2)

    def test_underlength_micro_story_gets_one_bounded_rewrite(self):
        class RepairingLLM(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == "repair_story_length":
                    return self.body
                return super().generate(prompt, task_type)

        llm = RepairingLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        short = "林禾发现信不见了。"
        repaired = flow._repair_length(short, self.project.contract())
        self.assertEqual(repaired.strip(), self.body)
        self.assertEqual(flow.calls, 1)
        self.assertEqual(flow._repair_length(repaired, self.project.contract()), repaired)
        self.assertEqual(flow.calls, 1)

    def test_micro_preflight_stops_before_costly_repair(self):
        self.use_deep_review()
        class PreflightLLM(ScriptedLLM):
            audits = 0

            def generate(self, prompt, task_type):
                if task_type == "blueprint_audit":
                    self.audits += 1
                    if self.audits == 1:
                        return json.dumps({"decision": "revise", "issues": [{"path": "scenes[0]",
                            "reason": "线索不能推出结局", "missing_link": "补充可见证据"}]}, ensure_ascii=False)
                    return '{"decision":"pass","issues":[]}'
                if task_type == "story_plan_repair":
                    return json.dumps(plan, ensure_ascii=False)
                return super().generate(prompt, task_type)

        plan = {"scenes": [dict(scene_id="s0", purpose="找信", goal="找信", pressure="时间",
            choice="搜查", consequence="找到", new_information="线索", emotional_change="放心",
            budget=900, dependencies=[])], "ending_resolution": "找回信",
            "promises": [{"promise": "信在哪里", "payoff_scene": "s0", "required": True}],
            "continuity_map": {"timeline": ["先丢后找"], "locations": ["县城"],
                "knowledge": ["林禾起初不知道"], "motives": ["林禾想找回信"]}}
        llm = PreflightLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        with self.assertRaisesRegex(StoryError, "停止高成本修复"):
            flow._preflight_plan(plan, self.project.contract())
        self.assertEqual(llm.audits, 1)
        self.assertNotIn("story_plan_repair", llm.tasks)
        self.assertTrue((self.project.root / "plan" / "rejected.json").exists())

    def test_invalid_scene_count_retries_before_prose(self):
        class PlanRetryLLM(ScriptedLLM):
            plan_calls = 0

            def generate(self, prompt, task_type):
                if task_type == "story_blueprint":
                    self.plan_calls += 1
                    if self.plan_calls == 1:
                        data = json.loads(super().generate(prompt, task_type))
                        data["beats"] *= 4
                        return json.dumps(data, ensure_ascii=False)
                return super().generate(prompt, task_type)

        llm = PlanRetryLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        plan = flow.plan()
        self.assertEqual(len(plan["scenes"]), 1)
        self.assertEqual(llm.plan_calls, 2)
        self.assertFalse((self.project.root / "drafts").exists())

    def test_auto_selected_candidate_falls_back_before_prose(self):
        class FallbackLLM(ScriptedLLM):
            audits = 0

            def generate(self, prompt, task_type):
                if task_type == "blueprint_audit":
                    self.audits += 1
                    if self.audits <= 2:
                        return json.dumps({"decision": "revise", "issues": [
                            {"path": "scenes[0]", "reason": "证据不足", "missing_link": "补证据"},
                            {"path": "ending_resolution", "reason": "结尾冲突", "missing_link": "换结尾"}]},
                            ensure_ascii=False)
                    return '{"decision":"pass","issues":[]}'
                return super().generate(prompt, task_type)

        llm = FallbackLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        report = flow.generate(auto_revise=False)
        self.assertEqual(report["decision"], "passed")
        self.assertEqual(llm.audits, 3)
        self.assertEqual(json.loads((self.project.root / "selection.json").read_text())["candidate_index"], 1)

    def test_compact_blueprint_repairs_once_before_prose(self):
        class RepairLLM(ScriptedLLM):
            audits = 0

            def generate(self, prompt, task_type):
                if task_type == "blueprint_audit":
                    self.audits += 1
                    if self.audits == 1:
                        return json.dumps({"decision": "revise", "issues": [{
                            "path": "beats[0].evidence", "reason": "证据不足", "missing_link": "补收据"}]},
                            ensure_ascii=False)
                    return '{"decision":"pass","issues":[]}'
                if task_type == "story_blueprint_repair":
                    partial = json.loads(super().generate(prompt, "story_blueprint"))
                    partial.pop("cost")
                    return json.dumps({"blueprint": partial}, ensure_ascii=False)
                return super().generate(prompt, task_type)

        llm = RepairLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        plan = flow.plan()
        self.assertIn("blueprint", plan)
        self.assertEqual(llm.audits, 2)
        self.assertTrue((self.project.root / "plan" / "blueprint-0-repair.json").exists())

    def test_structural_editor_rejection_tries_one_alternate_with_budget(self):
        self.use_deep_review()
        class StructuralLLM(ScriptedLLM):
            edit_calls = 0

            def generate(self, prompt, task_type):
                if task_type == "story_edit":
                    self.edit_calls += 1
                    if self.edit_calls == 1:
                        return '{"back_to_plan":true,"reason":"谜底机制不成立","edits":[]}'
                return super().generate(prompt, task_type)

        llm = StructuralLLM(self.body)
        flow = StoryWorkflow(self.project, llm, max_calls=30)
        flow.develop()
        report = flow.generate(auto_revise=False)
        self.assertEqual(report["decision"], "passed")
        self.assertTrue(flow.fallback_used)
        self.assertEqual(json.loads((self.project.root / "selection.json").read_text())
                         ["candidate_index"], 1)
        self.assertEqual(llm.edit_calls, 2)

    def test_three_blueprint_hard_flaws_skip_repair_call(self):
        class ThreeIssuesLLM(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == "blueprint_audit":
                    return json.dumps({"decision": "revise", "issues": [
                        {"path": f"beats[{i}]", "reason": "核心证据缺失", "missing_link": "补证据"}
                        for i in range(3)]}, ensure_ascii=False)
                return super().generate(prompt, task_type)

        llm = ThreeIssuesLLM(self.body)
        flow = StoryWorkflow(self.project, llm)
        flow.develop()
        with self.assertRaisesRegex(StoryError, "写前因果预审"):
            flow.plan()
        self.assertNotIn("story_blueprint_repair", llm.tasks)


    def test_real_trial_drafts_are_blocked_by_length_or_fact_anchor(self):
        fixture = Path(__file__).resolve().parents[1] / "examples" / "story_studio" / "关门之后"
        # These optional historical outputs are not tracked in Git. Keep the
        # deterministic length/fact tests runnable in a clean checkout and make
        # missing empirical coverage visible instead of raising StopIteration.
        files = [next((fixture / folder / "drafts").glob(pattern), None)
                 for folder, pattern in (("direct-baseline-flash", "*/1/body.md"),
                                         ("direct-baseline-pro", "*/1/body.md"),
                                         ("real-run", "2b0c9014-*/1/body.md"))]
        if any(path is None for path in files):
            self.skipTest("Historical real-run drafts are absent; empirical fixture check not run")
        flash, pro, long = [path.read_text(encoding="utf-8") for path in files]
        from short_fiction_agent.story_studio.quality import length_status
        target = {"length": {"min": 2600, "target": 3200, "max": 4000}}
        self.assertFalse(length_status(flash, target)["within_range"])
        self.assertFalse(length_status(pro, target)["within_range"])
        self.assertFalse(length_status(long, target)["within_range"])
        facts = {"story_facts": [{"id": "memory_duration", "statement": "八分钟",
            "critical": True, "required_text": ["八分钟"], "forbidden_text": ["十分钟"]}]}
        checks = [{"fact_id": "memory_duration", "verdict": "supported",
            "quote": "八分钟", "reason": "正文写到八分钟"}]
        self.assertIn("fact:memory_duration:forbidden:十分钟", fact_gate(pro, facts, checks))

    def test_short_story_scene_coverage(self):
        raw = self.brief.to_dict()
        raw["format"] = "short_story"
        raw["length"] = {"target": 7600, "min": 6000, "max": 9500}
        alternate = StoryProject(Path(self.temp.name) / "short")
        alternate.create(StoryBrief.from_dict(raw))
        llm = ScriptedLLM(self.body)
        flow = StoryWorkflow(alternate, llm)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report["decision"], "passed")
        self.assertEqual(llm.scene_number, 4)
        self.assertEqual(report["review_coverage"]["spans"], [[0, len(alternate.active_draft()[1])]])

    def test_long_short_story_repairs_scene_before_joining(self):
        class OversizedScenes(ScriptedLLM):
            def generate(self, prompt, task_type):
                if task_type == "write_story_scene":
                    self.scene_number += 1
                    return "原" * 3000
                if task_type == "repair_story_scene_length":
                    self.tasks.append(task_type)
                    return "改" * 1900
                return super().generate(prompt, task_type)
        raw = self.brief.to_dict()
        raw.update(format="short_story", review_mode="deep_review",
                   length={"target":7600,"min":6000,"max":9500})
        project = StoryProject(Path(self.temp.name) / "scene_bounds")
        project.create(StoryBrief.from_dict(raw))
        llm = OversizedScenes(self.body)
        flow = StoryWorkflow(project, llm)
        flow.develop()
        body = flow._draft_text(flow.plan())
        from short_fiction_agent.story_studio.contracts import visible_count
        # The final 3000-character scene fits the remaining whole-story budget.
        self.assertEqual(visible_count(body), 8700)
        self.assertEqual(llm.tasks.count("repair_story_scene_length"), 3)
        self.assertEqual(len(list((project.root / "scene_repairs").glob("*/before.md"))), 3)

    def test_scene_repair_failure_preserves_both_outputs(self):
        contract = self.brief.to_dict()
        contract.update(format="short_story", length={"target":6000,"min":5400,"max":7200})
        context = {"contract":contract,"scene_count":3,"scene_index":0,"target_visible_chars":2000}
        with patch.object(self.flow, "call_text", return_value="仍" * 3000) as call:
            with self.assertRaisesRegex(StoryError, "修复后仍越界"):
                self.flow._repair_scene_length("原" * 3000, context)
            self.assertEqual(call.call_count, 2)
        folder = next((self.project.root / "scene_repairs").iterdir())
        self.assertEqual((folder / "before.md").read_text(), "原" * 3000)
        self.assertEqual((folder / "after.md").read_text().strip(), "仍" * 3000)

    def test_narrow_length_allocation_is_feasible_and_preserves_totals(self):
        for count in range(2, 9):
            for minimum in range(5001, 5101):
                target, maximum = minimum + 1, minimum + 2
                allocations = [[scene_share(n, count, i) for i in range(count)]
                               for n in (minimum, target, maximum)]
                self.assertEqual([sum(a) for a in allocations], [minimum, target, maximum])
                for low, mid, high in zip(*allocations):
                    self.assertLessEqual(low, mid)
                    self.assertLessEqual(mid, high)
        self.assertIsNone(scene_share(None, 6, 0))
        contract = self.brief.to_dict()
        contract.update(format="short_story", length={"min":6001,"target":6003,"max":6005})
        # Old ceil(6001/6)=1001 > floor(6005/6)=1000 rejected every scene.
        with patch.object(self.flow, "call_text") as call:
            for i in range(6):
                body = "字" * scene_share(6003, 6, i)
                context = {"contract":contract, "scene_count":6, "scene_index":i,
                           "target_visible_chars":scene_share(6003, 6, i)}
                self.assertEqual(self.flow._repair_scene_length(body, context), body)
            call.assert_not_called()

    def test_scene_repair_handles_short_output_without_touching_valid_scene(self):
        contract = self.brief.to_dict()
        contract.update(format="short_story", length={"target":6000,"min":5400,"max":7200})
        context = {"contract":contract,"scene_count":3,"scene_index":0,"target_visible_chars":2000}
        with patch.object(self.flow, "call_text", return_value="补" * 2000) as call:
            self.assertEqual(self.flow._repair_scene_length("好" * 2000, context), "好" * 2000)
            call.assert_not_called()
            self.assertEqual(self.flow._repair_scene_length("短" * 200, context).strip(), "补" * 2000)
            self.assertEqual(call.call_count, 1)

    def test_long_story_length_failure_stops_before_model_review(self):
        raw = self.brief.to_dict()
        raw.update(format="short_story", review_mode="deep_review",
                   length={"target":7600,"min":6000,"max":9500})
        project = StoryProject(Path(self.temp.name) / "length_stop")
        project.create(StoryBrief.from_dict(raw))
        flow = StoryWorkflow(project, ScriptedLLM(self.body))
        flow.develop()
        with patch.object(flow, "_draft_text", return_value="长" * 10000), patch.object(flow, "_repair_length", side_effect=lambda body, contract: body), patch.object(flow.reviewer, "review") as review:
            with self.assertRaisesRegex(StoryError, "停止全文模型审查"):
                flow.generate()
            review.assert_not_called()
        record, body = project.active_draft()
        self.assertEqual(record["state"], "needs_author")
        self.assertEqual(len(body.strip()), 10000)

    def test_review_chunks_do_not_create_orphan_periods(self):
        body = "甲" * 4000 + "。\n" + "乙" * 498 + "。\n" + "丙" * 1000 + "。\n"
        self.assertEqual(body[4500], "。")  # old fixed-width split created an orphan
        spans = review_spans(body)
        self.assertEqual("".join(body[a:b] for a,b in spans), body)
        for a,b in spans:
            self.assertFalse(body[a:b].startswith("。"))
            self.assertTrue(b == len(body) or body[b-1] == "\n")

    def test_review_chunks_keep_single_long_paragraph(self):
        body = "完整" * 3000 + "。\n下一段。\n"
        spans = review_spans(body)
        self.assertEqual(spans[0], [0, 6002])
        self.assertEqual(spans[-1][1], len(body))
        with self.assertRaises(ValueError):
            review_spans(body, 0)

    def test_short_serial_has_episode_exports(self):
        raw = self.brief.to_dict()
        raw["format"] = "short_serial"
        raw["length"] = {"target": 18000, "min": 14000, "max": 23000}
        alternate = StoryProject(Path(self.temp.name) / "serial")
        alternate.create(StoryBrief.from_dict(raw))
        llm = ScriptedLLM(self.body)
        flow = StoryWorkflow(alternate, llm)
        flow.develop()
        report = flow.generate()
        self.assertEqual(report["decision"], "passed")
        self.assertEqual(llm.scene_number, 9)
        package = StoryExporter(alternate, flow.call_json).export("fanqie_short")
        self.assertEqual(len(list((package / "episodes").glob("*.txt"))), 3)

    def test_legacy_nested_blocker_survives_good_average(self):
        gate = SubmissionGateAgent.__new__(SubmissionGateAgent)
        result = gate._gate_result("test", 90, 72, "hard", [
            {"title": "aggregate", "score": 88, "passed": True, "modules": [
                {"module": "critical", "score": 35, "passed": False}]}], [], [], {})
        self.assertFalse(result["passed"])
        self.assertIn("aggregate/critical", result["blockers"])

    def test_reducing_length_is_not_a_regression(self):
        before = {"results": [{"fixture_id": "case", "metrics": {"zh_chars": 3000,
            "dialogue_count": 10, "review_score": 80, "wrote_ok": 1}}]}
        after = {"results": [{"fixture_id": "case", "metrics": {"zh_chars": 1800,
            "dialogue_count": 5, "review_score": 80, "wrote_ok": 1}}]}
        result = compare_snapshots(before, after)
        self.assertEqual(result["diffs"][0]["delta"]["zh_chars"], -1200)
        self.assertEqual(result["regressions"], [])

    def test_legacy_import_preserves_source_and_review_is_not_inherited(self):
        source = Path(self.temp.name) / "old"
        (source / "config").mkdir(parents=True)
        (source / "chapters").mkdir()
        (source / "config" / "novel.yaml").write_text("title: 旧小说\ngenre: 青春校园恋爱\n", encoding="utf-8")
        (source / "chapters" / "chapter_001.md").write_text("旧故事。", encoding="utf-8")
        original = {p: p.read_bytes() for p in source.rglob("*") if p.is_file()}
        dest = StoryProject(Path(self.temp.name) / "imported")
        self.assertEqual(import_legacy(source, dest), 0)
        self.assertEqual({p: p.read_bytes() for p in original}, original)
        self.assertEqual(dest.active_draft()[0]["state"], "legacy_generated")
        self.assertEqual(json.loads((dest.root / "migration_report.json").read_text())["chapter_count"], 1)
        with self.assertRaises(StoryError):
            StoryExporter(dest).export("fanqie_long")


if __name__ == "__main__":
    unittest.main()
