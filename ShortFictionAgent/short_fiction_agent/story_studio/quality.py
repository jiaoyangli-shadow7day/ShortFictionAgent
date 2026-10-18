"""Evidence-based full-text review with version-bound decisions."""

from __future__ import annotations

import json
import re
import shutil
from copy import deepcopy
from uuid import uuid4

from .market import market_gate, market_review_prompt
from .research_guidance import research_context, model_research_context, guidance_fingerprint
from .contracts import StoryError, digest, visible_count
from .storage import StoryProject, write_json, read_json, now
from .selection_review import OPENING_RUBRIC, CAUSAL_RUBRIC
from . import narrative_setup
from . import whole_story
from . import lead
from . import reader_rewards
from . import prose_integrity
from . import reader_experience
from . import chief_editor
from . import chief_review, claim_audit, submission_guide, expectation_ledger, chief_first_read, editorial_priority, target_reader
from .engagement import (
    PROTOCOL as ENGAGEMENT_PROTOCOL, GUIDANCE as ENGAGEMENT_GUIDANCE,
    enabled as engagement_enabled, windows as engagement_windows,
    validate_observations, validate_confirmations, suspects as engagement_suspects,
    gate as engagement_gate, REVIEW_INSTRUCTION, CONFIRM_INSTRUCTION,
)


DIMENSIONS = (
    "opening_pull", "causal_progress", "character_specificity", "payoff",
    "economy", "coherence", "voice", "channel_fit",
)
CRITICAL = ("opening_pull", "causal_progress", "payoff", "coherence")
REVIEW_RUBRIC = {
    "editorial_priority": editorial_priority.INSTRUCTION,
    "target_reader": target_reader.REVIEW_INSTRUCTION,
    "contradiction_severity": "只将读者可直接感知、损害核心人物行动或结局兑现的矛盾报告为blocker并引用正文。已知信息不能在后文被写成首次得知；明确失败的行动不能无新转机变成成功。不要核算无关小账或补齐法律手续；细小金额、日期和手续差异只作copyedit_detail，不能仅以其它维度低分或数量累积阻断。技术或商业回报须让读者理解赢法，不要求完整行业操作清单。",
    "relationship_payoff": CAUSAL_RUBRIC['earned_relationship_change'],
    # The complete causal rubric is supplied once below, including at full-body review.
    "opening": {key: value for key, value in OPENING_RUBRIC.items() if key != 'causal_feasibility'},
    "causal_feasibility": CAUSAL_RUBRIC,
    "opening_windows": "有独立导语时，导语与第一章分别单读，再检查衔接；导语允许预告结果，不能要求预告中的动作在章首已经执行。前80个非空白字符观察入场是否清楚，前1000字符观察追读问题如何推进；都是内部编辑观察窗，不是平台门槛。只评价开头片段，不要求中间片段重新建立钩子。",
    "language": "语言简短清楚，人物指代明确，情绪由可观察行动、对话和细节承载；检查背景先行、泛泛感叹、主题口号、重复解释和误导性双关。不要把所有题材改成极端羞辱、危险或打脸。",
    "payoff": "对照原始受众、题材与标题导语，用一句话复述实际入场时想看的体验，再核对正文和结尾是否持续兑现。方案主问题只是解释，不能覆盖原始基准；能力退出、主题成熟或关系阶段变化不自动证明回报。不能用新恋情、奖项或空泛道理偷换已建立的遗憾、关系或悬念，也不机械要求用完能力次数。",
    "scope": "只判断正文与作者合同，不用大纲替正文补证据；内部编辑通过不代表读者留存、平台推荐或收益已验证。",
    # guided_review supplies the principles and selected source cards once.
    "continuous_pull": {"protocol": ENGAGEMENT_GUIDANCE["protocol"],
                        "guidance_location": "research_context.continuous_pull"},
    "narrative_setup": {"protocol": narrative_setup.PROTOCOL, "requirements": narrative_setup.REQUIREMENTS},
}


def review_rubric_hash() -> str:
    return digest({"rubric": REVIEW_RUBRIC, "dimensions": DIMENSIONS,
                   "critical_dimensions": CRITICAL, "research_guidance_hash": guidance_fingerprint(),
                   "review_instructions": [REVIEW_INSTRUCTION, CONFIRM_INSTRUCTION, narrative_setup.INSTRUCTION,
                                           whole_story.INSTRUCTION, whole_story.CHALLENGE_INSTRUCTION,
                                           whole_story.CRITERIA, whole_story.PRINCIPLES],
                   "reader_reward_protocol": [reader_rewards.PROTOCOL, reader_rewards.GUIDANCE],
                   "prose_integrity_protocol": prose_integrity.PROTOCOL,
                   "reader_experience_protocol": [reader_experience.PROTOCOL, reader_experience.INSTRUCTION],
                   "reader_basis_protocol": [reader_experience.GROUNDING_PROTOCOL,
                                             reader_experience.GROUNDING_INSTRUCTION],
                   "lead_protocol": [lead.PROTOCOL, lead.CHECKS],
                   "chief_editor_protocol": chief_editor.criteria_hash(),
                   "protocol": "grounded-full-review-v10-original-reader-experience"})


def parse_json_response(response: str) -> dict:
    response = response.strip()
    if response.startswith("```"):
        response = re.sub(r"^```(?:json)?\s*", "", response, count=1)
        response = re.sub(r"\s*```$", "", response, count=1)
    try:
        value = json.loads(response)
    except (json.JSONDecodeError, TypeError) as exc:
        raise StoryError(f"模型没有返回有效 JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise StoryError("模型 JSON 顶层必须是对象")
    return value


def validate_issue(issue: dict, body: str) -> dict:
    if not isinstance(issue, dict):
        raise StoryError("问题记录不是对象")
    quote, span = issue.get("quote"), issue.get("span")
    if not isinstance(quote, str) or not quote:
        raise StoryError("评审问题必须引用正文")
    if not isinstance(span, list) or len(span) != 2 or not all(isinstance(x, int) for x in span):
        raise StoryError("问题 span 必须是两个整数")
    start, end = span
    if start < 0 or end <= start or end > len(body) or body[start:end] != quote:
        raise StoryError("评审引用与正文区间不匹配")
    severity = issue.get("severity")
    if severity not in ("blocker", "warning", "note"):
        raise StoryError("问题 severity 无效")
    issue.setdefault("id", str(uuid4()))
    return editorial_priority.normalize(issue)


def paragraph_spans(body: str) -> list[list[int]]:
    """Number non-empty prose lines with exact source offsets."""
    return [[match.start(), match.end()] for match in re.finditer(r"[^\n]+", body) if match.group().strip()]


def review_spans(body: str, target_chars: int = 4500) -> list[list[int]]:
    """Partition at prose-line boundaries without manufacturing broken sentences.

    target_chars is a soft limit: a single long paragraph remains intact. The
    caller's existing context guard still rejects oversized model prompts.
    """
    if target_chars <= 0:
        raise ValueError("target_chars must be positive")
    spans, start, end = [], 0, 0
    for line in body.splitlines(keepends=True):
        if end > start and end + len(line) - start > target_chars and body[start:end].strip():
            spans.append([start, end])
            start = end
        end += len(line)
    if end > start:
        if spans and not body[start:end].strip():
            spans[-1][1] = end
        else:
            spans.append([start, end])
    return spans


def ground_issue(issue: dict, body: str, *, offset: int = 0) -> dict:
    """Prefer exact quotes; otherwise anchor a numbered paragraph in the supplied text."""
    quote = issue.get("quote")
    if quote is not None and quote != "":
        if not isinstance(quote, str) or quote not in body:
            raise StoryError('评审提供的引文不在正文，不能用另一段替代')
        index = issue.get('paragraph_index')
        if index is not None:
            spans = paragraph_spans(body)
            if type(index) is not int or not 0 <= index < len(spans):
                raise StoryError('评审段落编号无效')
            a, b = spans[index]
            if quote not in body[a:b]:
                raise StoryError('评审引文与指定段落不一致')
            start = a + body[a:b].index(quote)
        else:
            if body.count(quote) != 1:
                raise StoryError('评审引文重复出现，必须提供段落编号')
            start = body.index(quote)
    else:
        index = issue.get("paragraph_index")
        spans = paragraph_spans(body)
        if type(index) is not int or index < 0 or index >= len(spans):
            raise StoryError("引文不在正文中，且段落编号无效")
        start, end = spans[index]
        quote = body[start:end]
    issue["quote"] = quote
    issue["span"] = [offset + start, offset + start + len(quote)]
    return issue


def length_status(body: str, contract: dict) -> dict:
    count = visible_count(body)
    length = contract["length"]
    return {"unit": "visible_char_v1", "value": count, "min": length["min"],
            "target": length["target"], "max": length["max"],
            "within_range": count >= length["min"] and
            (length["max"] is None or count <= length["max"])}


def fact_gate(body: str, contract: dict, checks: list[dict] | None = None) -> list[str]:
    """Enforce literal anchors and grounded verdicts for author-supplied facts."""
    facts = contract.get("story_facts") or []
    checks = checks or []
    if not facts:
        if checks:
            raise StoryError("审查报告包含合同之外的故事事实")
        return []
    if not isinstance(checks, list) or len(checks) != len(facts):
        raise StoryError("故事事实审查不完整")
    by_id = {item.get("fact_id"): item for item in checks if isinstance(item, dict)}
    if len(by_id) != len(facts) or set(by_id) != {fact["id"] for fact in facts}:
        raise StoryError("故事事实审查 ID 缺失或重复")
    blockers = []
    for fact in facts:
        fact_id = fact["id"]
        check = by_id[fact_id]
        verdict = check.get("verdict")
        quote = check.get("quote")
        if verdict not in ("supported", "contradicted", "unclear"):
            raise StoryError(f"故事事实判断无效: {fact_id}")
        if not isinstance(quote, str) or (quote and quote not in body):
            raise StoryError(f"故事事实引用不在正文中: {fact_id}")
        if verdict != "unclear" and not quote:
            raise StoryError(f"故事事实判断缺少正文证据: {fact_id}")
        if not isinstance(check.get("reason"), str) or not check["reason"].strip():
            raise StoryError(f"故事事实判断缺少原因: {fact_id}")
        if verdict == "contradicted" or (fact.get("critical", True) and verdict == "unclear"):
            blockers.append(f"fact:{fact_id}:{verdict}")
        for term in fact.get("required_text", []):
            if term not in body:
                blockers.append(f"fact:{fact_id}:missing:{term}")
        for term in fact.get("forbidden_text", []):
            if term in body:
                blockers.append(f"fact:{fact_id}:forbidden:{term}")
    return blockers


def review_decision(body: str, contract: dict, report: dict) -> dict:
    """Derive pass/fail; never trust a model-supplied overall score."""
    if report.get("content_hash") != digest(body) or report.get("contract_hash") != digest(contract):
        raise StoryError("审查报告与当前正文或写作合同版本不匹配")
    if report.get("review_rubric_hash") != review_rubric_hash():
        raise StoryError("审查标准已更改或报告缺少标准版本，请重新审读当前正文")
    if report.get('review_mode') == 'chief_editor':
        return _chief_decision(body, contract, report)
    if report.get('review_stage') == 'reader_experience_preflight':
        if not reader_experience.enabled(contract):
            raise StoryError('当前体裁不使用读者体验前置审读')
        blockers = reader_experience.gate(body, contract, report.get('reader_experience'))
        if reader_experience.grounding_gate(body, contract, report.get('reader_experience'),
                                           report.get('reader_grounding')):
            raise StoryError('读者体验前置否决尚缺可信审读依据，不能据此修改正文')
        coverage = report.get('review_coverage') or {}
        expected_spans = [window['span'] for window in reader_experience.windows(body)]
        if (coverage.get('complete') is not False or coverage.get('spans') != expected_spans
                or not blockers):
            raise StoryError('读者体验前置报告只能保留片段否决，不能代替全文通过')
        expected_issues = reader_experience.issues(report['reader_experience'])
        issues = [validate_issue(item, body) for item in report.get('issues', [])]
        if [{key: value for key, value in issue.items() if key != 'id'} for issue in issues] != expected_issues:
            raise StoryError('读者体验前置问题与独立否决证据不一致')
        return {'decision': 'revise_required', 'blockers': blockers, 'length': length_status(body, contract)}
    coverage = report.get("review_coverage") or {}
    spans = coverage.get("spans") or []
    if not coverage.get("complete") or not spans:
        raise StoryError("审查未覆盖全文")
    cursor = 0
    for span in sorted(spans):
        if not isinstance(span, list) or len(span) != 2 or span[0] > cursor or span[1] <= span[0]:
            raise StoryError("审查覆盖区间存在缺口或无效")
        cursor = max(cursor, span[1])
    if cursor < len(body):
        raise StoryError("审查没有覆盖正文结尾")
    dims = report.get("dimensions") or {}
    for key in DIMENSIONS:
        value = dims.get(key)
        if not isinstance(value, int) or value < 0 or value > 4:
            raise StoryError(f"审查维度缺失或无效: {key}")
        evidence = (report.get("dimension_evidence") or {}).get(key)
        if not isinstance(evidence, str) or not evidence.strip():
            raise StoryError(f"审查维度缺少具体依据: {key}")
    issues = [validate_issue(x, body) for x in report.get("issues", [])]
    blockers = [x["id"] for x in issues if x["severity"] == "blocker" and not editorial_priority.is_detail(x)]
    blockers.extend('submission_guide:' + error for error in
        submission_guide.submission_guide_review_errors(report, body, contract))
    # Several unresolved factual inconsistencies require revision even if the
    # model simultaneously awards high dimension scores. Exact duplicates count once.
    coherence_warnings = {(x["quote"], x.get("reason", "")) for x in issues
                          if x["severity"] == "warning" and x.get("category") == "coherence"
                          and not editorial_priority.is_detail(x)}
    if len(coherence_warnings) >= 2:
        blockers.append("coherence_warning_cluster")
    if contract.get("ending_mode") == "closed":
        payoff_warnings = [x for x in issues if x["severity"] == "warning" and
                           x.get("category") == "payoff" and not editorial_priority.is_detail(x)]
        if len(payoff_warnings) >= 2:
            blockers.append("payoff_warning_cluster")
    integrity_errors = report.get("review_integrity_errors") or []
    if not isinstance(integrity_errors, list):
        raise StoryError("审查完整性记录无效")
    blockers.extend(f"review_integrity:{index}" for index, _ in enumerate(integrity_errors, 1))
    blockers.extend(fact_gate(body, contract, report.get("fact_checks")))
    blockers.extend(f"dimension:{k}" for k in CRITICAL if dims[k] < 2)
    blockers.extend(market_gate(body, contract, report.get("market_checks")))
    blockers.extend(reader_rewards.gate(body, contract, report.get('reader_reward_review')))
    blockers.extend(reader_experience.gate(body, contract, report.get('reader_experience')))
    if reader_experience.enabled(contract) and reader_experience.grounding_gate(
            body, contract, report.get('reader_experience'), report.get('reader_grounding')):
        raise StoryError('读者体验审读依据未通过核验，不能确认正文通过')
    blockers.extend(lead.gate(body, contract, report.get('lead_review')))
    if prose_integrity.enabled(contract):
        blockers.extend(prose_integrity.gate(body, report.get('resource_audit'),
                                             report.get('obligation_audit')))
    if engagement_enabled(contract) and not integrity_errors:
        blockers.extend(engagement_gate(body, report))
    if not integrity_errors:
        blockers.extend(narrative_setup.gate(body, contract, report))
        if whole_story.enabled(contract):
            if not isinstance((report.get('whole_story') or {}).get('independent_challenge'), dict):
                raise StoryError('短故事终审缺少独立结构质疑记录')
            blockers.extend(whole_story.gate(body, report.get('whole_story')))
    if contract.get("market_contract") and dims["channel_fit"] < 2:
        blockers.append("dimension:channel_fit")
    length = length_status(body, contract)
    if not length["within_range"]:
        blockers.append("length_contract")
    return {"decision": "passed" if not blockers else "revise_required",
            "blockers": blockers, "length": length}


def _planned_reader_promise(project: StoryProject, contract: dict | None = None) -> dict | None:
    original = target_reader.entry_context(
        contract if contract is not None else project.brief().effective_contract(),
        include_author_promise=True)
    plan = read_json(project.root / 'plan' / 'scenes.json')
    if not isinstance(plan, dict) or not isinstance(plan.get('main_question'), str) or not plan['main_question'].strip():
        if not original['enabled']:
            return None
        return {'original_entry_context': original,
                'authority': '没有可用方案，仍须对照原始受众与实际标题导语的入场吸引'}
    return {'main_question': plan['main_question'], 'ending_resolution': plan.get('ending_resolution'),
        'original_entry_context': original,
        'source_hash': digest(plan),
        'authority': '原始受众与实际标题导语建立的入场吸引优先；main_question和ending_resolution仅是方案解释，不能替换原始承诺，也不得当作正文实际事实'}


def _chief_decision(body: str, contract: dict, report: dict) -> dict:
    """Reconstruct actual reading and evidence instead of accepting a ready flag."""
    chief = report.get('chief_editor')
    if (not isinstance(chief, dict) or chief.get('protocol') != chief_editor.PROTOCOL
            or chief.get('criteria_hash') != chief_editor.criteria_hash()
            or chief.get('record_hash') != digest({k: v for k, v in chief.items() if k != 'record_hash'})):
        raise StoryError('主审记录、证据或协议绑定损坏')
    binding = chief.get('binding') or {}
    expected = {'artifact_id': report.get('artifact_id'), 'revision': report.get('revision'),
        'content_hash': digest(body), 'contract_hash': digest(contract),
        'review_rubric_hash': review_rubric_hash(), 'research_hash': guidance_fingerprint(),
        'source_cards_hash': digest(chief.get('source_cards')),
        'editorial_feedback_hash': digest(chief.get('editorial_feedback')),
        'planned_reader_promise_hash': digest(chief.get('planned_reader_promise')),
        'max_prompt_chars': binding.get('max_prompt_chars')}
    if binding != expected:
        raise StoryError('主审输入、合同、研究或版本不匹配')
    reviewer_id = 'chief-' + digest({'project_id': contract.get('project_id'),
                                    'protocol': chief_editor.PROTOCOL})[:24]
    if chief.get('reviewer_id') != reviewer_id:
        raise StoryError('主审身份未绑定本次阅读')
    plan = chief_editor.build_plan(body, contract, max_prompt_chars=binding['max_prompt_chars'],
        source_cards=chief['source_cards'], editorial_feedback=chief['editorial_feedback'], reviewer_id=reviewer_id,
        planned_reader_promise=chief.get('planned_reader_promise'))
    if (chief.get('source_sha256') != plan['source_sha256']
            or chief.get('paragraph_plan_hash') != plan['paragraph_plan_hash']):
        raise StoryError('主审原文或段落计划已改变')
    coverage = report.get('review_coverage') or {}
    receipts = chief.get('chunks')
    if (coverage.get('complete') is not True or coverage.get('spans') != [c['span'] for c in plan['chunks']]
            or not isinstance(receipts, list) or len(receipts) != len(plan['chunks'])):
        raise StoryError('主审未覆盖全文；分段回执缺失')

    def checked_receipt(receipt, task, prompt, allowed, *, facts=False, audit_plan=None, ledger_plan=None,
                        first_read_plan=None):
        if (not isinstance(receipt, dict)
                or receipt.get('input_hash') != digest({'binding': binding, 'task': task, 'prompt': prompt})
                or receipt.get('response_hash') != digest(receipt.get('response'))):
            raise StoryError('主审回执与实际输入或响应不匹配')
        normalized = (chief_first_read.validate_response(receipt['response'], body, first_read_plan=first_read_plan)
            if first_read_plan is not None else expectation_ledger.validate_response(receipt['response'], body, ledger_plan=ledger_plan)
            if ledger_plan is not None else claim_audit.validate_reread_response(receipt['response'], body, audit_plan)
            if audit_plan is not None else chief_editor.validate_response(
                receipt['response'], body, allowed_ids=allowed, require_facts=facts))
        if receipt.get('format_normalizations', []) != normalized.get('format_normalizations', []):
            raise StoryError('主审格式规范化记录与原始响应不匹配')
        return normalized, receipt

    def replay_first_read(task, prompt, allowed, *, first_read_plan):
        matches = [item for item in chief.get('first_read_receipts', [])
                   if isinstance(item, dict) and item.get('plan_hash') == first_read_plan['plan_hash']]
        if len(matches) != 1:
            raise StoryError('初读实际分段回执缺失或重复')
        receipt = {key: value for key, value in matches[0].items()
                   if key not in {'plan_hash', 'paragraph_ids', 'parent_receipt_hash'}}
        return checked_receipt(receipt, task, prompt, allowed, first_read_plan=first_read_plan)

    first_read_record = chief_first_read.run_first_read(body, contract, stage=replay_first_read,
        reviewer_id=reviewer_id, max_prompt_chars=binding['max_prompt_chars'])
    if any(chief.get(key) != value for key, value in first_read_record.items()):
        raise StoryError('匿名初读观察、全文覆盖或回执链不匹配')
    results = []
    for chunk, receipt in zip(plan['chunks'], receipts):
        if receipt.get('chunk_id') != chunk['id'] or receipt.get('span') != chunk['span']:
            raise StoryError('主审回执覆盖范围不匹配')
        normalized, _ = checked_receipt(receipt, 'chief_editor_review', chunk['prompt'], chunk['paragraph_ids'],
                                       facts=plan['requires_synthesis'])
        results.append(normalized)
    def replay(task, prompt, allowed, *, facts=False, audit_plan=None, ledger_plan=None):
        if ledger_plan is not None:
            matches = [item for item in chief.get('ledger_receipts', [])
                       if isinstance(item, dict) and item.get('plan_hash') == ledger_plan['plan_hash']]
            if len(matches) != 1:
                raise StoryError('期待账本实际分段回执缺失或重复')
            # run_ledger owns the derived plan/range fields; replay the exact
            # stage receipt so its parent hash cannot include new metadata.
            receipt = {key: value for key, value in matches[0].items()
                       if key not in {'plan_hash', 'paragraph_ids', 'parent_receipt_hash'}}
        else:
            receipt = chief.get('claim_reread') if task.endswith('_claim_reread') else chief.get('synthesis')
        return checked_receipt(receipt, task, prompt, allowed, facts=facts,
                               audit_plan=audit_plan, ledger_plan=ledger_plan)
    final, continuation = chief_review.finish_review(body, contract, results, stage=replay,
        source_cards=chief['source_cards'], editorial_feedback=chief['editorial_feedback'],
        reviewer_id=reviewer_id, max_prompt_chars=binding['max_prompt_chars'],
        planned_reader_promise=chief.get('planned_reader_promise'), first_read=first_read_record['first_read'])
    if any(chief.get(key) != value for key, value in continuation.items()):
        raise StoryError('主审定向回读、核验或汇总记录不匹配')
    if coverage.get('narrative_complete') is not final['narrative_gate']['coverage_complete']:
        raise StoryError('主审叙事覆盖标记与实际原段核验不匹配')
    expected_issues = chief_editor.legacy_issues(final, body)
    if (chief.get('response') != final or report.get('issues') != expected_issues
            or report.get('initial_copyedit_reminders', []) != final.get('initial_copyedit_reminders', [])):
        raise StoryError('主审结论或修改意见与原始回执不一致')
    if report.get('review_integrity_errors') != []:
        raise StoryError('主审仍有未确认的证据或格式问题')
    blockers = [issue['id'] for issue in expected_issues if issue['severity'] == 'blocker']
    blockers.extend('submission_guide:' + error for error in
        submission_guide.submission_guide_review_errors(report, body, contract))
    if final['recommendation'] != 'ready':
        blockers.append('chief_editor:' + final['recommendation'])
    # Literal author anchors are computable without another semantic reviewer.
    for fact in contract.get('story_facts') or []:
        for term in fact.get('required_text', []):
            if term not in body:
                blockers.append(f"fact:{fact['id']}:missing:{term}")
        for term in fact.get('forbidden_text', []):
            if term in body:
                blockers.append(f"fact:{fact['id']}:forbidden:{term}")
    if lead.enabled(contract):
        try:
            lead.split(body)
        except StoryError:
            blockers.append('chief_editor:lead_structure')
    length = length_status(body, contract)
    if not length['within_range']:
        blockers.append('length_contract')
    return {'decision': 'passed' if not blockers else 'revise_required', 'blockers': blockers, 'length': length}


class StoryReviewer:
    def __init__(self, project: StoryProject, call_json, *, max_prompt_chars: int | None = None):
        self.project = project
        owner = getattr(call_json, '__self__', None)
        provider_limit = getattr(getattr(owner, 'llm', None), 'max_prompt_tokens', None)
        self.max_prompt_chars = (max_prompt_chars if max_prompt_chars is not None else
            int(provider_limit * .7) - 256 if isinstance(provider_limit, (int, float)) and provider_limit > 0
            else chief_editor.DEFAULT_MAX_PROMPT_CHARS)
        self._stage_cache_dir = None
        def guided_review(task, prompt):
            full_prompt = self._guided_prompt(task, prompt)
            path = self._stage_cache_path(task, full_prompt)
            cached = read_json(path) if path else None
            if (isinstance(cached, dict) and cached.get('task') == task
                    and cached.get('prompt_hash') == digest(full_prompt)
                    and isinstance(cached.get('response'), dict)
                    and cached.get('response_hash') == digest(cached['response'])):
                self.project.record_run({'event': 'review_stage_reused', 'task': task,
                                         'prompt_hash': cached['prompt_hash']})
                return deepcopy(cached['response'])
            response = call_json(task, full_prompt)
            if path and isinstance(response, dict):
                write_json(path, {'task': task, 'prompt_hash': digest(full_prompt),
                                  'response_hash': digest(response), 'response': response})
            return response
        self.call_json = guided_review

    def _guided_prompt(self, task, prompt):
        if task in {'chief_editor_first_read', 'chief_editor_review', 'chief_editor_synthesis', 'chief_editor_claim_reread',
                    'chief_editor_expectation_ledger'}:
            # First reading must stay cold. Later chief phases already carry
            # scoped guidance; injecting another wrapper would break replay.
            return prompt
        contract = self.project.contract()
        guide_context = submission_guide.submission_guide_context(contract, 'review')
        if guide_context['enabled']:
            prompt = (submission_guide.submission_guide_guidance(contract, 'review')
                + '\n本次适用投稿指南记录：\n' + json.dumps(guide_context, ensure_ascii=False)
                + '\n' + prompt)
        if task in {'reader_experience_review', 'reader_basis_grounding', 'reader_basis_repair'}:
            # Research, author defenses and earlier scores compromise a cold read.
            return prompt
        context = research_context(contract, task=task)
        feedback = read_json(self.project.root / 'editorial_feedback.json')
        if isinstance(feedback, dict) and feedback.get('decision') == 'reject':
            prompt += ('\n具体退稿问题须核对当前正文是否真正改变，不以旧稿通过关闭；'
                       '保留能反驳意见的真实证据，不为了格式强行给正面结论：\n'
                       + json.dumps(feedback, ensure_ascii=False))
        if task == 'manuscript_lead_review':
            # lead.review_prompt already carries the grounded lead examples.
            return prompt
        return (prompt + "\n按作者合同选择以下研究经验，仅评价给出的正文证据，不预测推流：\n"
                + json.dumps(model_research_context(context), ensure_ascii=False))

    def _stage_cache_path(self, task, full_prompt):
        if self._stage_cache_dir is None:
            return None
        return self._stage_cache_dir / f"{digest({'task': task, 'prompt': full_prompt})}.json"

    def _discard_invalid_stage(self, task, prompt):
        path = self._stage_cache_path(task, self._guided_prompt(task, prompt))
        if path:
            path.unlink(missing_ok=True)

    def _checked_response(self, task, prompt, validator):
        """Reassess one completed malformed response; transport failures propagate."""
        for attempt in range(2):
            response = self.call_json(task, prompt)
            try:
                validator(response)
                return response
            except StoryError as exc:
                self._discard_invalid_stage(task, prompt)
                path = self.project.root / "invalid_responses" / f"{uuid4()}.json"
                write_json(path, {"task": task, "response": response, "error": str(exc),
                                  "attempt": attempt + 1, "created_at": now()})
                self.project.record_run({"event": "review_schema_error", "task": task,
                    "error": str(exc), "exhausted": attempt == 1, "evidence": str(path.relative_to(self.project.root))})
                if attempt == 1:
                    return response  # Normal review records the error and refuses approval.
                prompt += ("\n上次已完整返回，但字段或证据校验失败：" + str(exc) +
                           "。重新阅读并返回完整JSON。选择输入明确列出的编号，不自己数段落，"
                           "不要为了通过格式检查强行给正面结论。")
                if task == 'reader_reward_review':
                    prompt += ('每条quotes请缩短为正文中逐字连续出现的10至25字原文片段；'
                               '不要跨段拼接，不要凭记忆改写标点、空行或引号。'
                               '若找不到证据，应如实给出missing或unclear并使用空quotes。')

    def _grounded_reader(self, body, contract):
        """Repair the model's grounds before acting on a prose pass or veto."""
        task = 'reader_experience_review'
        prompt = reader_experience.prompt(body, contract)
        for attempt in range(2):
            response = self._checked_response(task, prompt,
                lambda value: reader_experience.validate(value, body, contract))
            reader_review = reader_experience.validate(response, body, contract)
            basis_prompt = reader_experience.grounding_prompt(body, contract, reader_review)
            basis_response = self._checked_response('reader_basis_grounding', basis_prompt,
                lambda value: reader_experience.validate_grounding(value, body, contract, reader_review))
            basis = reader_experience.validate_grounding(basis_response, body, contract, reader_review)
            blockers = reader_experience.grounding_gate(body, contract, reader_review, basis)
            path = self.project.root / 'reader_basis' / f"{digest({'reader': reader_review, 'basis': basis})}.json"
            write_json(path, {'reader_experience': reader_review, 'reader_grounding': basis,
                             'basis_blockers': blockers, 'attempt': attempt + 1})
            if not blockers:
                return reader_review, basis
            self.project.record_run({'event': 'reader_basis_unconfirmed', 'blockers': blockers,
                                    'evidence': str(path.relative_to(self.project.root)),
                                    'prose_unchanged': True})
            if attempt == 0:
                task = 'reader_basis_repair'
                prompt = reader_experience.basis_repair_prompt(body, contract, reader_review, basis)
        raise StoryError('匿名审读的依据核验仍未通过；正文已保留，请重审依据后再判断是否改稿。证据：' + str(path))

    def review(self, mode: str | None = None) -> dict:
        mode = mode or self.project.contract().get('review_mode', 'chief_editor')
        if mode == 'deep_review':
            return self._review_legacy()
        if mode != 'chief_editor':
            raise StoryError('未知审稿模式；请选择chief_editor或deep_review')
        return self._review_chief()

    def review_affected(self, previous_report: dict, edits, *, previous_body: str | None = None,
                        new_body: str | None = None, edited_body: str | None = None) -> dict:
        """Keep source-bound edits; conservatively read all until dependency reuse is proved.

        The explicit fallback avoids re-binding old full-text approval to new prose.
        This entry point is ready for a later dependency-scoped reading plan.
        """
        _, active = self.project.active_draft()
        target = new_body if new_body is not None else edited_body
        verified = False
        if (isinstance(previous_body, str) and isinstance(target, str) and target == active
                and isinstance(previous_report, dict) and previous_report.get('content_hash') == digest(previous_body)):
            from .workflow import apply_edits
            changes = edits.get('edits') if isinstance(edits, dict) else edits
            if isinstance(changes, list):
                try:
                    verified = apply_edits(previous_body, changes, []) == target
                    if isinstance(edits, dict):
                        verified = verified and edits.get('source_content_hash') == digest(previous_body)
                        verified = verified and edits.get('target_content_hash') == digest(target)
                except (StoryError, TypeError, AttributeError):
                    pass
        self.project.record_run({'event': 'chief_incremental_fallback', 'content_hash': digest(active),
            'edit_mapping_verified': verified,
            'reason': 'cross-paragraph dependencies require full reading; previous approval was not rebound'})
        return self.review(mode='chief_editor')

    def _review_chief(self) -> dict:
        record, body = self.project.active_draft()
        contract = self.project.contract()
        feedback = read_json(self.project.root / 'editorial_feedback.json')
        planned_reader_promise = _planned_reader_promise(self.project, contract)
        selected = model_research_context(research_context(contract, task='chief_editor_review'))
        cards = chief_editor.compact_source_cards(selected, budget=min(6000, self.max_prompt_chars // 5))
        binding = {'artifact_id': record['artifact_id'], 'revision': record['revision'],
            'content_hash': digest(body), 'contract_hash': digest(contract),
            'review_rubric_hash': review_rubric_hash(), 'research_hash': guidance_fingerprint(),
            'source_cards_hash': digest(cards), 'editorial_feedback_hash': digest(feedback),
            'planned_reader_promise_hash': digest(planned_reader_promise),
            'max_prompt_chars': self.max_prompt_chars}
        reviewer_id = 'chief-' + digest({'project_id': contract.get('project_id'),
                                        'protocol': chief_editor.PROTOCOL})[:24]
        plan = chief_editor.build_plan(body, contract, max_prompt_chars=self.max_prompt_chars,
            source_cards=cards, editorial_feedback=feedback, reviewer_id=reviewer_id,
            planned_reader_promise=planned_reader_promise)
        folder = self.project.root / 'review_resume' / ('chief-' + digest(binding))
        write_json(folder / 'plan.json', {'binding': binding, 'plan': plan})
        self.project.update_state(record, 'reviewing')

        def stage(task, prompt, allowed_ids, *, facts=False, audit_plan=None, ledger_plan=None,
                  first_read_plan=None):
            if len(prompt) > self.max_prompt_chars:
                raise StoryError('主审核验输入超过容量；当前稿与已完成回执已保留')
            input_hash = digest({'binding': binding, 'task': task, 'prompt': prompt})
            path = folder / f'{input_hash}.json'
            cached = read_json(path)
            if cached is not None:
                if (not isinstance(cached, dict) or cached.get('input_hash') != input_hash
                        or cached.get('response_hash') != digest(cached.get('response'))):
                    raise StoryError('主审缓存绑定损坏；不能复用')
                response = cached['response']
                self.project.record_run({'event': 'review_stage_reused', 'task': task, 'prompt_hash': digest(prompt)})
            else:
                response = self.call_json(task, prompt)
                # Save completed output before validation: an invalid schema is
                # evidence, not a reason to silently dispatch another editor.
                cached = {'input_hash': input_hash, 'response_hash': digest(response), 'response': response}
                write_json(path, cached)
            try:
                normalized = (chief_first_read.validate_response(response, body, first_read_plan=first_read_plan)
                    if first_read_plan is not None else expectation_ledger.validate_response(response, body, ledger_plan=ledger_plan)
                    if ledger_plan is not None else claim_audit.validate_reread_response(response, body, audit_plan)
                    if audit_plan is not None else chief_editor.validate_response(
                        response, body, allowed_ids=allowed_ids, require_facts=facts))
            except StoryError as exc:
                self.project.record_run({'event': 'chief_schema_error', 'task': task,
                    'error': str(exc), 'evidence': str(path.relative_to(self.project.root)),
                    'automatic_semantic_retry': False})
                raise
            normalizations = normalized.get('format_normalizations', [])
            if 'format_normalizations' in cached and cached['format_normalizations'] != normalizations:
                raise StoryError('主审格式规范化缓存与原始响应不匹配')
            if normalizations and 'format_normalizations' not in cached:
                # Keep the completed model response and its hash unchanged.
                # This declares a mechanical ID split, never a new verdict.
                cached['format_normalizations'] = normalizations
                write_json(path, cached)
                self.project.record_run({'event': 'chief_format_normalized', 'task': task,
                    'input_hash': input_hash, 'response_hash': cached['response_hash'],
                    'evidence': str(path.relative_to(self.project.root)),
                    'format_normalizations': normalizations, 'automatic_semantic_retry': False})
            return normalized, cached

        try:
            first_read_record = chief_first_read.run_first_read(body, contract, stage=stage,
                reviewer_id=reviewer_id, max_prompt_chars=self.max_prompt_chars)
            write_json(folder / 'first_read.json', first_read_record)
            results, receipts = [], []
            for chunk in plan['chunks']:
                result, receipt = stage('chief_editor_review', chunk['prompt'], chunk['paragraph_ids'],
                                        facts=plan['requires_synthesis'])
                results.append(result)
                receipts.append({'chunk_id': chunk['id'], 'span': chunk['span'], **receipt})
                write_json(folder / 'checkpoint.json', {'binding': binding,
                    'completed_chunk_ids': [r['chunk_id'] for r in receipts], 'receipts_hash': digest(receipts)})
            final, continuation = chief_review.finish_review(body, contract, results, stage=stage,
                source_cards=cards, editorial_feedback=feedback, reviewer_id=reviewer_id,
                max_prompt_chars=self.max_prompt_chars, planned_reader_promise=planned_reader_promise,
                first_read=first_read_record['first_read'])
            chief = {'protocol': chief_editor.PROTOCOL, 'criteria_hash': chief_editor.criteria_hash(),
                'reviewer_id': reviewer_id, 'binding': binding,
                'source_sha256': plan['source_sha256'], 'paragraph_plan_hash': plan['paragraph_plan_hash'],
                'source_cards': cards, 'editorial_feedback': feedback,
                'planned_reader_promise': planned_reader_promise,
                'chunks': receipts, **first_read_record, **continuation, 'response': final}
            chief['record_hash'] = digest(chief)
            report = {'review_id': str(uuid4()), 'review_mode': 'chief_editor',
                'artifact_id': record['artifact_id'], 'revision': record['revision'],
                'content_hash': digest(body), 'contract_hash': digest(contract),
                'review_rubric_hash': review_rubric_hash(),
                'review_coverage': {'complete': True, 'spans': [c['span'] for c in plan['chunks']],
                    'narrative_complete': final['narrative_gate']['coverage_complete'],
                    'meaning': 'all prose delivered in validated reading turns; not measured reader performance'},
                'chief_editor': chief, 'issues': chief_editor.legacy_issues(final, body),
                'initial_copyedit_reminders': final.get('initial_copyedit_reminders', []),
                'submission_guide': {**submission_guide.submission_guide_context(contract, 'review'),
                                     'content_hash': digest(body)},
                'dimensions': {}, 'dimension_evidence': {}, 'fact_checks': [], 'market_checks': [],
                'review_integrity_errors': [], 'created_at': now()}
            report.update(review_decision(body, contract, report))
            write_json(self.project.root / 'reviews' / f"{report['review_id']}.json", report)
            self.project.update_state(record, report['decision'])
            self.project.record_run({'event': 'chief_review_completed', 'revision': record['revision'],
                'review_id': report['review_id'], 'reading_turns': len(receipts),
                'first_read_turns': len(first_read_record['first_read_receipts']),
                'synthesis_turns': int(continuation['synthesis'] is not None),
                'claim_reread_turns': int(continuation['claim_reread'] is not None),
                'claim_gate': final.get('claim_gate'), 'decision': report['decision']})
            if report['decision'] == 'passed':
                commit = {'commit_id': f"{record['artifact_id']}-{record['revision']}",
                    'artifact_id': record['artifact_id'], 'revision': record['revision'],
                    'content_hash': digest(body), 'review_id': report['review_id']}
                write_json(self.project.root / 'memory' / 'commits' / f"{commit['commit_id']}.json", commit)
            # Completed stage receipts remain reusable; they are bound to this
            # exact version, contract, criteria, research and feedback.
            return report
        except Exception as exc:
            self.project.update_state(record, 'review_error')
            self.project.record_run({'event': 'review_error', 'review_mode': 'chief_editor', 'error': str(exc)[:500]})
            raise

    def _review_legacy(self) -> dict:
        record, body = self.project.active_draft()
        contract = self.project.contract()
        lead_selection = None
        if lead.enabled(contract) and lead.CHAPTER.search(body) and body[:lead.CHAPTER.search(body).start()].strip():
            lead_selection = lead.validate_selection(body, contract,
                read_json(self.project.root / 'lead_selections' / f'{digest(body)}.json'))
            if lead_selection.get('editorial_feedback_hash') != digest(read_json(self.project.root / 'editorial_feedback.json')):
                raise StoryError('导语选稿未处理当前退稿意见；须重新比较候选')
        standard_hash = review_rubric_hash()
        self._stage_cache_dir = self.project.root / 'review_resume' / digest({
            'artifact_id': record['artifact_id'], 'revision': record['revision'],
            'content_hash': record['content_hash'], 'contract_hash': digest(contract),
            'review_rubric_hash': standard_hash})
        self.project.update_state(record, "reviewing")
        try:
            reader_review = None
            reader_grounding = None
            if reader_experience.enabled(contract):
                reader_review, reader_grounding = self._grounded_reader(body, contract)
                reader_blockers = reader_experience.gate(body, contract, reader_review)
                if reader_blockers:
                    report = {'review_id': str(uuid4()), 'artifact_id': record['artifact_id'],
                        'revision': record['revision'], 'content_hash': record['content_hash'],
                        'contract_hash': digest(contract), 'review_rubric_hash': standard_hash,
                        'review_stage': 'reader_experience_preflight',
                        'review_coverage': {'complete': False,
                            'spans': [window['span'] for window in reader_experience.windows(body)]},
                        'reader_experience': reader_review,
                        'reader_grounding': reader_grounding,
                        'issues': [validate_issue(item, body) for item in reader_experience.issues(reader_review)],
                        'dimensions': {}, 'dimension_evidence': {}, 'review_integrity_errors': [],
                        'created_at': now()}
                    report.update(review_decision(body, contract, report))
                    write_json(self.project.root / 'reviews' / f"{report['review_id']}.json", report)
                    self.project.update_state(record, report['decision'])
                    self.project.record_run({'event': 'reader_experience_veto',
                        'content_hash': record['content_hash'], 'review_id': report['review_id'],
                        'skipped_full_review': True, 'blockers': reader_blockers})
                    shutil.rmtree(self._stage_cache_dir, ignore_errors=True)
                    self._stage_cache_dir = None
                    return report
            spans = review_spans(body)
            local_issues = []
            local_notes = []
            integrity_errors = []
            progression_observations = []
            setup_checks = []
            setup_requirements = narrative_setup.requirements(contract)
            setup_paragraphs = narrative_setup.opening_paragraphs(body)
            for start, end in spans:
                segment = body[start:end]
                paragraphs = [segment[a:b] for a, b in paragraph_spans(segment)]
                progression_windows = engagement_windows(segment, offset=start) if engagement_enabled(contract) else []
                window_inputs = [{**window, "evidence_paragraphs": [
                    {"paragraph_index": index, "text": paragraphs[index]}
                    for index in range(window["paragraph_start"], window["paragraph_end"])]}
                    for window in progression_windows]
                def validate_cold(response):
                    if start == 0 and setup_requirements:
                        narrative_setup.validate_checks(response.get("narrative_checks"), setup_requirements, setup_paragraphs)
                    if engagement_enabled(contract):
                        validate_observations(response, progression_windows, segment)
                response = self._checked_response(
                    "cold_read_review",
                    "你是第一次看到这篇故事的目标读者编辑。只能根据给出的正文判断，不看大纲。"
                    "输入可能是全文中间的片段，中间片段不需要重新开头或结束故事；"
                    "不得把片段边界或缺少相邻段落当成正文缺失，仅报告片段内有证据的问题。"
                    "输出JSON对象：issues必须是数组，每项有paragraph_index（从0开始的段落编号）、"
                    "severity（只能是blocker/warning/note）、"
                    "category为opening_pull/causal_progress/character_specificity/payoff/economy/voice/coherence之一，"
                    "reason与fix_intent；reader_question、drop_point、want_to_continue。"
                    "不要自己誊写quote，由程序从paragraph_index提取原文。"
                    "示例JSON：{\"issues\":[{\"paragraph_index\":0,\"severity\":\"warning\","
                    "\"category\":\"opening_pull\",\"reason\":\"冲突太晚出现\","
                    "\"fix_intent\":\"提前行动\"}],\"reader_question\":\"主角会怎么做\","
                    "\"drop_point\":null,\"want_to_continue\":true}。\n"
                    + (REVIEW_INSTRUCTION if engagement_enabled(contract) else "")
                    + (narrative_setup.INSTRUCTION if start == 0 and setup_requirements else "")
                    + json.dumps({"audience": contract["audience"], "tone": contract["tone"],
                                  "original_entry_context": target_reader.entry_context(contract),
                                  "is_opening": start == 0, "is_ending": end == len(body),
                                  "review_rubric": REVIEW_RUBRIC,
                                  "previous_text": body[:start] if engagement_enabled(contract) else "",
                                  "progression_windows": window_inputs,
                                  "narrative_requirements": setup_requirements if start == 0 else [],
                                  "opening_paragraphs": setup_paragraphs if start == 0 else [],
                                  "paragraphs": paragraphs}, ensure_ascii=False), validate_cold,
                )
                if start == 0 and setup_requirements:
                    try:
                        setup_checks = narrative_setup.validate_checks(
                            response.get("narrative_checks"), setup_requirements, setup_paragraphs)
                    except StoryError as exc:
                        integrity_errors.append({"stage": "narrative_setup", "reason": str(exc)})
                if engagement_enabled(contract):
                    try:
                        progression_observations.extend(validate_observations(response, progression_windows, segment))
                    except StoryError as exc:
                        integrity_errors.append({"stage": "continuous_pull", "span": [start, end], "reason": str(exc)})
                raw_issues = response.get("issues", [])
                if not isinstance(raw_issues, list):
                    integrity_errors.append({"stage": "cold_read_review", "span": [start, end],
                                             "reason": "issues 不是数组"})
                    raw_issues = []
                for issue in raw_issues:
                    if not isinstance(issue, dict):
                        integrity_errors.append({"stage": "cold_read_review", "span": [start, end],
                                                 "reason": "问题记录不是对象"})
                        continue
                    try:
                        issue = ground_issue(issue, segment, offset=start)
                        local_issues.append(validate_issue(issue, body))
                    except StoryError as exc:
                        integrity_errors.append({"stage": "cold_read_review", "span": [start, end],
                                                 "reason": str(exc)})
                local_notes.append({"span": [start, end],
                                    "reader_question": response.get("reader_question"),
                                    "drop_point": response.get("drop_point"),
                                    "want_to_continue": response.get("want_to_continue")})

            global_response = self.call_json(
                "continuity_review",
                "你是完整故事编辑。阅读全部正文及作者合同。输出JSON对象：dimensions必须是对象，不能是数组；"
                "以以下八个维度名作为键，每个值为0-4整数。"
                "维度opening_pull,causal_progress,character_specificity,payoff,economy,coherence,voice,channel_fit；"
                "dimension_evidence也必须是对象，以同样八个维度名作为键，值为每项具体原因的字符串；"
                "issues数组中每项有paragraph_index（从0开始）、"
                "severity（只能是blocker/warning/note）、category、reason、fix_intent。"
                "不要誊写quote，由程序从paragraph_index提取原文。"
                "人物先说谎、后被证据揭穿属于有意叙事，不要当作作者事实矛盾；"
                "只有正文没有建立说谎动机或揭露证据时才报问题。"
                "内部总分不能替代具体维度。完整短篇结尾不要求下章悬念。\n"
                + (CONFIRM_INSTRUCTION if engagement_enabled(contract) else "")
                + json.dumps({"contract": contract,
                              "original_entry_context": target_reader.entry_context(contract, include_author_promise=True),
                              "review_rubric": REVIEW_RUBRIC,
                              "paragraphs": [body[a:b] for a, b in paragraph_spans(body)],
                              "progression_suspects": engagement_suspects(progression_observations),
                              "cold_read_notes": local_notes}, ensure_ascii=False),
            )
            progression_confirmations = []
            if engagement_enabled(contract):
                try:
                    progression_confirmations = validate_confirmations(global_response, progression_observations, body)
                except StoryError as exc:
                    integrity_errors.append({"stage": "continuous_pull_confirmation", "reason": str(exc)})
            fact_checks = []
            if contract.get("story_facts"):
                fact_response = self.call_json(
                    "story_fact_review",
                    "你是故事事实核对编辑。逐条核对作者事实，不得凭大纲代替正文。"
                    "输出JSON对象，checks数组必须每项对应一个fact_id，含verdict"
                    "（supported/contradicted/unclear）、quote和reason。"
                    "supported或contradicted必须引用正文连续原文；无法确认时verdict=unclear且quote可为空。"
                    "检查时间线、方位、人物知情范围、物件首次出现和选择代价。"
                    "不能因为某个词出现就认定复杂因果成立。\n"
                    + json.dumps({"facts": contract["story_facts"], "body": body}, ensure_ascii=False),
                )
                raw_checks = fact_response.get("checks")
                if not isinstance(raw_checks, list):
                    integrity_errors.append({"stage": "story_fact_review", "reason": "checks 不是数组"})
                    raw_checks = []
                fact_checks = []
                for fact in contract["story_facts"]:
                    matches = [item for item in raw_checks if isinstance(item, dict) and
                               item.get("fact_id") == fact["id"]]
                    if len(matches) != 1:
                        integrity_errors.append({"stage": "story_fact_review", "fact_id": fact["id"],
                                                 "reason": "事实判断缺失或重复"})
                        fact_checks.append({"fact_id": fact["id"], "verdict": "unclear",
                                            "quote": "", "reason": "审查结果缺失或重复"})
                    else:
                        fact_checks.append(matches[0])
                for check in fact_checks:
                    quote = check.get("quote")
                    if isinstance(quote, str) and quote and quote not in body:
                        integrity_errors.append({"stage": "story_fact_review",
                                                 "fact_id": check.get("fact_id"),
                                                 "reason": "引文不在正文内"})
                        check["verdict"] = "unclear"
                        check["quote"] = ""
                        check["reason"] = "原审查引文不在正文内，事实无法证实"
            issues = local_issues[:]
            for check in setup_checks:
                if check["verdict"] != "supported":
                    issues.append(validate_issue(ground_issue({
                        "paragraph_index": (check["evidence_paragraph_indices"] or [0])[0],
                        "severity": "blocker", "category": "causal_progress",
                        "origin": "narrative_setup", "reason": check["reason"],
                        "fix_intent": check["fix_intent"],
                    }, body), body))
            global_issues = global_response.get("issues", [])
            if not isinstance(global_issues, list):
                integrity_errors.append({"stage": "continuity_review", "reason": "issues 不是数组"})
                global_issues = []
            for issue in global_issues:
                if not isinstance(issue, dict):
                    integrity_errors.append({"stage": "continuity_review", "reason": "问题记录不是对象"})
                    continue
                try:
                    issue = ground_issue(issue, body)
                    issues.append(validate_issue(issue, body))
                except StoryError as exc:
                    integrity_errors.append({"stage": "continuity_review", "reason": str(exc)})
            for confirmation in progression_confirmations:
                if confirmation["verdict"] != "context_resolves":
                    issues.append(validate_issue({**confirmation, "severity": "blocker",
                        "category": "causal_progress", "origin": "continuous_pull"}, body))
            challenge_response = self.call_json(
                "causal_challenge",
                "请做严格因果漏洞审计，不评分、不夸奖。只找足以使核心谜底或结尾不成立的硬伤："
                "事实无法推出结论、物件机制自相矛盾、数字或时间错误、主问题没有回答。"
                "先区分人物台词、误解与叙述者确认的事实；有明确揭穿的谎言不是连续性错误。"
                "输出JSON对象，issues数组最多三项；每项含paragraph_index（从0开始）、"
                "severity（只能是blocker或warning）、reason、missing_link。"
                "普通省略的生活细节不要报；但低成本替代行动若直接消除核心困境，"
                "或主角实际行为更消耗同一稀缺资源，必须核对硬约束与动机，不得把虚假两难当生活细节。"
                "不要求人物做最优选择：有前文动机支撑的逃避或非理性行为可以成立。"
                "若确无硬伤返回空数组。"
                "不要誊写quote，由程序从段落编号提取原文。\n"
                + json.dumps({"premise": contract["author_input"]["premise"],
                              "reader_promise": contract["reader_promise"],
                              "causal_rubric": CAUSAL_RUBRIC,
                              "paragraphs": [body[a:b] for a, b in paragraph_spans(body)]}, ensure_ascii=False),
            )
            challenge_issues = challenge_response.get("issues", [])
            if not isinstance(challenge_issues, list) or len(challenge_issues) > 3:
                integrity_errors.append({"stage": "causal_challenge", "reason": "issues 结构或数量无效"})
                challenge_issues = []
            for issue in challenge_issues:
                if not isinstance(issue, dict):
                    integrity_errors.append({"stage": "causal_challenge", "reason": "问题记录不是对象"})
                    continue
                issue["category"] = "coherence"
                issue["fix_intent"] = issue.get("missing_link", "核对因果链")
                try:
                    issue = ground_issue(issue, body)
                    issues.append(validate_issue(issue, body))
                except StoryError as exc:
                    integrity_errors.append({"stage": "causal_challenge", "reason": str(exc)})
            raw_dimensions = global_response.get("dimensions")
            raw_evidence = global_response.get("dimension_evidence")
            raw_dimensions = raw_dimensions if isinstance(raw_dimensions, dict) else {}
            raw_evidence = raw_evidence if isinstance(raw_evidence, dict) else {}
            dimensions, dimension_evidence = {}, {}
            for key in DIMENSIONS:
                score = raw_dimensions.get(key)
                if type(score) is not int or not 0 <= score <= 4:
                    integrity_errors.append({"stage": "continuity_review", "reason": f"维度分数无效: {key}"})
                    score = 0
                evidence = raw_evidence.get(key)
                if not isinstance(evidence, str) or not evidence.strip():
                    integrity_errors.append({"stage": "continuity_review", "reason": f"维度依据无效: {key}"})
                    evidence = "模型未提供可核对的维度依据"
                dimensions[key] = score
                dimension_evidence[key] = evidence
            market_checks = []
            if contract.get("market_contract"):
                market_response = self.call_json("market_review", market_review_prompt(body, contract))
                write_json(self.project.root / "runs" / f"market-review-{uuid4()}.json", market_response)
                market_checks = market_response.get("checks")
                market_gate(body, contract, market_checks)  # Fail closed on malformed/missing evidence.
            structure = None
            reward_review = None
            resource_audit = None
            obligation_audit = None
            if reader_rewards.effective_spec(contract):
                reward_review = self._checked_response('reader_reward_review',
                    reader_rewards.prompt(body, contract),
                    lambda response: reader_rewards.validate_checks(body, contract, response))
                reader_rewards.validate_checks(body, contract, reward_review)
                issues.extend(validate_issue(item, body) for item in reader_rewards.issues(body, contract, reward_review))
            if prose_integrity.enabled(contract):
                resource_audit = self._checked_response('resource_audit',
                    prose_integrity.resource_prompt(body, contract),
                    lambda response: prose_integrity.validate_resource(body, response))
                prose_integrity.validate_resource(body, resource_audit)
                obligation_audit = self._checked_response('obligation_audit',
                    prose_integrity.obligation_prompt(body, contract),
                    lambda response: prose_integrity.validate_obligation(body, response))
                prose_integrity.validate_obligation(body, obligation_audit)
            if whole_story.enabled(contract):
                challenge_response = self._checked_response('whole_story_challenge',
                    whole_story.challenge_prompt(body, contract),
                    lambda response: whole_story.validate_challenge(response, body))
                challenge = whole_story.validate_challenge(challenge_response, body)
                structure_response = self._checked_response('whole_story_review',
                    whole_story.prompt(body, contract, challenge), lambda response: whole_story.validate(response, body))
                structure = whole_story.validate(structure_response, body)
                structure['independent_challenge'] = challenge
                write_json(self.project.root / 'runs' / f'whole-story-challenge-{uuid4()}.json', challenge)
                issues.extend(validate_issue(item, body) for item in whole_story.issues(structure))
            lead_review = None
            if lead.enabled(contract):
                try:
                    lead_text, story_text = lead.split(body)
                except StoryError:
                    lead_text = None
                    first_line = next((line for line in body.splitlines() if line.strip()), body)
                    start = body.index(first_line)
                    issues.append(validate_issue({'quote': first_line, 'span': [start, start + len(first_line)],
                        'severity': 'blocker', 'category': 'opening_pull', 'origin': 'manuscript_lead',
                        'reason': '作者合同要求独立导语与第一章，但当前正文没有完整结构。',
                        'fix_intent': '根据实际整篇正文补写独立导语、第一章分界和完整首场；简介不能代替导语。'}, body))
                if lead_text is not None:
                    lead_review = self._checked_response('manuscript_lead_review',
                        lead.review_prompt(story_text, contract, text=lead_text),
                        lambda response: lead.validate_checks(response, lead_text, story_text))
                    lead_review = {**lead_review, 'protocol': lead.PROTOCOL,
                                   'content_hash': digest(body), 'contract_hash': digest(contract),
                                   'selection': lead_selection}
                    for check in lead_review['checks']:
                        if check['verdict'] == 'revise':
                            quote = check['lead_quote']
                            start = body.index(quote)
                            issues.append(validate_issue({'quote': quote, 'span': [start, start + len(quote)],
                                'severity': 'blocker', 'category': 'opening_pull', 'origin': 'manuscript_lead',
                                'reason': check['reason'], 'fix_intent': check['revision']}, body))
            report = {
                "review_id": str(uuid4()), "artifact_id": record["artifact_id"],
                "revision": record["revision"], "content_hash": record["content_hash"],
                "contract_hash": digest(contract), "review_coverage": {"complete": True, "spans": spans},
                "review_rubric_hash": standard_hash,
                "submission_guide": {**submission_guide.submission_guide_context(contract, 'review'),
                                     'content_hash': digest(body)},
                "dimensions": dimensions,
                "dimension_evidence": dimension_evidence,
                "fact_checks": fact_checks, "market_checks": market_checks,
                "issues": issues, "review_integrity_errors": integrity_errors,
                "cold_read_notes": local_notes, "created_at": now(),
                "narrative_setup": {"protocol": narrative_setup.PROTOCOL,
                                    "checks": setup_checks},
                "whole_story": structure,
                "lead_review": lead_review,
                "reader_reward_review": reward_review,
                "resource_audit": resource_audit,
                "obligation_audit": obligation_audit,
                "reader_experience": reader_review,
                "reader_grounding": reader_grounding,
                "initial_copyedit_reminders": [item for item in issues if editorial_priority.is_detail(item)]
                    + (prose_integrity.initial_copyedit_reminders(body, resource_audit, obligation_audit)
                       if prose_integrity.enabled(contract) else []),
            }
            if engagement_enabled(contract):
                report["continuous_pull"] = {"protocol": ENGAGEMENT_PROTOCOL,
                    "scope": "model_editorial_observations_not_reader_retention",
                    "observation_target_visible_chars": 700,
                    "observations": progression_observations, "confirmations": progression_confirmations}
            decision = review_decision(body, contract, report)
            report.update(decision)
            write_json(self.project.root / "reviews" / f"{report['review_id']}.json", report)
            self.project.update_state(record, report["decision"])
            if report["decision"] == "passed":
                commit = {"commit_id": f"{record['artifact_id']}-{record['revision']}",
                          "artifact_id": record["artifact_id"], "revision": record["revision"],
                          "content_hash": record["content_hash"], "review_id": report["review_id"]}
                path = self.project.root / "memory" / "commits" / f"{commit['commit_id']}.json"
                write_json(path, commit)
            shutil.rmtree(self._stage_cache_dir, ignore_errors=True)
            self._stage_cache_dir = None
            return report
        except Exception as exc:
            self._stage_cache_dir = None
            self.project.update_state(record, "review_error")
            self.project.record_run({"event": "review_error", "error": str(exc)})
            raise


def current_review(project: StoryProject, *, require_passed: bool = True) -> dict:
    record, body = project.active_draft()
    veto_paths = [project.root / "independent_veto.json"]
    history = project.root / "independent_veto_history"
    if history.exists():
        veto_paths.extend(sorted(history.glob("*.json")))
    for path in veto_paths:
        independent_veto = read_json(path)
        if (independent_veto and independent_veto.get("content_hash") == digest(body)
                and independent_veto.get("decision") == "reject"):
            raise StoryError("当前稿件被绑定正文的独立复核否决；需修订并重新全文审读")
    reports = sorted((project.root / "reviews").glob("*.json")) if (project.root / "reviews").exists() else []
    for path in sorted(reports, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True):
        report = read_json(path)
        if report.get("artifact_id") != record["artifact_id"] or report.get("revision") != record["revision"]:
            continue
        if (report.get('review_mode') == 'chief_editor'
                and (report.get('chief_editor') or {}).get('binding', {}).get('editorial_feedback_hash')
                != digest(read_json(project.root / 'editorial_feedback.json'))):
            raise StoryError('主审报告尚未处理当前编辑反馈')
        if (report.get('review_mode') == 'chief_editor'
                and (report.get('chief_editor') or {}).get('binding', {}).get('planned_reader_promise_hash')
                != digest(_planned_reader_promise(project))):
            raise StoryError('主审报告尚未核对当前方案的读者承诺')
        derived = review_decision(body, project.brief().effective_contract(), report)
        if report.get("decision") != derived["decision"] or report.get("blockers") != derived["blockers"]:
            raise StoryError("审查报告结论与当前门禁计算不一致")
        if require_passed and derived["decision"] != "passed":
            raise StoryError("当前稿件尚未通过审查")
        return report
    raise StoryError("当前稿件没有有效审查报告")
