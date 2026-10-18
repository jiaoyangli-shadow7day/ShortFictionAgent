"""Writing-time entry samples and grounded editorial vetoes for Fanqie shorts.

Structure and quotation checks are deterministic. Whether a reader wants to click,
whether a desire matters, and whether a turn changes the situation are model
judgments, recorded separately from the existing causal feasibility assessment.
"""
from __future__ import annotations

import json

from .contracts import StoryError, digest
from .selection_review import comparison_input_hash as causal_input_hash
from .selection_review import validate_comparison as validate_causal_comparison


PROTOCOL = "story-entry-pack-v1"
_TEXT = {"type": "string", "minLength": 1}
_TURN_FIELDS = ("event", "before", "after", "new_action")
_CANDIDATE_TEXT_FIELDS = (
    "protagonist", "goal", "obstacle", "cost", "unique_element", "core_scene",
    "ending", "promise", "weakness", "working_title", "read_question", "ending_payoff",
)

ENTRY_PACK_SCHEMA = {
    "type": "object",
    "required": ["working_title", "provisional_lead", "read_question", "turns", "ending_payoff"],
    "properties": {
        "working_title": _TEXT,
        "provisional_lead": {
            "type": "object", "required": ["first_sentence", "concrete_event", "text"],
            "properties": {key: _TEXT for key in ("first_sentence", "concrete_event", "text")},
        },
        "read_question": _TEXT,
        "turns": {"type": "array", "minItems": 3, "items": {
            "type": "object", "required": list(_TURN_FIELDS),
            "properties": {key: _TEXT for key in _TURN_FIELDS},
        }},
        "ending_payoff": _TEXT,
    },
}

_ASSESSMENT_SCHEMA = {
    "type": "object", "required": ["decision", "basis", "evidence"],
    "properties": {
        "decision": {"enum": ["pass", "reject"]}, "basis": _TEXT,
        "evidence": {"type": "array", "minItems": 1, "items": {
            "type": "object", "required": ["field", "quote"],
            "properties": {"field": _TEXT, "quote": _TEXT},
        }},
    },
}
COMPARISON_SCHEMA = {
    "type": "object", "required": ["comparisons", "recommended_id"],
    "$defs": {"assessment": _ASSESSMENT_SCHEMA},
    "properties": {
        "recommended_id": {"type": ["string", "integer", "null"]},
        "comparisons": {"type": "array", "items": {
            "type": "object",
            "required": ["candidate_id", "rank", "strength", "weakness", "execution_risk",
                         "feasibility", "entry_review"],
            "properties": {
                "candidate_id": {"type": ["string", "integer"]},
                "rank": {"type": "integer", "minimum": 1},
                "strength": _TEXT, "weakness": _TEXT, "execution_risk": _TEXT,
                "entry_review": {
                    "type": "object",
                    "required": ["decision", "click", "readthrough", "desire", "turns", "ending"],
                    "properties": {
                        "decision": {"enum": ["pass", "reject"]},
                        **{key: {"$ref": "#/$defs/assessment"}
                           for key in ("click", "readthrough", "desire", "ending")},
                        "turns": {"type": "array", "minItems": 3, "items": {
                            "allOf": [{"$ref": "#/$defs/assessment"}, {
                                "type": "object", "required": ["turn_index"],
                                "properties": {"turn_index": {"type": "integer", "minimum": 0}},
                            }],
                        }},
                    },
                },
            },
        }},
    },
}

ENTRY_RUBRIC = {
    "scope": "内部编辑假设；不能预测CTR、推流、真实停读点或收益。",
    "author_priority": "作者既定事实、题材和本篇风格优先。允许安静情感与普通甜宠；不得为了入口强塞背叛、财产、危险或打脸。未限定时也不擅自补上双方必须体面成熟等限制。",
    "click": "先单看工作标题和导语首句：陌生读者知道谁发生了什么，反常/关系利害在哪里，为什么想看后果。只表达正确立场、主题或情绪，不能单凭顺口判通过。短标题可以成立，须说明它依赖的具体上下文。",
    "readthrough": "再看导语试写与read_question：当前事件怎样产生一个具体疑问，人物下一步做什么值得看。含混指代、作者空泛预告、仅完成沟通或办事的过程不能自动算期待。",
    "desire": "人物当下想得到或保住什么，事件如何威胁/诱使其作选择？亲近、吃醋、自尊和失去一次机会也可成立；若没有具体欲望，只靠理解、正确沟通或流程闭合，明确reject。",
    "turns": "逐次比较before与after，核对行动、信息、筹码或关系发生了什么实质变化，为什么原办法需要改变。换道具、换名重复试探、按步骤办完手续和增加事故不自动算转折；安静的关系选择可以改变局面。",
    "ending": "结尾具体兑现哪个入口疑问和情绪承诺？对手回应与主角新行动怎样带来回报？道理总结不能代替可见回报；不能把设想的正文当成已经写出的证据。",
    "independence": "先给入口判断，再独立给feasibility。逻辑成立不能替代吸引力，有噱头不能替代因果成立；不得把审美厌恶、俗套或不体面冒充核心因果缺口。",
}


def applies(contract: dict) -> bool:
    return "fanqie_short" in (contract.get("channels") or [])


def generation_guidance() -> str:
    return (
        "\n番茄短篇写前入口样包：仍一次给四案，每案保留原有策划字段，另加下列字段。"
        "工作标题是本案预览，不覆盖作者既定标题；作者前提、锁定事实、题材与本篇风格优先。"
        "未锁定时四案尽量在核心机制上不同，不能只是四种暗恋试探或同一流程换职业。"
        "provisional_lead.first_sentence是text原样第一句前缀；text是约80—180字的导语开头试写，"
        "必须让一个具体事件实际发生，concrete_event概括该事件。字数仅为写作建议。"
        "它是前置样包，不能直接充当投稿导语；正式导语必须等待成稿并按正文核验。"
        "read_question写一个具体追读问题；goal写人物具体欲望和利害。"
        "turns至少三项，每项event写发生的事、before/after写局面如何改变、"
        "new_action写主角因此改用什么办法；长篇幅可继续延展。"
        "ending_payoff写入口承诺怎样得到可见兑现，不用主题宣言替代。"
        "可以克制、甜宠或生活喜剧，不强制背叛、财产、极端危险或打脸；"
        "允许作者选择的私心、争夺、失误和不体面行为产生真实后果。"
        "结构如下：\n" + json.dumps(ENTRY_PACK_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    )


def comparison_guidance() -> str:
    return (
        "\n入口先审：对全部四案在同一次比较里增加entry_review，保留独立feasibility。"
        "先判断是否值得点开、是否有具体追读期待，再审因果。不要因为四案里排名第一就给pass；"
        "允许入口全部reject，或有入口但因果全部revise，此时recommended_id=null。"
        "推荐只能是viable=true、entry_review.decision=pass且feasibility.decision=pass的最高排名案。"
        "每个click/readthrough/desire/ending判断以及每项turns判断都输出decision:pass或reject、"
        "具体basis与evidence:[{field,quote}]；quote必须是对应候选指定字段内的连续原文，"
        "不得引用别的候选、作者说明或未来正文。"
        "click证据至少含working_title和provisional_lead.first_sentence；"
        "readthrough至少含provisional_lead.text和read_question；desire至少含goal；"
        "turns逐项覆盖，turn_index从0起，证据至少含对应turns.0.after等字段；"
        "ending至少含ending_payoff。局面是否真变、欲望是否有力由你依据原文判断，"
        "不能用术语黑名单、句长、细节数量替代语义审读。"
        "任一子项reject则entry_review.decision=reject；所有子项pass才整体pass。"
        "因果blocking_claims仍按原协议给候选原文和缺失支撑；不要把俗套或格调混入feasibility。"
        "入口标准：\n" + json.dumps(ENTRY_RUBRIC, ensure_ascii=False, separators=(",", ":"))
        + "\n比较新增结构（feasibility沿用原独立协议）：\n"
        + json.dumps(COMPARISON_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    )


def planning_guidance() -> str:
    return (
        "选中候选的working_title、provisional_lead、read_question、turns、ending_payoff是写前承诺。"
        "场景大纲须明确哪里推进这个具体追读问题、怎样让转折改变办法、如何兑现结尾；"
        "不要把人物欲望和交锋改成只完成沟通/手续的流程。"
        "保留作者设定与工作标题的事实边界；provisional_lead只是试写，正式导语仍绑定完成正文。"
    )


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_candidates(response: dict, *, expected_count: int = 4) -> list[dict]:
    options = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(options, list) or len(options) != expected_count or not options:
        raise StoryError(f"入口样包必须返回{expected_count}个候选")
    ids = []
    for candidate in options:
        if not isinstance(candidate, dict):
            raise StoryError("入口样包候选必须是对象")
        identifier = candidate.get("id")
        if not (_nonempty(identifier) or type(identifier) is int):
            raise StoryError("入口样包候选ID必须非空且有效")
        ids.append(str(identifier).strip())
        if any(not _nonempty(candidate.get(key)) for key in _CANDIDATE_TEXT_FIELDS):
            raise StoryError("入口样包缺少具体策划字段、工作标题、追读问题或结尾回报")
        if type(candidate.get("viable")) is not bool:
            raise StoryError("入口样包viable必须是布尔值")
        lead = candidate.get("provisional_lead")
        if not isinstance(lead, dict) or any(not _nonempty(lead.get(key))
                for key in ("first_sentence", "concrete_event", "text")):
            raise StoryError("入口样包缺少导语首句、具体事件或开头试写")
        if not lead["text"].startswith(lead["first_sentence"]):
            raise StoryError("入口样包导语首句必须与开头试写的原文前缀一致")
        turns = candidate.get("turns")
        if not isinstance(turns, list) or len(turns) < 3 or any(
                not isinstance(turn, dict) or any(not _nonempty(turn.get(key)) for key in _TURN_FIELDS)
                for turn in turns):
            raise StoryError("入口样包至少需要三个含事件、前后局面与新行动的转折")
    if len(set(ids)) != len(ids):
        raise StoryError("入口样包候选ID必须唯一")
    return options


def _source_fields(candidate: dict) -> dict[str, str]:
    fields = {key: candidate[key] for key in _CANDIDATE_TEXT_FIELDS}
    fields.update({f"provisional_lead.{key}": value
                   for key, value in candidate["provisional_lead"].items()
                   if key in ("first_sentence", "concrete_event", "text")})
    fields.update({f"turns.{index}.{key}": turn[key]
                   for index, turn in enumerate(candidate["turns"]) for key in _TURN_FIELDS})
    return fields


def _assessment(check, source, required_fields, label):
    if not isinstance(check, dict) or check.get("decision") not in ("pass", "reject") \
            or not _nonempty(check.get("basis")):
        raise StoryError(f"入口{label}缺少独立判断或具体理由")
    evidence = check.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise StoryError(f"入口{label}缺少原文证据")
    anchored = set()
    for anchor in evidence:
        if not isinstance(anchor, dict) or not _nonempty(anchor.get("field")) \
                or not _nonempty(anchor.get("quote")):
            raise StoryError(f"入口{label}原文证据字段无效")
        field, quote = anchor["field"], anchor["quote"]
        if field not in source or quote not in source[field]:
            raise StoryError(f"入口{label}证据必须准确引用对应候选的指定字段原文")
        anchored.add(field)
    if not set(required_fields).issubset(anchored):
        raise StoryError(f"入口{label}缺少当前入口或兑现位置的原文锚点")
    return check["decision"]


def _validate_entry_review(review, candidate):
    if not isinstance(review, dict) or review.get("decision") not in ("pass", "reject"):
        raise StoryError("候选比较缺少独立入口判断")
    source = _source_fields(candidate)
    decisions = [
        _assessment(review.get("click"), source,
                    ("working_title", "provisional_lead.first_sentence"), "点击"),
        _assessment(review.get("readthrough"), source,
                    ("provisional_lead.text", "read_question"), "追读"),
        _assessment(review.get("desire"), source, ("goal",), "人物欲望"),
        _assessment(review.get("ending"), source, ("ending_payoff",), "结尾回报"),
    ]
    turns = review.get("turns")
    if not isinstance(turns, list) or len(turns) != len(candidate["turns"]) or any(
            not isinstance(turn, dict) or type(turn.get("turn_index")) is not int for turn in turns):
        raise StoryError("入口比较必须逐项判断全部转折")
    indices = [turn["turn_index"] for turn in turns]
    if sorted(indices) != list(range(len(candidate["turns"]))):
        raise StoryError("入口比较转折编号必须完整且唯一")
    for turn in turns:
        index = turn["turn_index"]
        decisions.append(_assessment(turn, source, (f"turns.{index}.after",), f"转折{index + 1}"))
    expected = "pass" if all(decision == "pass" for decision in decisions) else "reject"
    if review["decision"] != expected:
        raise StoryError("入口整体结论与点击、追读、欲望、转折或结尾判断矛盾")


def validate_comparison(response: dict, options: list[dict]) -> dict:
    validate_candidates({"candidates": options}, expected_count=len(options))
    rows = response.get("comparisons") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != len(options):
        raise StoryError("入口比较必须逐项覆盖全部候选")
    by_id = {str(candidate["id"]): candidate for candidate in options}
    for row in rows:
        if not isinstance(row, dict) or str(row.get("candidate_id")) not in by_id:
            raise StoryError("入口比较引用了不存在的候选ID")
        _validate_entry_review(row.get("entry_review"), by_id[str(row["candidate_id"])])
    result = validate_causal_comparison(response, options,
        eligibility_check=lambda row: row["entry_review"]["decision"] == "pass")
    return {**result, "entry_protocol": PROTOCOL,
            "entry_assessment_scope": "internal_editorial_only",
            "entry_commercial_validation": "unverified"}


def require_entry_choice(comparison: dict, candidate_id) -> None:
    row = next((row for row in comparison.get("comparisons", [])
                if str(row.get("candidate_id")) == str(candidate_id)), None)
    if not row or (row.get("entry_review") or {}).get("decision") != "pass":
        raise StoryError("所选候选入口被独立否决；请修订样包并重新开发")


def comparison_input_hash(contract: dict, options: list[dict], source_hash: str) -> str:
    return digest({"causal_input_hash": causal_input_hash(contract, options, source_hash),
                   "entry_protocol": PROTOCOL, "entry_rubric": ENTRY_RUBRIC,
                   "entry_pack_schema": ENTRY_PACK_SCHEMA, "comparison_schema": COMPARISON_SCHEMA})
