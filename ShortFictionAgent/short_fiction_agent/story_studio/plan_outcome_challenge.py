"""Challenge large real-world wins that a broad payoff review can miss."""
from __future__ import annotations

import json
from copy import deepcopy

from .contracts import StoryError
from . import editorial_priority


PROTOCOL = "plan-outcome-challenge-v2-reader-priority"
TRIGGERS = ("保证金", "冠名权", "解除合同", "转签", "股权", "废标", "代理权")
LINKS = ("prior_right", "opponent_attribution", "decision_maker", "execution_step")


def enabled(plan: dict, contract: dict) -> bool:
    if contract.get("format") != "short_story" or not contract.get("reader_rewards"):
        return False
    outcome = " ".join(str(scene.get("consequence") or "") for scene in plan.get("scenes") or [])
    outcome += " " + str(plan.get("ending_resolution") or "")
    return any(term in outcome for term in TRIGGERS)


def prompt(plan: dict, contract: dict) -> str:
    return (
        editorial_priority.INSTRUCTION +
        "你是独立的重大现实后果挑战者，只审方案中对手损失最大的合同、订单、保证金、"
        "商业资格或股权结果。已有的整体爽点/回报审查通过也不能当证据。先逐字引用最重的"
        "结果声称，然后观察四条连接对本篇阅读体验的实际作用：prior_right=哪项此前存在的权利或协议受影响；"
        "opponent_attribution=具体证据如何把违约行为归到承担后果的一方；"
        "decision_maker=谁有权决定该结果及其动机；execution_step=必要的核验、通知、"
        "改选或其他执行动作是否发生。可依据架空世界既定规则，不强求现实案件当场定罪；"
        "金额巨大本身不增加核算或制度手续要求。只需读者能明白主角做了什么、对方为何接招、"
        "实际赢到什么；不要为版权授权、通知或结算手续补齐逼出长篇说明。缺少手续但取得与反击"
        "已经看懂的，属于copyedit_detail；主角只有泼水，对手就无缘无故失去全部商业筹码，"
        "则是回报取得过程缺失，属于reading_core。场景没有写出的连接仍标missing，source_quote写'未写'；"
        "不得凭作者愿望、ending声称或行业常识补出证据。只输出JSON对象："
        "decision为pass/revise；claim_quote为场景中连续原文；checks严格四项，"
        "每项含link、status(supported/missing)、source_quote(场景连续原文或'未写')、"
        "reason、editorial_priority(reading_core/copyedit_detail)、reading_effect（具体读者影响，"
        "细节说明为何不妨碍理解或爽感）。只有reading_core的missing才要求decision=revise；"
        "copyedit_detail的missing只作初始校对提醒，不能要求重规划。不得将手续问题改称关键因果后阻断。\n"
        + json.dumps({"author_contract": contract.get("author_input"),
                      "scenes": plan.get("scenes"),
                      "ending_resolution": plan.get("ending_resolution")},
                     ensure_ascii=False, separators=(",", ":"))
    )


def validate(response: dict, plan: dict) -> dict:
    if not isinstance(response, dict) or response.get("decision") not in {"pass", "revise"}:
        raise StoryError("重大后果挑战结论无效")
    source = json.dumps(plan.get("scenes") or [], ensure_ascii=False)
    quote = response.get("claim_quote")
    if not isinstance(quote, str) or not 8 <= len(quote) <= 180 or quote not in source:
        raise StoryError("重大后果挑战缺少场景中的结果原文")
    checks = response.get("checks")
    if not isinstance(checks, list) or len(checks) != len(LINKS) or {row.get("link") for row in checks if isinstance(row, dict)} != set(LINKS):
        raise StoryError("重大后果挑战缺少四项因果连接")
    normalized = deepcopy(response)
    normalized['checks'] = []
    for raw_row in checks:
        row = editorial_priority.normalize(raw_row)
        if (row.get("status") not in {"supported", "missing"} or
                not isinstance(row.get("reason"), str) or not row["reason"].strip()):
            raise StoryError("重大后果挑战判断无效")
        evidence = row.get("source_quote")
        if not isinstance(evidence, str) or not evidence.strip():
            raise StoryError("重大后果挑战缺少证据原文")
        if row["status"] == "supported":
            if evidence == "未写" or evidence not in source or evidence == quote:
                raise StoryError("重大后果挑战把结果声称当成取得证据")
        elif evidence != "未写" and evidence not in source:
            raise StoryError("重大后果挑战引用不存在的场景原文")
        normalized['checks'].append(row)
    blockers = [row for row in normalized['checks']
                if row['status'] == 'missing' and not editorial_priority.is_detail(row)]
    reminders = [deepcopy(row) for row in normalized['checks']
                 if row['status'] == 'missing' and editorial_priority.is_detail(row)]
    if ((response['decision'] == 'pass' and blockers) or
            (response['decision'] == 'revise' and not blockers and not reminders)):
        raise StoryError("重大后果挑战结论与因果连接冲突")
    normalized['model_decision'] = response.get('model_decision', response['decision'])
    normalized['decision'] = 'revise' if blockers else 'pass'
    normalized['initial_copyedit_reminders'] = reminders
    return normalized


def issues(response: dict) -> list[dict]:
    return [{"path": "scenes", "reason": row["reason"],
             "missing_link": row["link"] + ": " + row["reason"], "repair_scope": "replan",
             **{key: row[key] for key in ('editorial_priority', 'reading_effect') if key in row}}
            for row in response["checks"]
            if row["status"] == "missing" and not editorial_priority.is_detail(row)]
