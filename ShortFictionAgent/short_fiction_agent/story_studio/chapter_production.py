"""Default short-story writing: one real chapter, then one reader review.

The completed-chapter checkpoint owns its constituent scene outputs. A repaired
chapter, rather than stale raw scenes, is the source of the next chapter.
"""
from __future__ import annotations

import json
import re

from . import chapter_reader, chapter_structure, lead, scene_planning, target_reader, whole_story
from .contracts import StoryError, clean_body, digest, visible_count
from .engagement import WRITE_INSTRUCTION
from .research_guidance import guidance_fingerprint
from .storage import read_json, write_json, write_text

PROTOCOL = 'write-and-read-chapters-v3-original-reader-promise'


class ChapterReplanRequired(StoryError):
    """An actual reader finding requires a structural plan change."""


def _writing_instructions(contract):
    """Bind the exact instructions used for both drafting and local rewriting."""
    return (target_reader.WRITE_INSTRUCTION + WRITE_INSTRUCTION + chapter_reader.WRITE_INSTRUCTION
            + (whole_story.WRITE_INSTRUCTION if whole_story.enabled(contract) else ''))


def _heading(unit, index):
    title = re.sub(r'^第[一二三四五六七八九十百零〇两\d]+章[：:、.．\s]*', '', unit['title']).strip()
    label = '第一章' if index == 0 else f'第{index + 1}章'
    return label + (' ' + title if title else '')


def _prose(text):
    lines = clean_body(text).splitlines()
    # The program owns boundaries; tolerate a redundant first heading only.
    if lines and re.match(r'^第[一二三四五六七八九十百零〇两\d]+章', lines[0]):
        lines = lines[1:]
    if any(re.match(r'^第[一二三四五六七八九十百零〇两\d]+章', line) for line in lines):
        raise StoryError('场景正文自行新增章节，不能覆盖已规划的章级审读边界')
    return clean_body('\n'.join(lines))


def _bound_text(path, binding, generate):
    """Persist completed source before any later review or parse can fail."""
    state = read_json(path.with_suffix('.json'))
    if state is None:
        text = generate()
        write_text(path, text)
        text = path.read_text(encoding='utf-8')
        write_json(path.with_suffix('.json'), {'binding': binding, 'content_hash': digest(text)})
    else:
        text = path.read_text(encoding='utf-8')
        if state.get('binding') != binding or state.get('content_hash') != digest(text):
            raise StoryError('章级正文检查点来源或内容损坏，停止复用')
    return text


def _review(flow, body, prior, reviews, unit, is_final, directory):
    plan = chapter_reader.make_plan(body, flow.project.contract(), previous_text=prior,
        previous_reviews=reviews, chapter_id=unit['chapter_id'], is_final=is_final)
    path = directory / 'reviews' / (plan['plan_hash'] + '.json')
    saved = read_json(path)
    if saved is None:
        raw = flow.call_text('chief_editor_chapter_reader', plan['prompt'])
        saved = {'plan_hash': plan['plan_hash'], 'prompt_hash': plan['prompt_hash'],
                 'raw_text': raw, 'raw_hash': digest(raw)}
        write_json(path, saved)
    if (saved.get('plan_hash') != plan['plan_hash'] or saved.get('prompt_hash') != plan['prompt_hash']
            or saved.get('raw_hash') != digest(saved.get('raw_text'))):
        raise StoryError('章级审读回答缓存与当前来源不一致')
    from .quality import parse_json_response
    original_response = None
    try:
        response = parse_json_response(saved['raw_text'])
        original_response = response
        review = chapter_reader.validate_response(response, plan)
    except StoryError as exc:
        # Repair formatting of the completed answer only; no semantic reroll.
        fixed = read_json(path.with_name(path.stem + '-format.json'))
        if fixed is None:
            response = flow.call_json('json_format_repair',
                '仅修正下列已完成审读的JSON格式、字段层级或证据ID，保留原语义结论与具体问题。'
                '不得重新评价作品、把负面判断变通过，或发明新剧情。'
                '如果原回答缺少判断证据，按原负面含义补到对应已提供原文；只返回JSON。\n'
                + json.dumps({'error': str(exc), 'original_response': saved['raw_text'],
                              'original_review_prompt': plan['prompt']}, ensure_ascii=False))
            fixed = {'source_raw_hash': saved['raw_hash'], 'response': response,
                     'response_hash': digest(response)}
            write_json(path.with_name(path.stem + '-format.json'), fixed)
        if (fixed.get('source_raw_hash') != saved['raw_hash']
                or fixed.get('response_hash') != digest(fixed.get('response'))):
            raise StoryError('章级审读格式恢复记录损坏')
        if original_response is not None:
            old_verdict = original_response.get('verdict')
            if old_verdict in {'continue', 'revise', 'replan'} and fixed['response'].get('verdict') != old_verdict:
                raise StoryError('章级审读格式恢复改变原语义结论，不能继承通过')
            old_rows = [*original_response.get('windows', []), original_response.get('cumulative', {}), original_response.get('reading_value_check', {})]
            new_rows = [*fixed['response'].get('windows', []), fixed['response'].get('cumulative', {}), fixed['response'].get('reading_value_check', {})]
            old_problems = [(row.get('status'), row.get('repair_scope'), row.get('reason'),
                             row.get('editorial_priority', 'reading_core'), row.get('reading_effect')) for row in old_rows
                            if isinstance(row, dict) and row.get('status') in {'thin', 'uncertain'}]
            new_problems = [(row.get('status'), row.get('repair_scope'), row.get('reason'),
                             row.get('editorial_priority', 'reading_core'), row.get('reading_effect')) for row in new_rows
                            if isinstance(row, dict) and row.get('status') in {'thin', 'uncertain'}]
            if old_problems != new_problems:
                raise StoryError('章级审读格式恢复丢失或改写原负面问题，不能继承通过')
        else:
            raise StoryError('章级审读原回答无法恢复语义结论，已保留回答等待恢复')
        review = chapter_reader.validate_response(fixed['response'], plan)
    write_json(path.with_name(path.stem + '-validated.json'), review)
    flow.project.record_run({'event': 'chapter_reader_checked', 'chapter_id': unit['chapter_id'],
        'content_hash': digest(body), 'verdict': review['verdict'],
        'legacy_inferred': unit['legacy_inferred'], 'evidence': str(path.relative_to(flow.project.root))})
    return review


def _accept(flow, body, prior, reviews, unit, is_final, directory, binding, context, plan):
    """One local rewrite, or a persisted structural request before more prose."""
    for attempt in range(2):
        review = _review(flow, body, prior, reviews, unit, is_final, directory)
        if review['verdict'] == 'continue':
            return body, review
        if review['verdict'] == 'replan' or attempt == 1:
            flow._save_budget_replan_request(plan, context['contract'],
                plan['scenes'][unit['scene_indices'][-1]], '\n\n'.join(value for value in (prior, body) if value),
                '章级阅读未通过：须重建场景/回报或解决跨章重复，不能继续堆正文。', reader_review=review)
            raise ChapterReplanRequired('章级阅读需要重规划：' + unit['chapter_id'])
        repair = chapter_reader.revision_instructions(review)
        repair_binding = {'chapter_binding': digest(binding), 'source_hash': digest(body),
                          'review_hash': digest(review), 'attempt': attempt + 1}
        source = body
        body = _bound_text(directory / 'revision-1.md', repair_binding,
            lambda: flow.call_text('rewrite_story_chapter',
                '只重写当前完整阅读单元，保留章标题，不附解释。按主审实际证据修复表达或交锋。'
                '保留作者设定及前文事实，不私自改变未来查询规则、人物关系或加入万能事故。'
                '如果场景本身不值得保留，应退回规划，不能用文句润色或章末空预告掩盖。'
                '保持合理展开，不为字数填充，不压缩导语；本次正文不含导语。\n'
                + _writing_instructions(context['contract'])
                + json.dumps({'contract': context['contract'], 'previous_text': prior, 'chapter': source,
                              'reader_repair': repair, 'chapter_unit': unit,
                              'target_reader_context': context['target_reader_context'],
                              'writing_context': context}, ensure_ascii=False)))
        body = source.splitlines()[0] + '\n\n' + _prose(body)
        flow.project.record_run({'event': 'chapter_rewritten', 'chapter_id': unit['chapter_id'],
            'before_hash': digest(source), 'after_hash': digest(body), 'review_hash': digest(review)})
    raise AssertionError('unreachable')


def draft(flow, plan, context, *, max_chapters=None):
    from .workflow import scene_budget
    contract = context['contract']
    # This entry promise comes from the author contract, never from the model's
    # later main_question or a revised outline's self-description.
    context = {**context, 'target_reader_context': target_reader.entry_context(
        contract, include_author_promise=True)}
    writing_instructions = _writing_instructions(contract)
    units = chapter_structure.chapters(plan)
    if max_chapters is not None and (type(max_chapters) is not int or max_chapters < 1):
        raise StoryError('前缀验证章节数必须为正整数')
    limit = len(units) if max_chapters is None else min(max_chapters, len(units))
    draft_length = lead.story_length(contract)
    scenes, completed, reviews = plan['scenes'], [], []
    flow.project.record_run({'event': 'draft_strategy', 'strategy': 'chapters_with_reader',
        'protocol': PROTOCOL, 'chapter_map': units, 'prefix_only': max_chapters is not None,
        'length_contract': contract['length']})
    for index, unit in enumerate(units[:limit]):
        prior = '\n\n'.join(completed)
        handoffs = [{'chapter_id': row['chapter_id'], 'actual_handoff': chapter_reader.actual_handoff(row)}
                    for row in reviews]
        binding = {'protocol': PROTOCOL, 'plan_hash': digest(plan), 'contract_hash': digest(contract),
            'chapter_map_hash': digest(units), 'chapter': unit, 'previous_text_hash': digest(prior),
            'prior_reviews_hash': digest(reviews), 'context_hash': digest(context),
            'writer_instructions_hash': digest(writing_instructions),
            'reader_criteria_hash': chapter_reader.criteria_hash(), 'research_hash': guidance_fingerprint()}
        directory = flow.project.root / 'chapter_checkpoints' / digest(binding)
        def generate_chapter():
            chunks = []
            for position, i in enumerate(unit['scene_indices']):
                current = '\n\n'.join(chunks)
                actual_prior = '\n\n'.join(value for value in (prior, current) if value)
                scene = scenes[i]
                try:
                    budget = scene_budget(draft_length, len(scenes), i,
                        visible_count(actual_prior), scenes=scenes)
                except StoryError as exc:
                    flow._save_budget_replan_request(plan, contract, scene, actual_prior, exc)
                    raise
                scene_input = {**context, 'scene': scene, 'scene_index': i, 'scene_count': len(scenes),
                    'chapter_unit': unit, 'target_visible_chars': budget['target'], 'length_budget': budget,
                    'episode_end': i == len(scenes) - 1, 'previous_text': actual_prior,
                    'previous_tail': actual_prior[-1600:], 'chapter_reader_handoff': handoffs,
                    'prior_actual_progression': handoffs,
                    'whole_story_orientation': scene_planning.continuation_context(plan, i)}
                prompt = ('只写当前场景的连续小说正文，不附导语、标题、分析或下章预告。'
                    '当前场属于chapter_unit，模型输出分块不等于另起章节。'
                    '先兑现读者正等的那次戏：人物主动争取，对方以自己的欲望实际接招，结果带来得失、心动或偏爱。'
                    '办成事项、解释道理不自动成为核心体验，不能把原始卖点改成成熟相处的结论。'
                    '以previous_text实际事实承接，不重复已兑现的能力证明、偏爱或关系道理。'
                    '生活与职业细节服务当前交锋，删无变化的手续、心理复述和重复核验；保留有作用的安静互动。'
                    '首场从人物当下具体处境独立入场，不能假定读者已读导语或开篇候选。'
                    '首章本身必须值得看，不用后文会精彩为铺垫辩护；不强制每几百字另造冲突。'
                    '字数预算是展开安排，不能为凑满篇幅重讲同一件事；场景结束自然承接后果。\n'
                    + writing_instructions
                    + json.dumps(scene_input, ensure_ascii=False, sort_keys=True))
                scene_binding = {'chapter_binding': digest(binding), 'scene_id': scene['scene_id'],
                                 'prompt_hash': digest(prompt)}
                text = _bound_text(directory / 'scenes' / (str(i) + '.md'), scene_binding,
                    lambda: flow._write_scene_transport_parts(prompt, scene_input,
                        directory / 'scenes' / str(i)))
                chunks.append(_prose(text))
                if index == 0 and position == 0 and len(unit['scene_indices']) > 1:
                    # Even a plan putting many scenes in chapter one cannot
                    # postpone its first reader decision until the whole book.
                    entry = {**unit, 'chapter_id': unit['chapter_id'] + '-opening-trial',
                             'scene_ids': [scene['scene_id']], 'scene_indices': [i]}
                    entry_body, entry_review = _accept(flow, _heading(unit, index) + '\n\n' + chunks[0],
                        prior, reviews, entry, False, directory / 'opening_trial',
                        binding, context, plan)
                    chunks[0] = _prose(entry_body)
                    flow.project.record_run({'event': 'opening_trial_completed',
                        'chapter_id': unit['chapter_id'], 'content_hash': digest(entry_body),
                        'review_hash': digest(entry_review)})
            return _heading(unit, index) + '\n\n' + '\n\n'.join(chunks)
        body = _bound_text(directory / 'original.md', binding, generate_chapter)
        body, review = _accept(flow, body, prior, reviews, unit, index == len(units) - 1,
                              directory, binding, context, plan)
        completed.append(body.strip())
        reviews.append(review)
        write_text(directory / 'accepted.md', body)
        write_json(directory / 'accepted.json', {'binding': binding, 'content_hash': digest(body),
            'review_hash': digest(review), 'status': 'chapter_reader_continue_not_final_acceptance'})
    body = clean_body('\n\n'.join(completed))
    return {'body': body, 'complete': max_chapters is None,
        'completed_chapters': len(completed), 'planned_chapters': len(units), 'chapter_reviews': reviews,
        'scope': 'complete_draft_pending_final_review' if max_chapters is None else 'prefix_only_not_submission'}
