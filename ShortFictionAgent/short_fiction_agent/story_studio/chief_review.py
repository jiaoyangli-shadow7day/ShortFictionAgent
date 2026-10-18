"""Shared continuation for live chief reviews and immutable receipt replay.

The supplied stage owns transport, budgets and durable raw responses. This
module never starts a model client or retries a completed semantic judgment.
Publication packaging uses the same continuation with its existing task prefix.
"""
from __future__ import annotations

from . import chief_editor, claim_audit, narrative_audit, editorial_closure, expectation_ledger, chief_first_read, editorial_priority
from .contracts import StoryError


def finish_review(body, contract, reading_results, *, stage, source_cards=None,
                  editorial_feedback=None, reviewer_id='chief-editor',
                  max_prompt_chars=chief_editor.DEFAULT_MAX_PROMPT_CHARS,
                  task_prefix='chief_editor', planned_reader_promise=None, first_read=None):
    if not reading_results:
        raise StoryError('主审缺少实际读取记录')
    ledger_record = expectation_ledger.run_ledger(body, contract, stage=stage,
        reviewer_id=reviewer_id, max_prompt_chars=max_prompt_chars, task_prefix=task_prefix,
        planned_reader_promise=planned_reader_promise)
    current_ledger = ledger_record['current_ledger']
    ledger_objections = expectation_ledger.findings(current_ledger, body)
    plan = chief_editor.claim_reread_plan(body, contract, reading_results,
        source_cards=source_cards, editorial_feedback=editorial_feedback,
        reviewer_id=reviewer_id, max_prompt_chars=max_prompt_chars,
        planned_reader_promise=planned_reader_promise)
    checks, reread = None, None
    if plan['requires_reread']:
        if len(plan['prompt']) > max_prompt_chars:
            raise StoryError('主审定向回读输入超过容量；保留未核验状态')
        checks, reread = stage(task_prefix + '_claim_reread', plan['prompt'],
            plan['allowed_ids'], audit_plan=plan)
    synthesis = None
    if len(reading_results) > 1 or ledger_objections:
        prompt, allowed = chief_editor.synthesis_prompt(body, contract, reading_results,
            source_cards=source_cards, editorial_feedback=editorial_feedback,
            reviewer_id=reviewer_id, max_prompt_chars=max_prompt_chars,
            claim_audit={'plan': plan, 'checks': checks}, planned_reader_promise=planned_reader_promise,
            current_ledger=current_ledger)
        if len(prompt) > max_prompt_chars:
            raise StoryError('主审跨段证据超过汇总容量；正文与核验回执已保留，不能宣称通过')
        final, synthesis = stage(task_prefix + '_synthesis', prompt, allowed)
    else:
        final = reading_results[0]
    effective = claim_audit.final_gate(final, plan, checks)
    effective = narrative_audit.final_gate(effective, body, reading_results)
    effective = editorial_closure.final_gate(effective, body, reading_results, editorial_feedback)
    effective = expectation_ledger.final_gate(effective, body, current_ledger, contract=contract)
    if first_read is not None:
        # Apply last: a later fact, plan or source check cannot erase the
        # source-only reading. Default StoryReviewer also replays its receipts.
        effective = chief_first_read.final_gate(effective, body, contract, first_read)
    effective = editorial_priority.final_gate(effective, body)
    return effective, {'claim_plan_hash': plan['plan_hash'], 'claim_reread': reread,
                       'claim_checks': checks, 'synthesis': synthesis, **ledger_record}
