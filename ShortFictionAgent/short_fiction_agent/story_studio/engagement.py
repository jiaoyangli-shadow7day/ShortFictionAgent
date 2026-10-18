"""Continuous reader interest: editorial evidence, never a retention predictor."""
from __future__ import annotations

import json
from pathlib import Path
import re

from .contracts import StoryError, digest, visible_count

PROTOCOL = "continuous-pull-v1"
SCENE_REVIEW_PROTOCOL = "actual-scene-progression-v3-emotional-realization"
GUIDANCE = {
    "protocol": PROTOCOL,
    "source": "docs/architecture.md",
    "source_scope": "用户提供截断正文的前中段分析；结尾未读，无真实留存数据",
    "rules": [
        "读者期待由人物行动、信息、关系或处境的实际变化推动；回答一个问题后，由结果引出下一项选择或期待。",
        "压力之间给有分量的回报：能力得到验证、关系获得回应、主动选择或小胜；不要靠连续受辱拖长故事。",
        "同一能力、关系或物件在新处境中产生不同后果；避免换个人再重复同一种争吵、照顾或打脸。",
        "对白承担试探、抗拒、承诺、决策或回应；生活细节若改变理解、选择、代价或亲近感，应保留。",
        "预告句必须有具体后果；不能仅靠‘可我错了’、隐瞒常识、假倒计时和新人物救场延续悬念。",
        "安静的情感回应与日常偏爱也能推进；结尾可以收束，不必再留悬念。观察字数不是反转间隔或平台阈值。",
        "热度不保证逐段优秀：连续正文中同类受辱、讨好、自我辩解或换配角争吵也可能拖延。记录可压缩处，不能把来源中的全部做法变成模板。",
        "悬念不能豁免因果与规则核查：改变已经说出的选择要交代触发原因；幻想能力的盲区与限制保持一致；猜测、口供和表象不能直接当成客观事实。",
        "重复观众评论、重复剪辑回放、不断换藏身点与恐惧比喻不自动形成推进；保留改变选择、理解或代价的新信息，合并其余复述。",
        "关系回报要产生可观察的下一责任或主动验证：相处约定落实、双方互相照顾、开始协作或主动问清；不能用永久含混和单方猜心无限延后回应。",
        "有事情发生只证明推进，不证明情绪强度。核心戏应让读者经历期待、人物互动或阻力、主动应对和具体兑现；甜、痛、危险与胜利依作者承诺变化，不能只列办成的事项。",
    ],
    "transfer_limit": "仅迁移抽象机制，不复制来源角色、情节顺序和原文；不将模型意愿判断称为读者留存。",
    "continuous_reading_evidence": json.loads(
        (Path(__file__).with_name("data") / "continuous_pull_research.json").read_text(encoding="utf-8")),
}
BEAT_FIELDS = ("reader_question", "action", "change", "reward", "next_expectation")
PLAN_INSTRUCTION = (
    "短篇每场另含progression_beats数组，按本场真实交锋需要安排节拍，不能均匀凑数。"
    "每项有reader_question（此刻读者为什么想知道具体答案）、action（人物做什么）、"
    "response（对方实际怎样接招或环境怎样反馈，不能只有脸红、认可、事情办成）、"
    "change（行动改变的信息/处境/关系）、reward（本节回应或情绪回报，暂未回应须说明原因）、"
    "next_expectation（结果引出的具体期待；结尾可写已收束）。各项为非空字符串，写清动作与回应所需信息，"
    "不为短字段建议省掉人物意图、回应和因果，也不写正文段落。"
    "大场面拆出多次实际变化；节拍之间有因果承接。压力、发现、能力证明与关系回报按题材交替，"
    "不能用反复受辱或空泛预告充数，不规定每几百字反转。"
    "同一阶段若需要多次不同交锋，就规划多节拍或自然拆场，不能把一年的关系变化塞进一句相互支持。"
    "先从读者想看的交锋与回报构思故事，再填字段；不把考试、比赛、毕业等时间节点本身当情节。\n"
)
WRITE_INSTRUCTION = (
    "执行progression_beats的行动、变化与回应，并自然承接读者尚在等待的问题。"
    "前文实际已经兑现的事不得重新吊起；让回报产生新的后果，避免只在场尾制造变化。"
    "prior_actual_progression是基于已写正文的审读交接；previous_text为原文事实。"
    "当计划与前文不一致，以前文为准并合理承接，不把交接中的待答问题冒充已发生事实。"
    "有作用的照顾、暧昧与生活细节应写足；无变化的解释、争吵和心理复述压缩。"
    "写成连贯小说，节拍字段和审稿术语不得进入正文。\n"
)
REVIEW_INSTRUCTION = (
    "另输出progression_observations，逐项覆盖progression_windows的id。按窗口顺序判断，"
    "每项有window_id、verdict（flowing/stalled/unclear）、reader_question、change、reward、"
    "next_expectation、reason、fix_intent、evidence_paragraph_indices（当前窗口内1至4个整数编号，不重复）。"
    "编号对应本片段paragraphs数组，从0开始；必须满足paragraph_start <= 编号 < paragraph_end。"
    "只选最能证明判断的段落，不要列出全窗口；paragraph_end是边界，不是可选编号。"
    "每个窗口已列出evidence_paragraphs及paragraph_index，请直接从该窗口列表选择编号，不自己数段落。"
    "所有文字字段非空；已收束可明确写无后续期待，未兑现可说明仍在等待什么。"
    "只使用当前窗口结束之前已出现的信息，不用后面的高潮为当前停滞补证据。"
    "此前正文在previous_text。检查主问题是否获得回应或产生新后果，区别有作用的安静互动与重复。"
    "窗口是约700可见字符的编辑观察尺度，不要求每窗爆点；段落完整保留。"
    "stalled只用于可指出具体重复或无效拖延的位置，不能仅因没有新冲突而判停滞。\n"
    "flowing只表示此窗口在推进，不等于核心场面充分展开或强烈吸引；不要用有动作、有钩子抵消高潮概述。\n"
)
CONFIRM_INSTRUCTION = (
    "另输出progression_confirmations，完整覆盖progression_suspects的window_id。"
    "每项有window_id、verdict（stalled/context_resolves/unclear）、reason、fix_intent、paragraph_index。"
    "用完整正文复核疑似停滞：解释有作用的铺垫、照顾或关系回应可以context_resolves；"
    "后续高潮不能抵消此前确有证据的拖延。引用必须在该窗口结束之前；"
    "stalled须引用窗口内原文，给出局部压缩或改写方向；不得编造读者跳出率。"
    "无疑似窗口返回空数组。\n"
)
SCENE_REVIEW_INSTRUCTION = (
    "你是独立的场内推进编辑。只审当前已经写出的正文，不读取未来大纲或预定结局。"
    "不要默认生成者执行了节拍。按实际行动、信息、关系和处境的变化判断。"
    "reader_contract是作者的题材与读者承诺，不含未来大纲；同时判断变化是否服务这项承诺。"
    "技术排障、购物或办手续即使步骤不断变化，也可能让次要任务占据主线。"
    "甜宠应让读者期待双方理解、试探、选择和关系回应；不能因末尾一次肢体接触忽略此前大量偏题。"
    "不要求每段恋爱或固定比例，保留确实改变关系、代价与理解的职业和生活细节。"
    "另输出promise_alignment对象：verdict为aligned/off_promise/unclear，reason和fix_intent为具体非空文字，"
    "paragraph_index准确指向最能说明判断的当前正文段落（整数，从0开始）。"
    "安静的照顾、选择与回应可以有效推进；热闹争吵、重复解释或同一问题反复预告也可能停滞。"
    "每项reason具体说明此前期待在本窗得到回应、改变或仍需等待的原因。"
    "另输出actual_handoff对象，含facts、answered_questions、pending_questions三个数组。"
    "facts为当前场实际发生的关键事实，1至6项；answered_questions与pending_questions各0至4项。"
    "每项有text（具体事实或问题）及paragraph_index（当前正文paragraphs数组的整数编号）。"
    "只记录已有证据支持的事实与期待，不把未执行的计划或猜测写成事实，不必为结尾再造悬念。"
    "待答问题须承接正文中已有的主线目标、关系试探或实际后果，允许空数组；"
    "不要为了凑推进建议补手续、准备清单或行程流程。已有安排若没有新的代价或关系作用，不算新期待。\n"
    + REVIEW_INSTRUCTION
)


def validate_scene_review(response: dict, expected: list[dict], body: str) -> dict:
    """Ground the actual scene handoff as well as every editorial window."""
    observations = validate_observations(response, expected, body)
    handoff = response.get("actual_handoff")
    if not isinstance(handoff, dict):
        raise StoryError("场内审读缺少实际正文交接记录")
    spans = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", body) if m.group().strip()]
    alignment = response.get('promise_alignment')
    if not isinstance(alignment, dict) or alignment.get('verdict') not in ('aligned', 'off_promise', 'unclear'):
        raise StoryError('场内审读缺少读者承诺对齐判断')
    _text_fields(alignment, ('reason', 'fix_intent'))
    index = alignment.get('paragraph_index')
    if type(index) is not int or not 0 <= index < len(spans):
        raise StoryError('读者承诺对齐证据编号无效')
    a, b = spans[index]
    alignment = {**alignment, 'quote': body[a:b], 'span': [a,b]}
    normalized = {}
    for key, minimum, maximum in (("facts", 1, 6), ("answered_questions", 0, 4),
                                  ("pending_questions", 0, 4)):
        entries = handoff.get(key)
        if not isinstance(entries, list) or not minimum <= len(entries) <= maximum:
            raise StoryError("场内实际交接的事实或问题数量无效")
        normalized[key] = []
        seen = set()
        for row in entries:
            if not isinstance(row, dict):
                raise StoryError("场内实际交接条目无效")
            _text_fields(row, ("text",))
            index = row.get("paragraph_index")
            if type(index) is not int or not 0 <= index < len(spans) or row["text"] in seen:
                raise StoryError("场内实际交接证据编号无效或条目重复")
            a, b = spans[index]
            normalized[key].append({"text": row["text"], "paragraph_index": index,
                                    "quote": body[a:b], "span": [a,b]})
            seen.add(row["text"])
    return {"observations": observations, "actual_handoff": normalized, 'promise_alignment': alignment}


def enabled(contract: dict) -> bool:
    return contract.get("format") == "short_story"


def validate_scene_beats(plan: dict) -> None:
    for scene in plan["scenes"]:
        beats = scene.get("progression_beats")
        if not isinstance(beats, list) or not beats:
            raise StoryError("短篇场景缺少非空progression_beats持续推进节拍")
        for beat in beats:
            if not isinstance(beat, dict) or any(
                not isinstance(beat.get(key), str) or not beat[key].strip() for key in BEAT_FIELDS
            ):
                raise StoryError("持续推进节拍缺少读者期待、行动、变化、回报或下一期待")
        if len({digest(beat) for beat in beats}) != len(beats):
            raise StoryError("同场持续推进节拍完全重复")


def windows(segment: str, *, offset: int = 0, target: int = 700) -> list[dict]:
    """Group complete prose lines. No punctuation heuristic claims semantic progress."""
    if target <= 0:
        raise ValueError("target must be positive")
    spans = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", segment) if m.group().strip()]
    result, first, count = [], 0, 0
    for index, (a, b) in enumerate(spans):
        count += visible_count(segment[a:b])
        if count >= target or index == len(spans) - 1:
            # Include separators so the saved observations cover the whole segment.
            start = 0 if not result else result[-1]["span"][1] - offset
            end = spans[index + 1][0] if index + 1 < len(spans) else len(segment)
            result.append({"window_id": f"w{offset + start}", "span": [offset + start, offset + end],
                           "paragraph_start": first, "paragraph_end": index + 1})
            first, count = index + 1, 0
    return result


def _text_fields(row: dict, fields: tuple[str, ...]) -> None:
    if any(not isinstance(row.get(k), str) or not row[k].strip() for k in fields):
        raise StoryError("持续吸引力审读缺少具体判断或修订方向")


def validate_observations(response: dict, expected: list[dict], segment: str) -> list[dict]:
    rows = response.get("progression_observations")
    ids = [w["window_id"] for w in expected]
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("window_id"), str) for r in rows)
            or sorted(r["window_id"] for r in rows) != sorted(ids)):
        raise StoryError("持续吸引力审读必须完整覆盖窗口，ID不得缺失或重复")
    spans = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", segment) if m.group().strip()]
    by_id = {row["window_id"]: row for row in rows}
    result = []
    for window in expected:
        row = by_id[window["window_id"]]
        if row.get("verdict") not in {"flowing", "stalled", "unclear"}:
            raise StoryError("持续吸引力窗口verdict无效")
        _text_fields(row, (*BEAT_FIELDS[:1], "change", "reward", "next_expectation", "reason", "fix_intent"))
        indices = row.get("evidence_paragraph_indices")
        if (not isinstance(indices, list) or not indices or len(indices) > 4
                or len(set(str(i) for i in indices)) != len(indices)
                or any(type(i) is not int or not window["paragraph_start"] <= i < window["paragraph_end"]
                       for i in indices)):
            raise StoryError(
                f"持续吸引力证据必须来自当前窗口，不能引用后文：{window['window_id']} "
                f"允许编号 {window['paragraph_start']} 至 {window['paragraph_end'] - 1}，"
                f"收到 {indices!r}；请从该窗口的 evidence_paragraphs 选择1至4个编号")
        # Compute the segment offset from its first window instead of paragraph spacing.
        offset = expected[0]["span"][0]
        evidence = [{"quote": segment[spans[i][0]:spans[i][1]],
                     "span": [offset + spans[i][0], offset + spans[i][1]]} for i in indices]
        result.append({**window, **{k: row[k] for k in
            ("verdict", "reader_question", "change", "reward", "next_expectation", "reason", "fix_intent")},
            "evidence": evidence})
    return result


def suspects(observations: list[dict]) -> list[dict]:
    return [row for row in observations if row["verdict"] != "flowing"]


def validate_confirmations(response: dict, observations: list[dict], body: str) -> list[dict]:
    expected = suspects(observations)
    rows = response.get("progression_confirmations")
    if (not isinstance(rows, list) or any(not isinstance(r, dict) or not isinstance(r.get("window_id"), str) for r in rows)
            or sorted(r["window_id"] for r in rows) != sorted(r["window_id"] for r in expected)):
        raise StoryError("全文复核必须完整覆盖疑似停滞窗口")
    spans = [(m.start(), m.end()) for m in re.finditer(r"[^\n]+", body) if m.group().strip()]
    by_id = {r["window_id"]: r for r in rows}
    result = []
    for window in expected:
        row = by_id[window["window_id"]]
        if row.get("verdict") not in {"stalled", "context_resolves", "unclear"}:
            raise StoryError("持续吸引力全文复核verdict无效")
        _text_fields(row, ("reason", "fix_intent"))
        index = row.get("paragraph_index")
        if type(index) is not int or not 0 <= index < len(spans):
            raise StoryError("持续吸引力全文复核段落编号无效")
        a, b = spans[index]
        if b > window["span"][1] or (row["verdict"] == "stalled" and a < window["span"][0]):
            raise StoryError("持续吸引力全文复核不能用后文高潮抵消停滞")
        result.append({"window_id": row["window_id"], "verdict": row["verdict"],
                       "reason": row["reason"], "fix_intent": row["fix_intent"],
                       "quote": body[a:b], "span": [a, b]})
    return result


def gate(body: str, report: dict) -> list[str]:
    """Recheck persisted coverage and evidence; scores cannot erase confirmed stalls."""
    record = report.get("continuous_pull")
    if not isinstance(record, dict) or record.get("protocol") != PROTOCOL:
        raise StoryError("短篇缺少当前持续吸引力审读，请重新审读")
    observations = record.get("observations")
    confirmations = record.get("confirmations")
    if not isinstance(observations, list) or not observations or not isinstance(confirmations, list):
        raise StoryError("持续吸引力报告结构不完整")
    cursor, ids = 0, set()
    for row in observations:
        if not isinstance(row, dict):
            raise StoryError("持续吸引力报告窗口无效")
        span = row.get("span")
        if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
                or span[0] != cursor or not span[0] < span[1] <= len(body)
                or row.get("window_id") != f"w{cursor}" or row["window_id"] in ids
                or row.get("verdict") not in {"flowing", "stalled", "unclear"}):
            raise StoryError("持续吸引力报告覆盖存在缺口或重复")
        _text_fields(row, ("reader_question", "change", "reward", "next_expectation", "reason", "fix_intent"))
        evidence = row.get("evidence")
        if not isinstance(evidence, list) or not evidence:
            raise StoryError("持续吸引力窗口缺少正文证据")
        for item in evidence:
            _validate_saved_evidence(item, body, lower=span[0], upper=span[1])
        cursor = span[1]; ids.add(row["window_id"])
    if cursor != len(body):
        raise StoryError("持续吸引力报告未覆盖正文结尾")
    wanted = {r["window_id"]: r for r in suspects(observations)}
    if (any(not isinstance(r, dict) or not isinstance(r.get("window_id"), str) for r in confirmations)
            or sorted(r["window_id"] for r in confirmations) != sorted(wanted)):
        raise StoryError("持续吸引力报告缺少疑似停滞全文复核")
    blockers = []
    for row in confirmations:
        window = wanted[row["window_id"]]
        if row.get("verdict") not in {"stalled", "context_resolves", "unclear"}:
            raise StoryError("持续吸引力报告复核结论无效")
        _text_fields(row, ("reason", "fix_intent"))
        _validate_saved_evidence(row, body,
            lower=window["span"][0] if row["verdict"] == "stalled" else 0, upper=window["span"][1])
        if row["verdict"] != "context_resolves":
            blockers.append(f"continuous_pull:{row['window_id']}:{row['verdict']}")
    return blockers


def _validate_saved_evidence(row: dict, body: str, *, lower: int, upper: int) -> None:
    if not isinstance(row, dict):
        raise StoryError("持续吸引力原文证据无效")
    span, quote = row.get("span"), row.get("quote")
    if (not isinstance(span, list) or len(span) != 2 or any(type(n) is not int for n in span)
            or not lower <= span[0] < span[1] <= upper or not isinstance(quote, str)
            or not quote.strip() or body[span[0]:span[1]] != quote):
        raise StoryError("持续吸引力证据不在指定正文区间")
