"""Independently reconcile all plan fields after a local repair."""
from __future__ import annotations

import json

from .contracts import StoryError


PROTOCOL = "plan-reconciliation-v1"


def prompt(plan: dict, contract: dict, prior_issues: list[dict]) -> str:
    plan_view = {key: plan.get(key) for key in
                 ("scenes", "promises", "ending_resolution", "continuity_map")}
    author_view = {key: contract.get(key) for key in
                   ("author_input", "reader_promise", "constraints")}
    return (
        "你是独立的短篇连贯性编辑，不参与写作。局部修复后，必须横向核对同一事件在"
        "goal、choice、consequence、new_information、progression_beats、promises、"
        "ending_resolution和作者简报里的全部说法。特别追查前次问题是否只改了一个字段，"
        "其他字段仍沿用旧事实；核对人物身份取得的时间、现金收支、刑事/民事后果、"
        "已损失资源与后文使用、能力边界、作者禁止的解题捷径。"
        "尤其检查后段才出现的系统内核协议、代理权限、回放投影或证据获取能力："
        "若开头及简报没有建立，不能为了过关临时添加；不可让声称不可改的原始记录"
        "忽然被代理修改，也不可把主人公未亲眼见过的事件从脑内回放导出成录像。"
        "还要核对场次推进：第二次系统反扑若仍由第一次完全相同的方法零代价解决，"
        "不能称为实质波折。不要因前一个模型已判通过而放松。"
        "只返回JSON对象：decision为pass或revise；issues为数组，最多6项。"
        "每项恰有path、reason、missing_link、quote_a、quote_b。"
        "path必须是当前方案中应修正的具体字段路径；quote_a和quote_b各为原文连续"
        "10到100字，分别证明冲突两端，可来自方案或作者简报，不得改写。"
        "若一个旧问题在任何字段仍有相反说法，decision必须是revise。\n"
        + json.dumps({"protocol": PROTOCOL, "prior_issues": prior_issues,
                      "contract": author_view, "plan": plan_view},
                     ensure_ascii=False, separators=(",", ":"))
    )


def validate(response: dict, plan: dict, contract: dict) -> dict:
    if not isinstance(response, dict) or response.get("decision") not in {"pass", "revise"}:
        raise StoryError("方案横向复核缺少有效结论")
    issues = response.get("issues")
    if not isinstance(issues, list) or len(issues) > 6:
        raise StoryError("方案横向复核问题数量无效")
    if (response["decision"] == "pass") != (len(issues) == 0):
        raise StoryError("方案横向复核结论与问题冲突")
    source = json.dumps({"plan": plan, "contract": contract}, ensure_ascii=False)
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {
            "path", "reason", "missing_link", "quote_a", "quote_b"
        }:
            raise StoryError("方案横向复核问题字段无效")
        if any(not isinstance(issue[key], str) or not issue[key].strip()
               for key in issue):
            raise StoryError("方案横向复核问题内容为空")
        for key in ("quote_a", "quote_b"):
            quote = issue[key]
            if not 10 <= len(quote) <= 100 or quote not in source:
                raise StoryError("方案横向复核证据必须是当前内容中的连续原文")
    return response
