"""Market contract validation, guidance, and evidence-grounded review gates."""

from __future__ import annotations

import json
from typing import Any

from .contracts import StoryError

# Editorial hypotheses checked 2026-09-30, not platform rules or market-share data.
MARKET_SIGNAL_SOURCES = [
    {"url": "https://fanqienovel.com/writer/zone/article/7683816556886753342",
     "scope": "番茄官方世情创作指引；不代表全站读者偏好"},
    {"url": "https://fanqienovel.com/writer/zone/article/7688608349599825982",
     "scope": "番茄官方短篇改编入选案例；不代表收益或转化率"},
]

MARKET_PROFILES = {
    "fanqie_shiqing": {
        "name": "番茄世情/现实向短篇",
        "required_channel": "fanqie_short",
        "description": "利益、代际与人情冲突中，人物主动选择并改变处境；可以反制，也可以有代价地和解。",
    },
    "fanqie_relationship": {
        "name": "番茄情感/关系向短篇",
        "required_channel": "fanqie_short",
        "description": "关系变化由可观察的选择推动；清醒成长、甜宠或和解都须兑现简报中的情感承诺。",
    },
    "douyin_carousel": {
        "name": "抖音图文/快节奏故事",
        "required_channel": "douyin_carousel",
        "description": "图文首屏交代具体处境，后续卡片有信息或行动进展，末页回应首屏承诺；属于改编试验。",
    },
}

MARKET_FIELDS = (
    "profile_id",
    "reader_emotion",
    "opening_conflict",
    "protagonist_action",
    "payoff",
)
ALLOWED_MARKET_FIELDS = frozenset(MARKET_FIELDS)

MARKET_CHECK_KEYS = (
    "opening_conflict",
    "protagonist_action",
    "payoff",
    "reader_emotion",
)


def validate_market_contract(
    raw: dict[str, Any] | None,
    channels: list[str],
    audience: dict[str, Any] | None,
    reader_promise: str,
) -> dict[str, str]:
    """Validate optional market contract and enforce channel, audience, and promise constraints."""
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise StoryError("market_contract 必须是字典映射")
    if not raw:
        return {}

    unknown = set(raw) - ALLOWED_MARKET_FIELDS
    if unknown:
        raise StoryError(f"market_contract 包含未知字段: {sorted(unknown)}")

    missing = ALLOWED_MARKET_FIELDS - set(raw)
    if missing:
        raise StoryError(f"market_contract 缺少必要字段: {sorted(missing)}")

    contract: dict[str, str] = {}
    # Contract dictionaries also enter model prompts and persisted YAML. A set
    # changes their insertion order across interpreter hash seeds.
    for key in MARKET_FIELDS:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            raise StoryError(f"market_contract.{key} 必须是非空字符串")
        contract[key] = val.strip()

    profile_id = contract["profile_id"]
    if profile_id not in MARKET_PROFILES:
        raise StoryError(f"未知的 market profile_id: {profile_id}，可选值为: {sorted(MARKET_PROFILES)}")

    required_channel = MARKET_PROFILES[profile_id]["required_channel"]
    if not isinstance(channels, list) or any(not isinstance(x, str) for x in channels):
        raise StoryError("channels 必须是字符串列表")
    if required_channel not in channels:
        raise StoryError(
            f"启用 market profile '{profile_id}' 时，channels 必须包含 '{required_channel}'"
        )

    if not isinstance(reader_promise, str) or not reader_promise.strip():
        raise StoryError(f"启用 market profile '{profile_id}' 时，必须在简报中指定明确的 reader_promise")

    if not isinstance(audience, dict):
        raise StoryError("audience 必须是字典映射")
    audience_desc = audience.get("description")
    if not isinstance(audience_desc, str):
        raise StoryError("audience.description 必须是字符串")
    audience_desc = audience_desc.strip()
    if not audience_desc or audience_desc == "中文故事读者":
        raise StoryError(
            f"启用 market profile '{profile_id}' 时，必须提供明确具体的 audience.description（不能为缺省泛化描述）"
        )

    return contract


def market_guidance(contract: dict[str, Any]) -> str:
    """Produce editorial market guidance for outline and draft generation."""
    market = contract.get("market_contract") or {}
    if not market:
        return ""

    profile_id = market.get("profile_id", "")
    meta = MARKET_PROFILES.get(profile_id, {})
    profile_name = meta.get("name", profile_id)
    profile_desc = meta.get("description", "")

    return (
        f"【市场与读者合同指导（编辑试验方向，非官方收益保证/不设机械爽点阈值）】\n"
        f"- 试验定位: {profile_name}（{profile_desc}）\n"
        f"- 目标读者情绪: {market.get('reader_emotion')}\n"
        f"- 开篇冲突承诺: {market.get('opening_conflict')}\n"
        f"- 主角行动承诺: {market.get('protagonist_action')}\n"
        f"- 回响/交代承诺: {market.get('payoff')}\n"
        f"- 创作原则: 尊重现实逻辑与因果代价，不设固定字数爽点阈值，不强制暴富或获刑；"
        f"所有回响必须来自主角具体选择与情节闭环。"
    )


def market_review_prompt(body: str, contract: dict[str, Any]) -> str:
    """Generate prompt for standalone LLM market contract review."""
    market = contract.get("market_contract") or {}
    return (
        "你是严谨的市场与读者合同审查编辑。请逐项核对故事正文是否真正兑现了作者承诺的市场与读者合同。\n"
        "注意：这是编辑试验方向审查，不强制反派获刑或机械爽点，重在检查承诺是否成立、是否落空。\n"
        "reader_emotion只判断正文能否支持期待的情绪，不代表真实读者反馈或销量。"
        "已有开篇冲突与本次指定的开篇冲突不同；不要把不符合新合同误报成原稿没有任何冲突。\n"
        "请审查以下4个项目：\n"
        "1. opening_conflict（开篇冲突）\n"
        "2. protagonist_action（主角行动）\n"
        "3. payoff（回响与交代）\n"
        "4. reader_emotion（目标读者情绪达成）\n\n"
        "输出JSON对象，包含 checks 数组，必须恰好4项，每项包含：\n"
        "- id: 对应上述4个标识之一（opening_conflict / protagonist_action / payoff / reader_emotion）\n"
        "- verdict: 只能是 fulfilled（已兑现）/ missing（缺失未兑现）/ unclear（含糊不清/证据不足）\n"
        "- quote: 正文中连续的真实引文（字符串）。verdict为fulfilled时必须提供确凿引文；missing时可为空字符串；引文必须是正文子串\n"
        "优先复制10至25字的连续短原文；不得拼接不同位置的句子、改字、添加省略号或删除引文内部换行。解释只写在reason中。\n"
        "- reason: 具体详细的审查判定理由，指出正文何处如何兑现或为何落空\n\n"
        + json.dumps(
            {
                "market_contract": market,
                "reader_promise": contract.get("reader_promise", ""),
                "audience": contract.get("audience"),
                "profile_guidance": market_guidance(contract),
                "body": body,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def market_gate(
    body: str,
    contract: dict[str, Any],
    checks: list[dict[str, Any]] | None = None,
) -> list[str]:
    """Validate market contract fulfillment from review checks and return blockers."""
    market = contract.get("market_contract") or {}
    checks = [] if checks is None else checks
    if not market:
        if checks:
            raise StoryError("审查报告包含合同之外的市场承诺审查项")
        return []

    if not isinstance(checks, list) or len(checks) != len(MARKET_CHECK_KEYS):
        raise StoryError("市场承诺审查结果不完整，必须包含全部4项审查项")

    by_id: dict[str, dict[str, Any]] = {}
    for item in checks:
        if not isinstance(item, dict):
            raise StoryError("市场承诺审查项必须是对象")
        check_id = item.get("id")
        if not isinstance(check_id, str) or check_id not in MARKET_CHECK_KEYS:
            raise StoryError(f"未知的市场承诺审查项 ID: {check_id}")
        if check_id in by_id:
            raise StoryError(f"市场承诺审查项 ID 重复: {check_id}")
        by_id[check_id] = item

    if set(by_id.keys()) != set(MARKET_CHECK_KEYS):
        raise StoryError(f"市场承诺审查项 ID 覆盖不全: 缺少 {sorted(set(MARKET_CHECK_KEYS) - set(by_id.keys()))}")

    blockers: list[str] = []
    for key in MARKET_CHECK_KEYS:
        check = by_id[key]
        verdict = check.get("verdict")
        quote = check.get("quote")
        reason = check.get("reason")

        if verdict not in ("fulfilled", "missing", "unclear"):
            raise StoryError(f"市场承诺判断 verdict 无效: {key}={verdict}")
        if not isinstance(quote, str):
            raise StoryError(f"市场承诺 quote 必须是字符串: {key}")
        if quote and quote not in body:
            raise StoryError(f"市场承诺引用不在正文内（非法原文）: {key}")
        if verdict == "fulfilled" and not quote.strip():
            raise StoryError(f"市场承诺判定为 fulfilled 时必须提供正文引文证据: {key}")
        if not isinstance(reason, str) or not reason.strip():
            raise StoryError(f"市场承诺缺少具体判定理由: {key}")

        if verdict in ("missing", "unclear"):
            blockers.append(f"market:{key}:{verdict}")

    return blockers
