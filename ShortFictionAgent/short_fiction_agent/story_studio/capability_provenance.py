"""Independent source check for powers introduced in a speculative plan."""
from __future__ import annotations

import json

from .contracts import StoryError


PROTOCOL = "capability-provenance-v1"


def enabled(contract: dict) -> bool:
    author = contract.get("author_input") or {}
    text = str(author.get("premise") or "") + str(contract.get("reader_promise") or "")
    return any(word in text for word in ("系统", "异能", "超自然", "金手指"))


def prompt(plan: dict, contract: dict) -> str:
    author = contract.get("author_input") or {}
    scenes = plan.get("scenes") or []
    return (
        "你是独立的超自然规则来源审计员，只核对能力、权限和取胜办法。"
        "逐场从pressure、choice、consequence和progression_beats寻找系统能力或操作协议。"
        "每项新能力都要能在作者前提或更早场景找到明确规则与适用条件。"
        "'现实法律存证'不会自动成为系统内嵌校核权限；物理流血不会自动导致"
        "系统逻辑死锁、代理格式化或预演升级。奖池金额事先存在也不代表任何取款"
        "方法都成立。不得把不可改的原始记录改成可改。"
        "具体指出首次引入了哪项机制、此前文本缺哪条授权规则，以及需要修正的"
        "场景字段。只输出JSON对象：decision=pass或revise；issues最多4项，"
        "每项有path、reason、missing_link、use_quote四个字符串。use_quote必须是"
        "当前场景中10到120字的连续原文。若有一个关键能力无来源，必须revise。"
        "pass时issues为空，并给scene_ids_checked列出所有已审场景ID；revise时也列出。\n"
        + json.dumps({"protocol": PROTOCOL, "author_premise": author.get("premise"),
                      "locked_facts": author.get("locked_facts"), "reader_promise": contract.get("reader_promise"),
                      "scenes": scenes}, ensure_ascii=False, separators=(",", ":"))
    )


def validate(response: dict, plan: dict) -> dict:
    if not isinstance(response, dict) or response.get("decision") not in {"pass", "revise"}:
        raise StoryError("能力来源审查结论无效")
    issues = response.get("issues")
    ids = response.get("scene_ids_checked")
    expected = [scene.get("scene_id") for scene in plan.get("scenes") or []]
    if not isinstance(issues, list) or len(issues) > 4 or ids != expected:
        raise StoryError("能力来源审查场景覆盖或问题数量无效")
    if (response["decision"] == "pass") != (len(issues) == 0):
        raise StoryError("能力来源审查结论与问题冲突")
    source = json.dumps(plan.get("scenes") or [], ensure_ascii=False)
    for issue in issues:
        if not isinstance(issue, dict) or set(issue) != {"path", "reason", "missing_link", "use_quote"}:
            raise StoryError("能力来源审查问题字段无效")
        if any(not isinstance(value, str) or not value.strip() for value in issue.values()):
            raise StoryError("能力来源审查问题内容为空")
        if not 10 <= len(issue["use_quote"]) <= 120 or issue["use_quote"] not in source:
            raise StoryError("能力来源审查缺少当前方案连续原文")
    return response
