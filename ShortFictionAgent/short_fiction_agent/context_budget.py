"""统一的上下文预算工具：token 估算 + 截断。

此前 writer/context/llm 各写一份 `chinese*1.5+other*0.3`, 而 AgentRuntime 用的是
`chinese/1.5+other/4`(约 0.67/字), 两套估算相差约 2 倍 —— 既重复又会让 harness
预算检查与 agent/client 的预算检查互相打架。这里收敛为唯一来源。

标准估算: 中文约 1.5 token/字、其它约 0.3 token/字（保守, 与真机验证过的
agent/client 行为一致; harness 也统一到这个值）。
"""

from __future__ import annotations


def estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文 ~1.5/字, 其它 ~0.3/字）。"""
    if not text:
        return 0
    chinese = sum(1 for c in text if "一" <= c <= "鿿")
    other = len(text) - chinese
    return int(chinese * 1.5 + other * 0.3)


def truncate_text(text: str, max_chars: int, suffix: str = "\n\n（后续内容已截断）") -> str:
    """在段落/句子边界处截断到 max_chars 以内。"""
    text = text or ""
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    para = cut.rfind("\n\n")
    if para > int(max_chars * 0.55):
        return cut[:para].rstrip() + suffix
    line = cut.rfind("\n")
    if line > max_chars // 2:
        return cut[:line].rstrip() + suffix
    if "。" in cut:
        return cut.rsplit("。", 1)[0] + "。" + suffix
    return cut.rstrip() + suffix


def fit_head_tail(prompt: str, token_limit: int, marker: str,
                  head_ratio: float = 0.3, min_tail: int = 3000) -> str:
    """头尾保留式硬压缩：超预算时保留开头(规则)与结尾(当前任务), 中间塞 marker。"""
    if estimate_tokens(prompt) <= token_limit:
        return prompt
    char_budget = max(1200, int(token_limit / 1.5 * 0.95))  # 反推字符预算
    head_chars = max(400, int(char_budget * head_ratio))
    tail_chars = max(min_tail, char_budget - head_chars - len(marker))
    fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
    while estimate_tokens(fitted) > token_limit and tail_chars > min_tail:
        tail_chars = int(tail_chars * 0.85)
        fitted = prompt[:head_chars].rstrip() + marker + prompt[-tail_chars:].lstrip()
    return fitted

