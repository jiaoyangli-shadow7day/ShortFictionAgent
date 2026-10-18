"""Independent check that a replacement candidate repairs the rejected mechanism."""
from __future__ import annotations

import json

from .contracts import StoryError


PROTOCOL = "candidate-reselection-v1"


def prompt(candidate: dict, rejected: dict, contract: dict | None = None) -> str:
    return (
        "你是没有参与候选生成的责任编辑。旧稿因整篇结构问题被否决；现在只审推荐的新候选"
        "是否真的换了失败的核心对抗机制。必须直接比较旧问题和候选的"
        "obstacle、cost、unique_element、core_scene、ending。"
        "同一举报入口反复发送指令，即使换名为合规审查、安全总线、权限熔断，"
        "仍然是同法重复；加轻微扣薪、划伤或改职业道具不能算新的持续损失。"
        "不要只检查纸面上有无损失：谁实际支付、为何不能由公司按正常流程采购或承担、"
        "损失如何持续影响下一场，都要成立。第二次权限回收须说明与第一次不同的"
        "触发证据、行动和不可逆后果；把同一申诉改称仲裁、自担没有金额和来源的"
        "违约金不算新机制。新权限必须能从作者前提找到明确来源，或候选明确安排"
        "在开篇显示原始授权、适用条件和代价；不能仅自称'初始协议'、'内置条款'就算铺垫。"
        "反派的物证要能证明其行为而非仅证明工具归属；曝光后合同相对方和投资者"
        "各自能作出什么决定、为何会失去真实筹码，不能靠众人哗然自动推导。"
        "如果新候选仍落入旧问题，decision=revise并给issues，最多2项；"
        "每项有reason、missing_link、candidate_quote三个字符串，candidate_quote必须"
        "是候选原文中10到120字的连续片段。"
        "如果pass，issues为空，difference必须具体写出旧方法和新方法分别是什么，"
        "并给candidate_quote证明新方法。不要因为候选比较曾给高分就自动通过。"
        "只输出JSON对象：decision、issues、difference、candidate_quote。\n"
        + json.dumps({'protocol': PROTOCOL,
                      'author_contract': {'premise': ((contract or {}).get('author_input') or {}).get('premise'),
                                          'locked_facts': ((contract or {}).get('author_input') or {}).get('locked_facts')},
                      'rejected_draft': rejected,
                      'recommended_candidate': candidate}, ensure_ascii=False,
                     separators=(',', ':'))
    )


def validate(response: dict, candidate: dict) -> dict:
    if not isinstance(response, dict) or response.get('decision') not in {'pass', 'revise'}:
        raise StoryError('重选候选独立审查结论无效')
    issues = response.get('issues')
    if not isinstance(issues, list) or len(issues) > 2:
        raise StoryError('重选候选独立审查问题无效')
    if (response['decision'] == 'pass') != (len(issues) == 0):
        raise StoryError('重选候选独立审查结论与问题冲突')
    source = json.dumps(candidate, ensure_ascii=False)
    if response['decision'] == 'pass':
        quote = response.get('candidate_quote')
        if not isinstance(response.get('difference'), str) or len(response['difference']) < 20:
            raise StoryError('重选候选通过缺少机制差异说明')
        if not isinstance(quote, str) or not 10 <= len(quote) <= 120 or quote not in source:
            raise StoryError('重选候选通过缺少新方案原文证据')
    else:
        if not issues:
            raise StoryError('重选候选否决缺少问题')
        for issue in issues:
            if not isinstance(issue, dict) or set(issue) != {'reason', 'missing_link', 'candidate_quote'}:
                raise StoryError('重选候选问题字段无效')
            quote = issue['candidate_quote']
            if not all(isinstance(issue[k], str) and issue[k].strip() for k in issue):
                raise StoryError('重选候选问题内容为空')
            if not 10 <= len(quote) <= 120 or quote not in source:
                raise StoryError('重选候选问题缺少候选原文证据')
    return response
