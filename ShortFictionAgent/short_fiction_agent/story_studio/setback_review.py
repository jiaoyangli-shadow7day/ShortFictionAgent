"""Check an explicitly promised loss and change of strategy in a story plan."""
from __future__ import annotations

import json
import re

from .contracts import StoryError


PROTOCOL = "setback-proof-v1"


def enabled(contract: dict) -> bool:
    author = contract.get("author_input") or {}
    return "损失一项已得成果" in str(author.get("premise") or "")


def prompt(plan: dict, contract: dict) -> str:
    scenes = plan.get("scenes") or []
    return (
        "独立核验作者指定的中段实质波折。你只审这一件事，不评价标题、文笔或结局爽点。"
        "先找主角前面已经实际到手的具体成果，再找对手的行动如何让主角失去这项成果，"
        "再找主角用不同于第一次的办法夺回或替代它。"
        "自愿支付少量调查费不等于对手夺走已得成果；只被吓到、看见假画面但毫发无损"
        "也不算。重复调用同一日志或同一举报入口，附加验价动作，也不算真正换打法。"
        "若本场拿走最有价值的系统能力，又在同一场靠新造的协议夺回并升级，"
        "只把较小的头衔损失留到下一场，也不算主线实质波折。"
        "必须核对失去的主要成果持续到下一场，并迫使主角在失去期间承担选择代价。"
        "若缺少任一环节，decision=revise，issues最多2项，每项必须是含path、reason、"
        "missing_link三个字符串字段的对象；path是应修改的具体场景字段路径。"
        "若pass，proof必须给出四个来自方案的10到120字连续原文片段："
        "prior_gain_quote、adversary_loss_quote、first_strategy_quote、changed_strategy_quote。"
        "若revise，proof可以为空对象。只返回JSON对象，包含decision、issues、proof。\n"
        + json.dumps({"protocol": PROTOCOL,
                      "author_request": (contract.get("author_input") or {}).get("premise"),
                      "scenes": scenes,
                      "ending_resolution": plan.get("ending_resolution")}, ensure_ascii=False)
    )


def validate(response: dict, plan: dict) -> dict:
    if not isinstance(response, dict) or response.get("decision") not in {"pass", "revise"}:
        raise StoryError("实质波折审查结论无效")
    issues, proof = response.get("issues"), response.get("proof")
    if not isinstance(issues, list) or len(issues) > 2 or not isinstance(proof, dict):
        raise StoryError("实质波折审查字段无效")
    if response["decision"] == "pass":
        if issues:
            raise StoryError("实质波折审查结论与问题冲突")
        keys = ("prior_gain_quote", "adversary_loss_quote", "first_strategy_quote",
                "changed_strategy_quote")
        source = json.dumps(plan, ensure_ascii=False)
        for key in keys:
            quote = proof.get(key)
            if not isinstance(quote, str) or not 10 <= len(quote) <= 120 or quote not in source:
                raise StoryError("实质波折通过缺少方案原文证明")
        if proof["first_strategy_quote"] == proof["changed_strategy_quote"]:
            raise StoryError("实质波折没有更换策略")
    else:
        if not issues:
            raise StoryError("实质波折否决缺少具体问题")
        normalized = []
        for issue in issues:
            if isinstance(issue, str) and re.fullmatch(
                    r"(?:plan\.)?scenes\[\d+\]\.(?:pressure|choice|consequence|progression_beats)",
                    issue):
                issue = {"path": issue,
                         "reason": "方案未证明对手夺走已取得的具体成果并迫使主角换用不同打法",
                         "missing_link": "写明先前到手的成果如何被对手夺走、主角实际承受的损失及不同于第一次的夺回行动；自愿调查费和再次举报均不算"}
            if (not isinstance(issue, dict) or
                    any(not isinstance(issue.get(key), str) or not issue[key].strip()
                        for key in ("path", "reason", "missing_link"))):
                raise StoryError("实质波折问题无效")
            normalized.append(issue)
        response = {**response, "issues": normalized}
    return response
